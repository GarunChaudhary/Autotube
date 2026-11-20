"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";

const EMPTY = { name: "", niche: "", audience: "", tone: "curious, warm, energetic", long_minutes: 6 };

export default function Channel() {
  const [channels, setChannels] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [editId, setEditId] = useState(null);
  const [msg, setMsg] = useState(null);

  const load = () => api("/api/channels").then((c) => {
    setChannels(c);
    if (c[0] && editId === null) { setEditId(c[0].id); setForm(pick(c[0])); }
  });
  useEffect(() => { load(); }, []);

  const pick = (c) => Object.fromEntries(Object.keys(EMPTY).map((k) => [k, c[k]]));
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });

  async function save() {
    setMsg(null);
    try {
      if (!form.name || !form.niche) throw new Error("Name and niche are required");
      const r = editId ? await api(`/api/channels/${editId}`, { method: "PUT", body: form })
        : await api("/api/channels", { method: "POST", body: form });
      setEditId(r.id); setMsg({ text: "Saved." }); load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
  }

  return (
    <>
      <div className="topbar"><h1>Channel & niche</h1>
        <div className="row">
          {channels.map((c) => <button key={c.id} className={c.id === editId ? "primary" : ""} onClick={() => { setEditId(c.id); setForm(pick(c)); }}>{c.name}</button>)}
          <button onClick={() => { setEditId(null); setForm(EMPTY); }}>+ New channel profile</button>
        </div>
      </div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      <div className="grid g2">
        <div className="card">
          <h2>{editId ? "Edit channel profile" : "New channel profile"}</h2>
          <label>Channel name</label><input value={form.name} onChange={set("name")} placeholder="My Channel" />
          <label>Niche (what the research agent searches for)</label>
          <input value={form.niche} onChange={set("niche")} placeholder="e.g. unsolved history mysteries, space science, weird animals" />
          <label>Target audience</label><input value={form.audience} onChange={set("audience")} placeholder="e.g. curious teens and adults who love trivia" />
          <label>Voice & tone</label><input value={form.tone} onChange={set("tone")} />
          <label>Long video length (minutes)</label><input type="number" min={2} max={20} value={form.long_minutes} onChange={set("long_minutes")} />
          <p className="small muted">Daily Shorts / long video counts and upload times are set on the <a href="/scheduler/">Scheduler</a> page.</p>
          <div style={{ marginTop: 14 }}><button className="primary" onClick={save}>Save</button></div>
        </div>
        <div className="card">
          <h2>Picking a niche that works</h2>
          <ul className="muted">
            <li>Choose a <b>factual, research-friendly</b> niche: history, science, nature, geography, inventions, mysteries.
              Every script is grounded in Wikipedia sources and fact-checked.</li>
            <li>Be specific: “forgotten engineering disasters” beats “facts”.</li>
            <li>YouTube’s policies demonetise “mass-produced or repetitive” content. AutoTube checks topics for duplicates and
              scripts for similarity, but <b>review videos before publishing</b> and add your own perspective when you can.</li>
            <li>Uploads disclose synthetic (AI) narration via the API's <code>containsSyntheticMedia</code> flag.</li>
          </ul>
        </div>
      </div>
    </>
  );
}
