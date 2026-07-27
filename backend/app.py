from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from pathlib import Path
from datetime import datetime
import json, os, yaml, time, ipaddress, re, logging, traceback
from jose import jwt
from jose.exceptions import JWTError
from passlib.hash import bcrypt
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST

# -------- Logging / Debug --------
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("pdu-api")

SECRET_KEY = os.getenv("SECRET_KEY", "change_me")
API_AUDIENCE = "ibm-pdu-ui"
ALGO = "HS256"
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
PDUS_FILE = DATA_DIR / "pdus.json"
AUDIT_FILE = DATA_DIR / "audit.log"
MODELS_FILE = Path("models.yaml")
USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"
METRICS_DIR = DATA_DIR / "metrics"; METRICS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="IBM PDU Manager API", debug=DEBUG)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def log_startup_config():
    log.info("=== IBM PDU Manager API démarrée ===")
    log.info("DEBUG=%s | USE_MOCK=%s | SNMP_VERSION=%s | ALLOWED_ORIGINS=%s",
             DEBUG, USE_MOCK, os.getenv("SNMP_VERSION", "2c"), os.getenv("ALLOWED_ORIGINS", "*"))
    if SECRET_KEY == "change_me":
        log.warning("SECRET_KEY est la valeur par défaut 'change_me' — à changer en production !")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
    except Exception:
        log.error("Exception non gérée sur %s %s\n%s", request.method, request.url.path, traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur (voir logs API)"})
    dur_ms = (time.time() - start) * 1000
    line = f"{request.method} {request.url.path} -> {response.status_code} ({dur_ms:.0f} ms)"
    if response.status_code >= 500:
        log.error(line)
    elif response.status_code >= 400:
        log.warning(line)
    else:
        log.debug(line) if DEBUG else None
    return response

@app.get("/health")
def health():
    """Endpoint de santé, sans authentification, pour vérifier que l'API répond."""
    return {"status": "ok", "mock": USE_MOCK, "debug": DEBUG, "time": datetime.utcnow().isoformat() + "Z"}

def load_models() -> Dict[str, Any]:
    with open(MODELS_FILE, "r") as f:
        return yaml.safe_load(f)

MODELS = load_models()

# demo users (admin hashé: 'admin', reader plaintext: 'reader')
USERS = {
    "admin": {"password": "$2b$12$nM2hmhm1u.t1Rwy7LPVoNe2heTFBr2fBh.sQ3TW5wwBySmOEJ70Ya", "role": "operator"},
    "reader": {"password": "reader", "role": "viewer"}
}

def create_token(user: "User") -> str:
    payload = {"sub": user.username, "role": user.role, "aud": API_AUDIENCE, "iat": int(time.time())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

def get_user_from_token(token: str) -> "User":
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGO], audience=API_AUDIENCE)
        return User(username=payload["sub"], role=payload.get("role", "viewer"))
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide")

async def current_user(authorization: Optional[str] = Header(None)) -> "User":
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Auth requise")
    token = authorization.split(" ", 1)[1]
    return get_user_from_token(token)

class User(BaseModel):
    username: str
    role: str = "operator"

class TokenRequest(BaseModel):
    username: str
    password: str

# -------- Storage --------
def read_pdus() -> List[Dict[str, Any]]:
    if PDUS_FILE.exists():
        return json.loads(PDUS_FILE.read_text())
    return []

def write_pdus(pdus: List[Dict[str, Any]]):
    PDUS_FILE.write_text(json.dumps(pdus, indent=2))

def audit(line: str):
    with open(AUDIT_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")

# -------- SNMP --------
# pysnmp >= 6 n'a plus d'API synchrone : l'API vit dans pysnmp.hlapi.v3arch.asyncio
# et est asynchrone. On garde des méthodes get/set synchrones (compatibles avec le
# reste du code) en exécutant les coroutines dans un thread dédié muni de son
# propre event loop, car FastAPI a déjà un loop en cours dans le thread principal.
import asyncio
from concurrent.futures import ThreadPoolExecutor

_SNMP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="snmp")

