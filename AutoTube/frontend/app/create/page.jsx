"use client";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import GenrePicker from "../../components/GenrePicker";

export default function Create() {
  const [channels, setChannels] = useState([]);
  const [cid, setCid] = useState("");
  const [kind, setKind] = useState("short");
  const [topic, setTopic] = useState("");
  const [genre, setGenre] = useState("");
  const [ideas, setIdeas] = useState([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    api("/api/channels").then((c) => { setChannels(c); if (c[0]) setCid(String(c[0].id)); });
  }, []);

  async function suggest() {
    setBusy("ideas"); setMsg(null);
    try { setIdeas((await api(`/api/channels/${cid}/suggest-topics?count=6${genre ? `&genre=${genre}` : ""}`, { method: "POST" })).topics); }
    catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy("");
  }

  async function create() {
    setBusy("create"); setMsg(null);
    try {
      const v = await api("/api/videos", { method: "POST", body: { channel_id: Number(cid), kind, topic, genre: genre || null } });
      setMsg({ text: <>Queued video #{v.id}: “{v.topic}”. <a href={`/video/?id=${v.id}`}>Watch progress →</a></> });
      setTopic("");
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy("");
  }

  async function batch() {
    setBusy("batch"); setMsg(null);
    try {
      const r = await api("/api/scheduler/plan-now", { method: "POST" });
      setMsg({ text: `Planned ${r.created.length} video(s) for upcoming schedule slots.` });
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy("");
  }

  if (!channels.length) return <div className="alert warn">Create a channel first on the <a href="/channel/">Channel & niche</a> page.</div>;

  return (
    <>
      <div className="topbar"><h1>Create video</h1></div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      <div className="grid g2">
        <div className="card">
          <h2>Single video</h2>
          <p className="small muted">Want it on YouTube right away instead of at a slot? Use <a href="/upload-now/">⚡ Upload now</a>.</p>
          <label>Channel</label>
          <select value={cid} onChange={(e) => setCid(e.target.value)}>
            {channels.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.niche})</option>)}
          </select>
          <label>Format</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="short">Short (9:16 vertical, ~45s)</option>
            <option value="long">Long video (16:9, channel length setting)</option>
          </select>
          <label>Story type (genre) - sets style, voice, music, title and search tags</label>
          <GenrePicker value={genre} onChange={(g) => { setGenre(g); setIdeas([]); }} />
          <label>Topic (leave empty to let the research agent pick a fresh one)</label>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. The Tunguska event" />
          <div className="row" style={{ marginTop: 14 }}>
            <button className="primary" onClick={create} disabled={!!busy}>{busy === "create" ? "Queuing…" : "Produce video"}</button>
            <button onClick={suggest} disabled={!!busy}>{busy === "ideas" ? "Researching…" : "Suggest topics"}</button>
          </div>
          {ideas.length > 0 && (
            <>
              <h3>Fresh topic ideas (duplicates filtered)</h3>
              {ideas.map((t) => (
                <div key={t} className="row" style={{ padding: "4px 0" }}>
                  <span>{t}</span><span className="spacer" />
                  <button onClick={() => setTopic(t)}>Use</button>
                </div>
              ))}
            </>
          )}
        </div>
        <div className="card">
          <h2>Scheduled production</h2>
          <p className="muted">Fills the upcoming upload slots from the <a href="/scheduler/">Scheduler</a> (default 3 Shorts + 1 long video per day, IST) within your daily limits. Each video is researched, written, fact-checked, rendered and quality-checked before its slot.</p>
          <button className="primary" onClick={batch} disabled={!!busy}>{busy === "batch" ? "Planning…" : "Plan upcoming slots now"}</button>
          <h3>How long does it take?</h3>
          <p className="small muted">On CPU: a Short takes roughly 2–5 minutes, a 6-minute long video roughly 10–25 minutes
            (AI writing + rendering). You can keep using the dashboard meanwhile.</p>
        </div>
      </div>
    </>
  );
}
