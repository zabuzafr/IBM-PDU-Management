from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from pathlib import Path
from datetime import datetime
import json, os, yaml, time, ipaddress, re
from jose import jwt
from jose.exceptions import JWTError
from passlib.hash import bcrypt
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST

SECRET_KEY = os.getenv("SECRET_KEY", "change_me")
API_AUDIENCE = "ibm-pdu-ui"
ALGO = "HS256"
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
PDUS_FILE = DATA_DIR / "pdus.json"
AUDIT_FILE = DATA_DIR / "audit.log"
MODELS_FILE = Path("models.yaml")
USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"
METRICS_DIR = DATA_DIR / "metrics"; METRICS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="IBM PDU Manager API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_models() -> Dict[str, Any]:
    with open(MODELS_FILE, "r") as f:
        return yaml.safe_load(f)

MODELS = load_models()

# demo users (admin hashé: 'admin', reader plaintext: 'reader')
USERS = {
    "admin": {"password": "$2b$12$3mUp8R.aeOHh2y4O8M10UeWmT2xV8iVjHn8eY0C1p7sB3xP4qvU3W", "role": "operator"},
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
class SnmpClient:
    def __init__(self):
        self.version = os.getenv("SNMP_VERSION", "2c")
        self.community = os.getenv("SNMP_COMMUNITY", "public")
        self.v3_user = os.getenv("SNMPV3_USER")
        self.v3_auth = os.getenv("SNMPV3_AUTH")
        self.v3_auth_key = os.getenv("SNMPV3_AUTH_KEY")
        self.v3_priv = os.getenv("SNMPV3_PRIV")
        self.v3_priv_key = os.getenv("SNMPV3_PRIV_KEY")

    def get(self, host: str, oid: str) -> Any:
        if USE_MOCK:
            return self._mock_get(host, oid)
        from pysnmp.hlapi import SnmpEngine, CommunityData, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, getCmd, UsmUserData, usmHMACSHAAuthProtocol, usmAesCfb128Protocol
        engine = SnmpEngine()
        if self.version == "2c":
            auth = CommunityData(self.community)
        else:
            auth_proto = usmHMACSHAAuthProtocol if (self.v3_auth or "").upper()=="SHA" else None
            priv_proto = usmAesCfb128Protocol if (self.v3_priv or "").upper()=="AES" else None
            auth = UsmUserData(self.v3_user, self.v3_auth_key, self.v3_priv_key, authProtocol=auth_proto, privProtocol=priv_proto)
        errorIndication, errorStatus, errorIndex, varBinds = next(getCmd(engine, auth, UdpTransportTarget((host, 161), timeout=1, retries=1), ContextData(), ObjectType(ObjectIdentity(oid))))
        if errorIndication or errorStatus:
            raise HTTPException(status_code=502, detail=f"SNMP GET failed: {errorIndication or errorStatus.prettyPrint()}")
        return varBinds[0][1].prettyPrint()

    def set(self, host: str, oid: str, value: int) -> Any:
        if USE_MOCK:
            return self._mock_set(host, oid, value)
        from pysnmp.hlapi import SnmpEngine, CommunityData, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, Integer, setCmd, UsmUserData, usmHMACSHAAuthProtocol, usmAesCfb128Protocol
        engine = SnmpEngine()
        if self.version == "2c":
            auth = CommunityData(self.community)
        else:
            auth_proto = usmHMACSHAAuthProtocol if (self.v3_auth or "").upper()=="SHA" else None
            priv_proto = usmAesCfb128Protocol if (self.v3_priv or "").upper()=="AES" else None
            auth = UsmUserData(self.v3_user, self.v3_auth_key, self.v3_priv_key, authProtocol=auth_proto, privProtocol=priv_proto)
        errorIndication, errorStatus, errorIndex, varBinds = next(setCmd(engine, auth, UdpTransportTarget((host, 161), timeout=1, retries=1), ContextData(), ObjectType(ObjectIdentity(oid), Integer(value))))
        if errorIndication or errorStatus:
            raise HTTPException(status_code=502, detail=f"SNMP SET failed: {errorIndication or errorStatus.prettyPrint()}")
        return True

    # --- MOCK ---
    _mock_state: Dict[str, Dict[str, Any]] = {}

    def _mock_pdu(self, host: str) -> Dict[str, Any]:
        if host not in self._mock_state:
            self._mock_state[host] = {
                "sysObjectID": "1.3.6.1.4.1.2.3.51.1",
                "voltage": 230, "current": 5.2, "power": 1196,
                "outlets": {str(i): {"name": f"Outlet {i}", "state": 1} for i in range(1, 13)}
            }
        p = self._mock_state[host]
        p["current"] = round(max(0.0, p["current"] + (0.5 - time.time()%1)), 2)
        p["power"] = round(p["voltage"] * p["current"], 2)
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
            return True
        return False

snmp = SnmpClient()

def get_profile_for(pdu: Dict[str, Any]) -> Dict[str, Any]:
    key = (pdu.get("model") or "IBM-42R8743")
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
    state: int

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
    u = USERS.get(req.username)
    if not u:
        raise HTTPException(401, "Identifiants invalides")
    stored = u["password"]
    ok = (stored.startswith("$2") and bcrypt.verify(req.password, stored)) or (stored == req.password)
    if not ok:
        raise HTTPException(401, "Identifiants invalides")
    token = create_token(User(username=req.username, role=u["role"]))
    return {"access_token": token, "token_type": "bearer"}

@app.get("/pdus", response_model=List[Pdu])
async def list_pdus(user: User = Depends(current_user)):
    return read_pdus()

@app.post("/pdus", response_model=Pdu)
async def add_pdu(pdu: Pdu, user: User = Depends(current_user)):
    pdus = read_pdus()
    if any(x["id"] == pdu.id for x in pdus):
        raise HTTPException(400, "ID déjà utilisé")
    pdus.append(pdu.model_dump())
    write_pdus(pdus)
    audit(f"{user.username} ADD_PDU {pdu.id} {pdu.ip}")
    return pdu

@app.delete("/pdus/{pdu_id}")
async def del_pdu(pdu_id: str, user: User = Depends(current_user)):
    pdus = read_pdus()
    pdus = [p for p in pdus if p["id"] != pdu_id]
    write_pdus(pdus)
    audit(f"{user.username} DEL_PDU {pdu_id}")
    return {"ok": True}

@app.get("/pdus/{pdu_id}/metrics", response_model=Metrics)
async def get_metrics(pdu_id: str, record: bool = False, user: User = Depends(current_user)):
    pdu = next((p for p in read_pdus() if p["id"] == pdu_id), None)
    if not pdu: raise HTTPException(404, "PDU inconnu")
    profile = get_profile_for(pdu)
    m = profile.get('metrics') or {}

    def maybe_get_float(oid_key: str):
        oid = m.get(oid_key)
        if not oid: return None
        try: return float(snmp.get(pdu['ip'], oid))
        except Exception: return None

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