def _run_coro_sync(coro):
    """Exécute une coroutine depuis un contexte sync, même si un event loop tourne déjà."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # pas de loop en cours (ex: route sync)
    return _SNMP_EXECUTOR.submit(asyncio.run, coro).result()

class SnmpClient:
    def __init__(self):
        self.version = os.getenv("SNMP_VERSION", "2c")
        self.community = os.getenv("SNMP_COMMUNITY", "public")
        self.v3_user = os.getenv("SNMPV3_USER")
        self.v3_auth = os.getenv("SNMPV3_AUTH")
        self.v3_auth_key = os.getenv("SNMPV3_AUTH_KEY")
        self.v3_priv = os.getenv("SNMPV3_PRIV")
        self.v3_priv_key = os.getenv("SNMPV3_PRIV_KEY")

    def _auth(self):
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData, UsmUserData,
            usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
            usmDESPrivProtocol, usmAesCfb128Protocol,
        )
        if self.version == "2c":
            return CommunityData(self.community)
        # SNMPv3 — niveaux supportés :
        #  - noAuthNoPriv : SNMPV3_USER seul (pas de clés)
        #  - authNoPriv   : + SNMPV3_AUTH (MD5|SHA) et SNMPV3_AUTH_KEY
        #  - authPriv     : + SNMPV3_PRIV (DES|AES) et SNMPV3_PRIV_KEY
        auth_map = {"MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol}
        priv_map = {"DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol}
        kwargs: Dict[str, Any] = {}
        if self.v3_auth_key:
            proto = auth_map.get((self.v3_auth or "").upper())
            if proto is None:
                log.warning("SNMPV3_AUTH=%r inconnu (attendu MD5 ou SHA), défaut: SHA", self.v3_auth)
                proto = usmHMACSHAAuthProtocol
            kwargs["authKey"] = self.v3_auth_key
            kwargs["authProtocol"] = proto
        if self.v3_priv_key:
            proto = priv_map.get((self.v3_priv or "").upper())
            if proto is None:
                log.warning("SNMPV3_PRIV=%r inconnu (attendu DES ou AES), défaut: AES", self.v3_priv)
                proto = usmAesCfb128Protocol
            kwargs["privKey"] = self.v3_priv_key
            kwargs["privProtocol"] = proto
        level = "authPriv" if "privKey" in kwargs else ("authNoPriv" if "authKey" in kwargs else "noAuthNoPriv")
        log.debug("SNMPv3: user=%r niveau=%s", self.v3_user, level)
        if not self.v3_user:
            raise HTTPException(500, "SNMP_VERSION=3 mais SNMPV3_USER non défini (docker-compose.yml)")
        return UsmUserData(self.v3_user, **kwargs)

    async def _aget(self, host: str, oid: str):
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, get_cmd
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create((host, 161), timeout=1, retries=1)
            errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
                engine, self._auth(), target, ContextData(), ObjectType(ObjectIdentity(oid)))
            if errorIndication or errorStatus:
                raise HTTPException(status_code=502, detail=f"SNMP GET failed ({host} {oid}): {errorIndication or errorStatus.prettyPrint()}")
            return varBinds[0][1].prettyPrint()
        finally:
            engine.close_dispatcher()

    async def _aset(self, host: str, oid: str, value: int):
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, set_cmd
        from pysnmp.proto.rfc1902 import Integer
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create((host, 161), timeout=1, retries=1)
            errorIndication, errorStatus, errorIndex, varBinds = await set_cmd(
                engine, self._auth(), target, ContextData(), ObjectType(ObjectIdentity(oid), Integer(value)))
            if errorIndication or errorStatus:
                raise HTTPException(status_code=502, detail=f"SNMP SET failed ({host} {oid}={value}): {errorIndication or errorStatus.prettyPrint()}")
            return True
        finally:
            engine.close_dispatcher()

    def get(self, host: str, oid: str) -> Any:
        if USE_MOCK:
            return self._mock_get(host, oid)
        log.debug("SNMP GET %s %s", host, oid)
        return _run_coro_sync(self._aget(host, oid))

    def get_many(self, host: str, oids: List[str]) -> List[Any]:
        """GET de plusieurs OIDs en parallèle. Retourne une liste alignée sur `oids` ;
        chaque élément est la valeur, ou l'exception si la lecture a échoué."""
        if USE_MOCK:
            return [self._mock_get(host, o) for o in oids]
        log.debug("SNMP GET (parallèle) %s : %d OIDs", host, len(oids))
        async def _many():
            return await asyncio.gather(*(self._aget(host, o) for o in oids), return_exceptions=True)
        return _run_coro_sync(_many())

    def set(self, host: str, oid: str, value: int) -> Any:
        if USE_MOCK:
            return self._mock_set(host, oid, value)
        log.debug("SNMP SET %s %s = %s", host, oid, value)
        return _run_coro_sync(self._aset(host, oid, value))

    # --- MOCK ---
    # Simulation cohérente : chaque prise allumée tire une charge stable (0.30–0.89 A,
    # déterministe par prise), le courant total = somme des prises ON + légère variation
    # lente (±2%). Tension, courant et puissance sont calculés ensemble dans un même
    # "snapshot" (rafraîchi au plus toutes les 1 s) pour garantir P = V × I.
    # Éteindre des prises fait donc baisser la consommation immédiatement.
    _mock_state: Dict[str, Dict[str, Any]] = {}

    def _outlet_load_a(self, host: str, idx: str) -> float:
        """Charge nominale stable d'une prise (déterministe par host+index)."""
        import hashlib
        h = int(hashlib.md5(f"{host}:{idx}".encode()).hexdigest(), 16)
        return round(0.30 + (h % 60) / 100.0, 2)  # 0.30 .. 0.89 A

    def _mock_pdu(self, host: str) -> Dict[str, Any]:
        import math
        if host not in self._mock_state:
            self._mock_state[host] = {
                "sysObjectID": "1.3.6.1.4.1.2.3.51.1",
                "voltage": 230.0, "current": 0.0, "power": 0.0, "_ts": 0.0,
                "outlets": {str(i): {"name": f"Outlet {i}", "state": 1} for i in range(1, 13)}
            }
        p = self._mock_state[host]
        now = time.time()
        # Snapshot rafraîchi au plus 1x/s : les 3 lectures d'une même requête
        # (tension, courant, puissance) restent cohérentes entre elles.
        if now - p["_ts"] >= 1.0:
            base = sum(self._outlet_load_a(host, i)
                       for i, d in p["outlets"].items() if d["state"] == 1)
            variation = 1.0 + 0.02 * math.sin(now / 7.0)   # ±2 % lent
            p["voltage"] = round(230.0 + 0.8 * math.sin(now / 13.0), 1)
            p["current"] = round(base * variation, 2)
            p["power"] = round(p["voltage"] * p["current"], 1)
            p["_ts"] = now
        return p

    def _mock_get(self, host: str, oid: str) -> Any:
        p = self._mock_pdu(host)
        if oid.endswith("sysObjectID"): return p["sysObjectID"]
        if oid.endswith("VOLTAGE"): return p["voltage"]
        if oid.endswith("CURRENT"): return p["current"]
        if oid.endswith("POWER"): return p["power"]
        if ".STATE." in oid:
            idx = oid.split(".STATE.")[-1]
            return p["outlets"][idx]["state"]
        if oid.endswith(".13") or ".13." in oid:
            idx = oid.split(".")[-1]
            return p["outlets"].get(idx, {}).get("state", 1)
        return "0"

    def _mock_set(self, host: str, oid: str, value: int) -> Any:
        p = self._mock_pdu(host)
        idx = None
        if ".CONTROL." in oid: idx = oid.split(".")[-1]
        elif ".13." in oid: idx = oid.split(".")[-1]
        if idx and idx.isdigit():
            if value == 1: p["outlets"][idx]["state"] = 1
            elif value == 2: p["outlets"][idx]["state"] = 2
            elif value == 3: p["outlets"][idx]["state"] = 1
            p["_ts"] = 0.0  # force le recalcul immédiat de la consommation
            return True
        return False

