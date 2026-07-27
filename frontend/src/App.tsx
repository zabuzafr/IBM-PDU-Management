import React, { useEffect, useMemo, useState } from "react";
import { Power, RefreshCw, Plug, Plus, Server, CircleAlert, Trash2, Pencil } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from "recharts";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const DEBUG = import.meta.env.DEV || import.meta.env.VITE_DEBUG === "true";
const POLL_MS = 5000; // 5s
const PERIODS = { '15m': 15*60, '1h': 60*60, '24h': 24*60*60 } as const;

function dbg(...args: any[]) { if (DEBUG) console.debug("[PDU-UI]", ...args); }

/** Extrait un message d'erreur lisible d'une réponse HTTP (JSON {detail} ou texte brut). */
async function readError(r: Response): Promise<string> {
  try {
    const j = await r.clone().json();
    if (j?.detail) return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
  } catch { /* pas du JSON */ }
  try { const t = await r.text(); if (t) return t; } catch { /* ignore */ }
  return `Erreur HTTP ${r.status}`;
}

/** fetch avec logs debug + messages d'erreur explicites (réseau vs HTTP). */
async function apiFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const url = `${API}${path}`;
  dbg("→", opts.method || "GET", url);
  let r: Response;
  try {
    r = await fetch(url, opts);
  } catch (e: any) {
    console.error("[PDU-UI] Erreur réseau sur", url, e);
    throw new Error(`API injoignable (${API}) : le backend est-il démarré ? (${e.message})`);
  }
  dbg("←", r.status, url);
  if (!r.ok) {
    const msg = await readError(r);
    console.warn("[PDU-UI] HTTP", r.status, url, "-", msg);
    throw new Error(`${msg} [${r.status}]`);
  }
  return r;
}

function useToken() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const login = async (username: string, password: string) => {
    dbg("Tentative de connexion:", username);
    const r = await apiFetch(`/auth/token`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({username, password})});
    const j = await r.json();
    dbg("Connexion réussie");
    localStorage.setItem("token", j.access_token); setToken(j.access_token);
  };
  const logout = () => { dbg("Déconnexion"); localStorage.removeItem("token"); setToken(null); };
  return { token, login, logout };
}

type Pdu = { id: string; ip: string; model?: string; location?: string; notes?: string };
type Outlet = { index: string; name: string; state: number; power_w?: number|null; current_a?: number|null; power_estimated?: boolean };
type Metrics = { voltage?: number; current?: number; power?: number; temperature?: number };
type HistPoint = { ts: string; voltage?: number; current?: number; power?: number; temperature?: number };

