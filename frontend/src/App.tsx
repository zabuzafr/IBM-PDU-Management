import React, { useEffect, useMemo, useState } from "react";
import { Power, RefreshCw, Plug, Plus, Server, CircleAlert } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer } from "recharts";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const POLL_MS = 5000; // 5s
const PERIODS = { '15m': 15*60, '1h': 60*60, '24h': 24*60*60 } as const;

function useToken() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const login = async (username: string, password: string) => {
    const r = await fetch(`${API}/auth/token`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({username, password})});
    if (!r.ok) throw new Error("Login failed");
    const j = await r.json();
    localStorage.setItem("token", j.access_token); setToken(j.access_token);
  };
  const logout = () => { localStorage.removeItem("token"); setToken(null); };
  return { token, login, logout };
}

type Pdu = { id: string; ip: string; model?: string; location?: string; notes?: string };
type Outlet = { index: string; name: string; state: number };
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
      const r = await fetch(`${API}/pdus`, { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) throw new Error(await r.text());
      setPdus(await r.json());
    } catch (e:any) { setErr(e.message); }
  })(); }, [token]);

  useEffect(() => { if (!token || !sel) return; (async () => {
    try {
      const [m, o, h] = await Promise.all([
        fetch(`${API}/pdus/${sel.id}/metrics?record=true`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
        fetch(`${API}/pdus/${sel.id}/outlets`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
        fetch(`${API}/pdus/${sel.id}/metrics/history?limit=${Math.ceil(PERIODS[period]/5)}`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json()),
      ]);
      setMetrics(m); setOutlets(o); setHistory(h);
    } catch (e:any) { setErr(e.message); }
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

  const act = async (idx: string, action: "on"|"off"|"cycle") => {
    if (!sel || !token) return;
    const r = await fetch(`${API}/pdus/${sel.id}/outlets/${idx}/action`, { method: "POST", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({action}) });
    if (!r.ok) { setErr(await r.text()); return; }
    const o = await fetch(`${API}/pdus/${sel.id}/outlets`, { headers: { Authorization: `Bearer ${token}` } }).then(r=>r.json());
    setOutlets(o);
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
              <span className="text-xs opacity-60">{p.model || 'Modèle inconnu'}</span>
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
            <thead><tr><th>#</th><th>Nom</th><th>État</th><th>Actions</th></tr></thead>
            <tbody>
              {outlets.map(o => (
                <tr key={o.index} className="border-t border-neutral-200 dark:border-neutral-800">
                  <td>{o.index}</td>
                  <td>{o.name}</td>
                  <td>{o.state===1? <span className="badge badge-on">ON</span> : <span className="badge badge-off">OFF</span>}</td>
                  <td className="flex gap-2">
                    <button className="btn" onClick={()=>act(o.index, 'on')}><Power className="w-4 h-4"/> ON</button>
                    <button className="btn" onClick={()=>act(o.index, 'off')}><Power className="w-4 h-4"/> OFF</button>
                    <button className="btn btn-primary" onClick={()=>act(o.index, 'cycle')}><RefreshCw className="w-4 h-4"/> Cycle</button>
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
  return (
    <div className="min-h-screen grid place-content-center p-6">
      <div className="card p-6 w-[360px] space-y-3">
        <h1 className="text-xl font-semibold">Connexion</h1>
        {error && <div className="text-red-600 text-sm">{error}</div>}
        <input className="input" placeholder="Utilisateur" value={u} onChange={e=>setU(e.target.value)}/>
        <input className="input" type="password" placeholder="Mot de passe" value={p} onChange={e=>setP(e.target.value)}/>
        <button className="btn btn-primary w-full" disabled={loading} onClick={async()=>{ setLoading(true); try { await onLogin(u,p); } finally { setLoading(false); } }}>Se connecter</button>
      </div>
    </div>
  );
}

function AddPdu({ onAdded, token }: { onAdded: (p:Pdu)=>void; token:string }){
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [ip, setIp] = useState("");
  const [model, setModel] = useState("IBM-42R8743");
  const [location, setLocation] = useState("");
  const [notes, setNotes] = useState("");
  const create = async () => {
    const r = await fetch(`${API}/pdus`, { method: "POST", headers: {"Content-Type":"application/json", Authorization: `Bearer ${token}`}, body: JSON.stringify({id, ip, model, location, notes})});
    if (!r.ok) return alert(await r.text());
    const p = await r.json(); onAdded(p); setOpen(false); setId(""); setIp(""); setModel("IBM-42R8743"); setLocation(""); setNotes("");
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