snmp = SnmpClient()

def get_profile_for(pdu: Dict[str, Any]) -> Dict[str, Any]:
    key = (pdu.get("model") or "IBM-DPI")
    profiles = MODELS.get('profiles', {})
    prof = profiles.get(key)
    if not prof:
        raise HTTPException(400, f"Profil inconnu: {key}")
    return prof

class Pdu(BaseModel):
    id: str
    ip: str
    model: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None

class Outlet(BaseModel):
    index: str
    name: str
    state: int              # 1=ON, 2=OFF, 0=inconnu, -1=non commutable (PDU surveillée)
    power_w: Optional[float] = None
    current_a: Optional[float] = None

class Metrics(BaseModel):
    voltage: Optional[float] = None
    current: Optional[float] = None
    power: Optional[float] = None
    temperature: Optional[float] = None

# History helpers
def _metrics_path(pdu_id: str) -> Path:
    return METRICS_DIR / f"{pdu_id}.jsonl"

def append_metrics(pdu_id: str, metrics: Dict[str, Any]):
    metrics = {k: v for k, v in metrics.items() if v is not None}
    if not metrics: return
    entry = {"ts": datetime.utcnow().isoformat() + "Z", **metrics}
    with open(_metrics_path(pdu_id), "a") as f:
        f.write(json.dumps(entry) + "\n")