export default function App() {
  const { token, login, logout } = useToken();
  const [pdus, setPdus] = useState<Pdu[]>([]);
  const [filter, setFilter] = useState("");
  const [sel, setSel] = useState<Pdu | null>(null);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [history, setHistory] = useState<HistPoint[]>([]);
  const [period, setPeriod] = useState<keyof typeof PERIODS>('15m');
  const [paused, setPaused] = useState(false);
  const [discOpen, setDiscOpen] = useState(false);
  const [discCidr, setDiscCidr] = useState("192.168.1.0/24");
  const [discResults, setDiscResults] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (!token) return; (async () => {
    try {
      const r = await apiFetch(`/pdus`, { headers: { Authorization: `Bearer ${token}` } });
      setPdus(await r.json());
      setErr(null);
    } catch (e:any) {
      setErr(`Chargement des PDUs impossible : ${e.message}`);
      // Token invalide/expiré -> retour à l'écran de connexion
      if (/\[401\]/.test(e.message)) { dbg("Token invalide, déconnexion"); logout(); }
    }
  })(); }, [token]);

  useEffect(() => { if (!token || !sel) return; (async () => {
    try {
      dbg("Chargement du PDU", sel.id);
      const [m, o, h] = await Promise.all([
        apiFetch(`/pdus/${sel.id}/metrics?record=true`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
        apiFetch(`/pdus/${sel.id}/outlets`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
        apiFetch(`/pdus/${sel.id}/metrics/history?limit=${Math.ceil(PERIODS[period]/5)}`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
      ]);
      dbg("PDU chargé:", { metrics: m, outlets: o.length, history: h.length });
      setMetrics(m); setOutlets(o); setHistory(h); setErr(null);
    } catch (e:any) { setErr(`Chargement du PDU "${sel.id}" impossible : ${e.message}`); }
  })(); }, [sel, token, period]);

  useEffect(() => {
    if (!token || !sel || paused) return;
    const t = setInterval(async () => {
      try {
        const m: Metrics = await fetch(`${API}/pdus/${sel.id}/metrics?record=true`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json());
        setMetrics(m);
        const point: HistPoint = { ts: new Date().toISOString(), ...m };
        setHistory(h => {
          const maxPts = Math.ceil(PERIODS[period]/5);
          const next = [...h, point];
          return next.slice(-maxPts);
        });
      } catch (e:any) { /* ignore transient */ }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [sel, token, paused, period]);

  const filtered = useMemo(() => pdus.filter(p => (p.id + p.ip + (p.location||"") + (p.model||"")).toLowerCase().includes(filter.toLowerCase())), [pdus, filter]);

  const renamePdu = async (p: Pdu) => {
    if (!token) return;
    const newId = window.prompt(`Nouveau nom pour le PDU "${p.id}" :`, p.id);
    if (!newId || newId === p.id) return;
    try {
      dbg("Renommage PDU:", p.id, "->", newId);
      const r = await apiFetch(`/pdus/${p.id}`, { method: "PUT", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({ new_id: newId }) });
      const updated = await r.json();
      setPdus(pdus => pdus.map(x => x.id === p.id ? updated : x));
      if (sel?.id === p.id) setSel(updated);
      setErr(null);
    } catch (e:any) { setErr(`Renommage de "${p.id}" impossible : ${e.message}`); }
  };

  const renameOutlet = async (o: Outlet) => {
    if (!sel || !token) return;
    const name = window.prompt(`Nouveau nom pour la prise #${o.index} (écrit dans le PDU) :`, o.name);
    if (!name || name === o.name) return;
    try {
      dbg("Renommage prise:", sel.id, o.index, "->", name);
      await apiFetch(`/pdus/${sel.id}/outlets/${o.index}/name`, { method: "PUT", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({ name }) });
      const os = await apiFetch(`/pdus/${sel.id}/outlets`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json());
      setOutlets(os); setErr(null);
    } catch (e:any) { setErr(`Renommage de la prise ${o.index} impossible : ${e.message}`); }
  };

  const delPdu = async (p: Pdu) => {
    if (!token) return;
    if (!window.confirm(`Supprimer le PDU "${p.id}" (${p.ip}) ?`)) return;
    try {
      dbg("Suppression PDU:", p.id);
      await apiFetch(`/pdus/${p.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
      setPdus(pdus => pdus.filter(x => x.id !== p.id));
      if (sel?.id === p.id) { setSel(null); setOutlets([]); setMetrics(null); setHistory([]); }
      setErr(null);
    } catch (e:any) { setErr(`Suppression de "${p.id}" impossible : ${e.message}`); }
  };

  const act = async (idx: string, action: "on"|"off"|"cycle") => {
    if (!sel || !token) return;
    try {
      dbg("Action prise:", action, "outlet", idx, "sur", sel.id);
      await apiFetch(`/pdus/${sel.id}/outlets/${idx}/action`, { method: "POST", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({action}) });
      // Rafraîchit prises ET métriques pour voir la consommation réagir immédiatement
      const [o, m] = await Promise.all([
        apiFetch(`/pdus/${sel.id}/outlets`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
        apiFetch(`/pdus/${sel.id}/metrics?record=true`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
      ]);
      setOutlets(o); setMetrics(m);
      setHistory(h => [...h, { ts: new Date().toISOString(), ...m }].slice(-Math.ceil(PERIODS[period]/5)));
      setErr(null);
    } catch (e:any) { setErr(`Action "${action}" sur la prise ${idx} impossible : ${e.message}`); }
  };

  if (!token) return <Login onLogin={login} error={err}/>;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold flex items-center gap-2"><Plug className="w-6 h-6"/> IBM PDU Manager</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Rechercher…" value={filter} onChange={e=>setFilter(e.target.value)} />
          <button className="btn btn-ghost" onClick={logout}>Déconnexion</button>
        </div>
      </header>

      {err && <div className="card p-3 text-red-600 flex items-center gap-2"><CircleAlert/> <span className="text-sm">{err}</span></div>}

      <section className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map(p => (
          <article key={p.id} className={`card p-4 cursor-pointer ${sel?.id===p.id? 'ring-2 ring-blue-500':''}`} onClick={()=>setSel(p)}>
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <div className="font-medium flex items-center gap-2"><Server className="w-4 h-4"/> {p.id}</div>
                <div className="text-xs opacity-70">{p.ip} {p.location? `· ${p.location}`:''}</div>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-xs opacity-60">{p.model || 'Modèle inconnu'}</span>
                <button className="btn btn-ghost p-1" title={`Renommer ${p.id}`} onClick={(e)=>{ e.stopPropagation(); renamePdu(p); }}><Pencil className="w-4 h-4"/></button>
                <button className="btn btn-ghost p-1" title={`Supprimer ${p.id}`} onClick={(e)=>{ e.stopPropagation(); delPdu(p); }}><Trash2 className="w-4 h-4 text-red-500"/></button>
              </div>
            </div>
            {sel?.id===p.id && metrics && (
              <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                <div className="card p-2"><div className="text-xs opacity-60">Tension</div><div className="text-lg font-semibold">{metrics.voltage??'-'} V</div></div>
                <div className="card p-2"><div className="text-xs opacity-60">Courant</div><div className="text-lg font-semibold">{metrics.current??'-'} A</div></div>
                <div className="card p-2"><div className="text-xs opacity-60">Puissance</div><div className="text-lg font-semibold">{metrics.power??'-'} W</div></div>
              </div>
            )}
          </article>
        ))}
        <AddPdu onAdded={(p)=>setPdus([...pdus, p])} token={token} />
      </section>

      {sel && (
        <section className="card p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold flex items-center gap-2"><Plug className="w-4 h-4"/> Prises — {sel.id}</h2>
            <div className="flex gap-2">
              <button className="btn" onClick={()=>sel && setSel({...sel})}><RefreshCw className="w-4 h-4"/></button>
            </div>
          </div>

          {/* Outils */}
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <label className="text-xs opacity-60">Période</label>
            <select className="input w-auto" value={period} onChange={e=>setPeriod(e.target.value as any)}>
              <option value="15m">15 min</option>
              <option value="1h">1 heure</option>
              <option value="24h">24 heures</option>
            </select>
            <button className="btn" onClick={()=>setPaused(p=>!p)}>{paused? 'Reprendre' : 'Pause'}</button>
            <button className="btn" onClick={async()=>{
              if (!sel || !token) return;
              const r = await fetch(`${API}/pdus/${sel.id}/metrics/history.csv?limit=${Math.ceil(PERIODS[period]/5)}`, { headers: { Authorization: `Bearer ${token}` } });
              const blob = await r.blob();
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = `${sel.id}-history-${period}.csv`;
              document.body.appendChild(a); a.click(); a.remove();
            }}>Export CSV</button>
            <button className="btn" onClick={()=>setDiscOpen(true)}>Découverte réseau</button>
          </div>

          {/* Graphiques temps réel */}
          <div className="grid md:grid-cols-3 gap-3">
            <ChartCard title="Tension (V)" data={history} dataKey="voltage"/>
            <ChartCard title="Courant (A)" data={history} dataKey="current"/>
            <ChartCard title="Puissance (W)" data={history} dataKey="power"/>
          </div>

          <table className="table">
            <thead><tr><th>#</th><th>Nom</th><th>État</th><th>Courant</th><th>Puissance</th><th>Actions</th></tr></thead>
            <tbody>
              {outlets.map(o => (
                <tr key={o.index} className="border-t border-neutral-200 dark:border-neutral-800">
                  <td>{o.index}</td>
                  <td>
                    <span className="inline-flex items-center gap-1">
                      {o.name}
                      <button className="btn btn-ghost p-0.5" title={`Renommer la prise ${o.index}`} onClick={()=>renameOutlet(o)}><Pencil className="w-3.5 h-3.5 opacity-50 hover:opacity-100"/></button>
                    </span>
                  </td>
                  <td>{o.state===-1? <span className="badge">Mesure</span> : o.state===1? <span className="badge badge-on">ON</span> : o.state===2? <span className="badge badge-off">OFF</span> : <span className="badge">?</span>}</td>
                  <td>{o.current_a != null ? `${o.current_a} A` : '—'}</td>
                  <td title={o.power_estimated ? "Puissance estimée (V × I × cosφ) : le firmware ne fournit pas la mesure directe pour cette prise" : undefined}>
                    {o.power_w != null ? `${o.power_estimated ? '~' : ''}${o.power_w} W` : '—'}
                  </td>
                  <td className="flex gap-2">
                    {o.state===-1 ? (
                      <span className="text-xs opacity-60">PDU surveillée — non commutable via SNMP</span>
                    ) : (<>
                      <button className="btn" onClick={()=>act(o.index, 'on')}><Power className="w-4 h-4"/> ON</button>
                      <button className="btn" onClick={()=>act(o.index, 'off')}><Power className="w-4 h-4"/> OFF</button>
                      <button className="btn btn-primary" onClick={()=>act(o.index, 'cycle')}><RefreshCw className="w-4 h-4"/> Cycle</button>
                    </>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Discovery Modal */}
      {discOpen && (
        <div className="fixed inset-0 bg-black/40 grid place-content-center p-4" onClick={()=>setDiscOpen(false)}>
          <div className="card p-4 w-[560px] space-y-3" onClick={e=>e.stopPropagation()}>
            <h3 className="font-semibold">Découverte réseau</h3>
            <div className="flex gap-2 items-center">
              <input className="input" placeholder="CIDR (ex: 192.168.1.0/24)" value={discCidr} onChange={e=>setDiscCidr(e.target.value)} />
              <button className="btn btn-primary" onClick={async()=>{
                if (!token) return;
                const r = await fetch(`${API}/discover?cidr=${encodeURIComponent(discCidr)}`, { headers: { Authorization: `Bearer ${token}` } });
                const j = await r.json();
                setDiscResults(j.found || []);
              }}>Scanner</button>
            </div>
            <div className="max-h-80 overflow-auto">
              <table className="table">
                <thead><tr><th>IP</th><th>sysObjectID</th><th>Modèle</th><th></th></tr></thead>
                <tbody>
                  {discResults.map((x,i)=>(
                    <tr key={i} className="border-t border-neutral-200 dark:border-neutral-800">
                      <td>{x.ip}</td>
                      <td className="text-xs opacity-70">{x.sysObjectID}</td>
                      <td>{x.suggested_model}</td>
                      <td>
                        <button className="btn" onClick={async()=>{
                          if (!token) return;
                          const id = `pdu-${x.ip.replaceAll('.','-')}`;
                          const r = await fetch(`${API}/pdus`, { method: "POST", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({id, ip: x.ip, model: x.suggested_model})});
                          if (r.ok) {
                            const p = await r.json();
                            setPdus(pdus=>[...pdus, p]); setSel(p);
                          }
                        }}>Ajouter</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="text-xs opacity-60">Astuce : limitez la taille du CIDR (max 256 hôtes par requête).</div>
          </div>
        </div>
      )}
    </div>
  );
}

function Login({ onLogin, error }: { onLogin: (u:string,p:string)=>Promise<void>; error: string|null }){
  const [u,setU] = useState("admin");
  const [p,setP] = useState("admin");
  const [loading, setLoading] = useState(false);
  const [localErr, setLocalErr] = useState<string|null>(null);
  const [apiUp, setApiUp] = useState<boolean|null>(null);

  // Vérifie que l'API répond dès l'affichage de l'écran de connexion
  useEffect(() => { (async () => {
    try {
      const r = await fetch(`${API}/health`);
      dbg("/health:", r.status, await r.clone().json().catch(()=>null));
      setApiUp(r.ok);
    } catch (e) {
      console.error("[PDU-UI] API injoignable:", API, e);
      setApiUp(false);
    }
  })(); }, []);

  const submit = async () => {
    setLoading(true); setLocalErr(null);
    try { await onLogin(u, p); }
    catch (e:any) {
      const msg = /\[401\]/.test(e.message)
        ? "Identifiants invalides (par défaut : admin / admin ou reader / reader)"
        : e.message;
      setLocalErr(msg);
    }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen grid place-content-center p-6">
      <div className="card p-6 w-[380px] space-y-3">
        <h1 className="text-xl font-semibold">Connexion</h1>
        {apiUp === false && (
          <div className="text-red-600 text-sm flex items-start gap-2">
            <CircleAlert className="w-4 h-4 mt-0.5 shrink-0"/>
            <span>API injoignable à <code>{API}</code>. Vérifiez que le backend tourne (<code>docker-compose logs api</code>).</span>
          </div>
        )}
        {(localErr || error) && <div className="text-red-600 text-sm">{localErr || error}</div>}
        <input className="input" placeholder="Utilisateur" value={u} onChange={e=>setU(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/>
        <input className="input" type="password" placeholder="Mot de passe" value={p} onChange={e=>setP(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/>
        <button className="btn btn-primary w-full" disabled={loading} onClick={submit}>{loading ? "Connexion…" : "Se connecter"}</button>
        <div className="text-xs opacity-60">API : {API} {apiUp === true ? "· ✓ en ligne" : apiUp === false ? "· ✗ hors ligne" : ""}</div>
      </div>
    </div>
  );
}

function AddPdu({ onAdded, token }: { onAdded: (p:Pdu)=>void; token:string }){
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [ip, setIp] = useState("");
  const [model, setModel] = useState("IBM-DPI");
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const create = async () => {
    const r = await fetch(`${API}/pdus`, { method: "POST", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({id, ip, model, location, notes})});
    if (!r.ok) return alert(await r.text());
    const p = await r.json(); onAdded(p); setOpen(false); setId(""); setIp(""); setModel("IBM-DPI"); setLocation(""); setNotes("");
  };
  return (
    <div className="card p-4 flex items-center justify-center text-neutral-500 hover:opacity-90 cursor-pointer" onClick={()=>setOpen(true)}>
      <div className="flex items-center gap-2"><Plus className="w-4 h-4"/> Ajouter un PDU</div>
      {open && (
        <div className="fixed inset-0 bg-black/40 grid place-content-center p-4" onClick={()=>setOpen(false)}>
          <div className="card p-4 w-[420px]" onClick={e=>e.stopPropagation()}>
            <h3 className="font-semibold mb-2">Nouveau PDU</h3>
            <div className="grid grid-cols-2 gap-2">
              <input className="input" placeholder="ID" value={id} onChange={e=>setId(e.target.value)} />
              <input className="input" placeholder="IP" value={ip} onChange={e=>setIp(e.target.value)} />
              <select className="input" value={model} onChange={e=>setModel(e.target.value)}>
                <option value="IBM-DPI">IBM-DPI (surveillée)</option>
                <option value="IBM-42R8743">IBM-42R8743</option>
                <option value="">(autre)</option>
              </select>
              <input className="input" placeholder="Emplacement" value={location} onChange={e=>setLocation(e.target.value)} />
              <textarea className="input col-span-2" placeholder="Notes" value={notes} onChange={e=>setNotes(e.target.value)} />
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button className="btn btn-ghost" onClick={()=>setOpen(false)}>Annuler</button>
              <button className="btn btn-primary" onClick={create}>Ajouter</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ChartCard({ title, data, dataKey }: { title: string; data: HistPoint[]; dataKey: keyof HistPoint }){
  const series = data.filter(d => (d as any)[dataKey] !== undefined).map(d => ({ t: new Date(d.ts).toLocaleTimeString(), v: (d as any)[dataKey] as number }));
  return (
    <div className="card p-3">
      <div className="text-xs opacity-60 mb-1">{title}</div>
      <div style={{ width: "100%", height: 180 }}>
        <ResponsiveContainer>
          <LineChart data={series} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="t" minTickGap={20} />
            <YAxis allowDecimals />
            <Tooltip />
            <Line type="monotone" dataKey="v" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