def _parse_iso(s: str):
    try:
        if s.endswith('Z'): s = s[:-1]
        return datetime.fromisoformat(s)
    except Exception:
        return None

def read_history(pdu_id: str, limit: int = 600, since: str | None = None, until: str | None = None) -> List[Dict[str, Any]]:
    path = _metrics_path(pdu_id)
    if not path.exists(): return []
    with open(path, "r") as f:
        lines = f.readlines()
    lines = lines[-limit:]
    out = []
    since_dt = _parse_iso(since) if since else None
    until_dt = _parse_iso(until) if until else None
    for ln in lines:
        try:
            item = json.loads(ln)
            ts = _parse_iso(item.get('ts',''))
            if since_dt and ts and ts < since_dt: continue
            if until_dt and ts and ts > until_dt: continue
            out.append(item)
        except Exception:
            continue
    return out

# ---------- Routes ----------
@app.post("/auth/token")
def login(req: TokenRequest):
    log.info("Tentative de connexion: utilisateur=%r", req.username)
    u = USERS.get(req.username)
    if not u:
        log.warning("Connexion refusée: utilisateur inconnu %r", req.username)
        raise HTTPException(401, "Identifiants invalides")
    stored = u["password"]
    ok = False
    if stored.startswith("$2"):
        try:
            ok = bcrypt.verify(req.password, stored)
        except Exception as e:
            # ex: incompatibilité passlib/bcrypt -> log explicite au lieu d'un 500 silencieux
            log.error("Échec de la vérification bcrypt pour %r: %s", req.username, e)
            raise HTTPException(500, "Erreur interne de vérification du mot de passe (voir logs API)")
    else:
        ok = (stored == req.password)
    if not ok:
        log.warning("Connexion refusée: mot de passe invalide pour %r", req.username)
        raise HTTPException(401, "Identifiants invalides")
    log.info("Connexion réussie: %r (rôle=%s)", req.username, u["role"])
    token = create_token(User(username=req.username, role=u["role"]))
    return {"access_token": token, "token_type": "bearer"}

@app.get("/pdus", response_model=List[Pdu])
async def list_pdus(user: User = Depends(current_user)):
    return read_pdus()

@app.post("/pdus", response_model=Pdu)
async def add_pdu(pdu: Pdu, user: User = Depends(current_user)):
    # Validation : ID non vide (sinon les routes /pdus/{id}/... deviennent /pdus//...)
    if not pdu.id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", pdu.id):
        raise HTTPException(400, "ID invalide : 1 à 64 caractères (lettres, chiffres, - _ .), non vide")
    try:
        ipaddress.ip_address(pdu.ip)
    except ValueError:
        raise HTTPException(400, f"Adresse IP invalide : {pdu.ip!r}")
    pdus = read_pdus()
    if any(x["id"] == pdu.id for x in pdus):
        raise HTTPException(400, f"ID déjà utilisé : {pdu.id!r}")
    pdus.append(pdu.model_dump())
    write_pdus(pdus)
    log.info("PDU ajouté: %s (%s) par %s", pdu.id, pdu.ip, user.username)
    audit(f"{user.username} ADD_PDU {pdu.id} {pdu.ip}")
    return pdu

@app.delete("/pdus/{pdu_id}")
async def del_pdu(pdu_id: str, user: User = Depends(current_user)):
    pdus = read_pdus()
    pdus = [p for p in pdus if p["id"] != pdu_id]
    write_pdus(pdus)
    audit(f"{user.username} DEL_PDU {pdu_id}")
    return {"ok": True}

def find_pdu(pdu_id: str) -> Dict[str, Any]:
    pdu = next((p for p in read_pdus() if p["id"] == pdu_id), None)
    if not pdu:
        raise HTTPException(404, f"PDU inconnu: {pdu_id}")
    return pdu

@app.get("/pdus/{pdu_id}/outlets", response_model=List[Outlet])
async def list_outlets(pdu_id: str, user: User = Depends(current_user)):
    """Liste les prises (nom + état) d'un PDU. Route appelée par le GUI au clic sur un PDU."""
    pdu = find_pdu(pdu_id)
    profile = get_profile_for(pdu)
    o = profile.get("outlets") or {}

    if USE_MOCK:
        state = snmp._mock_pdu(pdu["ip"])
        outlets = [Outlet(index=i, name=d["name"], state=int(d["state"]))
                   for i, d in sorted(state["outlets"].items(), key=lambda kv: int(kv[0]))]
        log.debug("Outlets (mock) pour %s: %d prises", pdu_id, len(outlets))
        return outlets

    # SNMP réel — sonde de joignabilité d'abord : si le PDU ne répond pas,
    # on échoue vite (~2 s) avec un message clair au lieu de renvoyer 12
    # fausses prises après 24 timeouts séquentiels (~54 s).
    t0 = time.time()
    try:
        sys_oid = snmp.get(pdu["ip"], "1.3.6.1.2.1.1.2.0")  # sysObjectID standard
        log.debug("Sonde SNMP %s OK (sysObjectID=%s)", pdu["ip"], sys_oid)
    except Exception:
        log.error("PDU %s (%s) injoignable en SNMP", pdu_id, pdu["ip"])
        if snmp.version == "2c":
            hint = (f"Vérifiez que SNMP est activé sur le PDU, que la communauté '{snmp.community}' "
                    f"est correcte (SNMP_COMMUNITY dans docker-compose.yml) et que le port UDP/161 n'est pas filtré. "
                    f"Si le PDU est configuré en SNMPv3, passez SNMP_VERSION=3 et définissez SNMPV3_USER.")
        else:
            hint = (f"Vérifiez l'utilisateur SNMPv3 '{snmp.v3_user}', le niveau de sécurité "
                    f"(clés SNMPV3_AUTH_KEY/SNMPV3_PRIV_KEY et protocoles associés) et le port UDP/161.")
        raise HTTPException(502, f"PDU {pdu['ip']} injoignable en SNMP (timeout). {hint}")

    # Auto-détection du profil d'après le sysObjectID : l'arbre Powerware
    # (enterprises.534.6) correspond au firmware IBM DPI. Si le PDU est
    # enregistré avec un autre modèle, on corrige automatiquement (et en base)
    # pour éviter les noSuchName sur de mauvais OIDs.
    if "534.6" in str(sys_oid) and (pdu.get("model") or "") != "IBM-DPI":
        log.warning("Modèle de %s corrigé automatiquement: %r -> 'IBM-DPI' (sysObjectID=%s)",
                    pdu_id, pdu.get("model"), sys_oid)
        pdus = read_pdus()
        for p in pdus:
            if p["id"] == pdu_id: p["model"] = "IBM-DPI"
        write_pdus(pdus)
        audit(f"AUTO MODEL_FIX {pdu_id} -> IBM-DPI")
        pdu["model"] = "IBM-DPI"
        profile = get_profile_for(pdu)
        o = profile.get("outlets") or {}

    style = o.get("style", "table")
    outlets: List[Outlet] = []

    if style == "dpi":
        # IBM DPI : le nombre de prises est AUTO-DÉTECTÉ. On lit en un seul lot
        # parallèle, pour chaque groupe potentiel i (1..max_groups) :
        #   tension {group_base}.{i+1}.1.0  -> sonde d'existence du groupe
        #   nom     {name_base}.{i+1}.0
        #   courant {group_base}.{i+1}.4.0  (0.1 A)
        #   puiss.  {group_base}.{i+1}.10.0 (W)
        # Un groupe existe si sa tension répond (noSuchName sinon). state = -1
        # (PDU surveillée, non commutable).
        maxg = int(o.get("max_groups", 12))
        volt_oids = [f"{o['group_base']}.{i+1}.1.0" for i in range(1, maxg + 1)]
        name_oids = [f"{o['name_base']}.{i+1}.0" for i in range(1, maxg + 1)]
        cur_oids  = [f"{o['group_base']}.{i+1}.4.0" for i in range(1, maxg + 1)]
        pow_oids  = [f"{o['group_base']}.{i+1}.10.0" for i in range(1, maxg + 1)]
        results = snmp.get_many(pdu["ip"], volt_oids + name_oids + cur_oids + pow_oids)
        volts = results[:maxg]
        names = results[maxg:2*maxg]
        curs  = results[2*maxg:3*maxg]
        pows  = results[3*maxg:]

        def num(v, scale):
            if isinstance(v, Exception): return None
            try: return round(float(v) * scale, 2)
            except (ValueError, TypeError): return None

        for i in range(1, maxg + 1):
            if isinstance(volts[i-1], Exception):
                continue  # groupe inexistant sur ce PDU
            n = names[i-1]
            raw_name = "" if isinstance(n, Exception) else str(n).strip()
            name = raw_name if raw_name and raw_name.lower() != "[description]" else f"J{i}"
            outlets.append(Outlet(index=str(i), name=name, state=-1,
                                  current_a=num(curs[i-1], 0.1), power_w=num(pows[i-1], 1)))
        if not outlets:
            log.warning("Aucun load group détecté pour %s — vérifier le profil/firmware", pdu_id)
    else:
        # Style historique : nombre via outlet_count_oid puis table {name}.{i} / {state}.{i}
        try:
            count = int(snmp.get(pdu["ip"], profile["outlet_count_oid"]))
            if not (1 <= count <= 64): raise ValueError(f"count={count}")
        except Exception as e:
            log.warning("Lecture du nombre de prises impossible pour %s (%s), défaut=12", pdu_id, e)
            count = 12
        name_oids = [f"{o['name']}.{i}" for i in range(1, count + 1)] if o.get("name") else []
        state_oids = [f"{o['state']}.{i}" for i in range(1, count + 1)]
        results = snmp.get_many(pdu["ip"], name_oids + state_oids)
        names = results[:len(name_oids)]
        states = results[len(name_oids):]
        for i in range(1, count + 1):
            n = names[i-1] if name_oids else None
            name = str(n) if n is not None and not isinstance(n, Exception) and str(n).strip() else f"Outlet {i}"
            s = states[i-1]
            if isinstance(s, Exception):
                log.warning("État de la prise %s/%s illisible: %s", pdu_id, i, s)
                state_v = 0
            else:
                try: state_v = int(s)
                except (ValueError, TypeError): state_v = 0
            outlets.append(Outlet(index=str(i), name=name, state=state_v))

    log.info("Outlets (SNMP/%s) pour %s: %d prises en %.1f s", style, pdu_id, len(outlets), time.time() - t0)
    return outlets

class OutletAction(BaseModel):
    action: str  # on | off | cycle

@app.post("/pdus/{pdu_id}/outlets/{idx}/action")
async def outlet_action(pdu_id: str, idx: str, req: OutletAction, user: User = Depends(current_user)):
    """Commande ON/OFF/CYCLE d'une prise. Réservé au rôle operator."""
    if user.role != "operator":
        raise HTTPException(403, "Rôle 'operator' requis pour commander une prise")
    pdu = find_pdu(pdu_id)
    profile = get_profile_for(pdu)
    if profile.get("switched") is False:
        raise HTTPException(400,
            f"Ce modèle ({pdu.get('model') or 'IBM-DPI'}) est une PDU surveillée : "
            f"la MIB du firmware ne permet pas de commander les prises via SNMP (mesures uniquement).")
    mapping = {"on": 1, "off": 2, "cycle": 3}  # enums à vérifier selon firmware (cf. models.yaml)
    if req.action not in mapping:
        raise HTTPException(400, f"Action invalide: {req.action!r} (attendu: on, off ou cycle)")
    state_oid = (profile.get("outlets") or {}).get("state")
    if not state_oid:
        raise HTTPException(400, "OID 'state' absent du profil (models.yaml)")
    log.info("Action prise: %s %s outlet=%s par %s", req.action.upper(), pdu_id, idx, user.username)
    ok = snmp.set(pdu["ip"], f"{state_oid}.{idx}", mapping[req.action])
    if not ok:
        log.error("Échec SNMP SET pour %s outlet=%s action=%s", pdu_id, idx, req.action)
        raise HTTPException(502, f"Échec de la commande {req.action} sur la prise {idx}")
    audit(f"{user.username} OUTLET_{req.action.upper()} {pdu_id} outlet={idx}")
    return {"ok": True}

@app.get("/pdus/{pdu_id}/metrics", response_model=Metrics)
async def get_metrics(pdu_id: str, record: bool = False, user: User = Depends(current_user)):
    pdu = next((p for p in read_pdus() if p["id"] == pdu_id), None)
    if not pdu: raise HTTPException(404, "PDU inconnu")
    profile = get_profile_for(pdu)
    m = profile.get('metrics') or {}

    def maybe_get_float(oid_key: str):
        entry = m.get(oid_key)
        if not entry: return None
        oid = entry.get("oid") if isinstance(entry, dict) else entry
        scale = float(entry.get("scale", 1)) if isinstance(entry, dict) else 1.0
        if not oid: return None
        try: return round(float(snmp.get(pdu['ip'], oid)) * scale, 2)
        except Exception as e:
            log.warning("Métrique %r illisible pour %s: %s", oid_key, pdu["id"], e)
            return None

    if USE_MOCK and not m:
        metrics = Metrics(
            voltage=float(snmp.get(pdu['ip'], "X.VOLTAGE")),
            current=float(snmp.get(pdu['ip'], "X.CURRENT")),
            power=float(snmp.get(pdu['ip'], "X.POWER")),
            temperature=None,
        )
    else:
        metrics = Metrics(
            voltage=maybe_get_float('voltage'),
            current=maybe_get_float('current'),
            power=maybe_get_float('power'),
            temperature=maybe_get_float('temperature'),
        )

    if record: append_metrics(pdu_id, metrics.model_dump())
    return metrics

@app.get("/pdus/{pdu_id}/metrics/history")
async def metrics_history(pdu_id: str, limit: int = 600, since: str | None = None, until: str | None = None, user: User = Depends(current_user)):
    pdu = next((p for p in read_pdus() if p["id"] == pdu_id), None)
    if not pdu: raise HTTPException(404, "PDU inconnu")
    return read_history(pdu_id, limit, since, until)

@app.get("/pdus/{pdu_id}/metrics/history.csv")
async def metrics_history_csv(pdu_id: str, limit: int = 600, since: str | None = None, until: str | None = None, user: User = Depends(current_user)):
    pdu = next((p for p in read_pdus() if p["id"] == pdu_id), None)
    if not pdu: raise HTTPException(404, "PDU inconnu")
    items = read_history(pdu_id, limit, since, until)
    fields = ["ts","voltage","current","power","temperature"]
    lines = [",".join(fields)]
    for it in items:
        row = [str(it.get(k, "")) for k in fields]
        lines.append(",".join(row))
    return Response("\n".join(lines), media_type="text/csv")

@app.get("/metrics")
def prometheus_metrics():
    reg = CollectorRegistry()
    g_voltage = Gauge("pdu_voltage_volts", "Voltage per PDU", ["pdu"], registry=reg)
    g_current = Gauge("pdu_current_amps", "Current per PDU", ["pdu"], registry=reg)
    g_power = Gauge("pdu_power_watts", "Power per PDU", ["pdu"], registry=reg)
    for pdu in read_pdus():
        hist = read_history(pdu["id"], limit=1)
        if hist:
            last = hist[-1]
            if "voltage" in last and last["voltage"] is not None: g_voltage.labels(pdu=pdu["id"]).set(float(last["voltage"]))
            if "current" in last and last["current"] is not None: g_current.labels(pdu=pdu["id"]).set(float(last["current"]))
            if "power" in last and last["power"] is not None: g_power.labels(pdu=pdu["id"]).set(float(last["power"]))
    data = generate_latest(reg)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

@app.get("/discover")
async def discover(cidr: str, max_hosts: int = 256, user: User = Depends(current_user)):
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except Exception:
        raise HTTPException(400, "CIDR invalide")
    hosts = list(net.hosts())
    if len(hosts) > max_hosts:
        hosts = hosts[:max_hosts]
    results = []
    for h in hosts:
        ip = str(h)
        try:
            soid = snmp.get(ip, "1.3.6.1.2.1.1.2.0")
            results.append({"ip": ip, "reachable": True, "sysObjectID": soid, "suggested_model": "IBM-42R8743"})
        except Exception:
            continue
    return {"found": results}
