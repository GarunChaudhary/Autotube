"use client";
import { useEffect, useState } from "react";
import { api, Badge, STAGES } from "../../lib/api";
import GenrePicker from "../../components/GenrePicker";

const FORMATS = [
  ["short", "📱 Short", "9:16 vertical, ~45 s. Fastest to make (≈2–5 min on CPU) and fastest reach."],
  ["long", "🎬 Long video", "16:9, channel length setting. Builds watch time (≈10–25 min to make)."],
];

function progress(v) {
  if (["PUBLISHED", "SCHEDULED", "UPLOADING", "READY", "WAITING_FOR_APPROVAL"].includes(v.status)) return 100;
  const i = STAGES.indexOf(v.stage);
  return i < 0 ? 0 : Math.round((i / (STAGES.length - 1)) * 100);
}

export default function UploadNow() {
  const [channels, setChannels] = useState(null);
  const [cid, setCid] = useState("");
  const [kind, setKind] = useState("short");
  const [genre, setGenre] = useState("horror");
  const [topic, setTopic] = useState("");
  const [skipReview, setSkipReview] = useState(false);
  const [ideas, setIdeas] = useState([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState(null);
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);

  const load = () => api("/api/immediate").then(setData).catch(() => {});
  useEffect(() => {
    api("/api/channels").then((c) => { setChannels(c); if (c[0]) setCid(String(c[0].id)); });
    api("/api/health").then(setHealth).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function suggest() {
    setBusy("ideas"); setMsg(null); setIdeas([]);
    try {
      const q = genre ? `&genre=${genre}` : "";
      const t = (await api(`/api/channels/${cid}/suggest-topics?count=6${q}`, { method: "POST" })).topics;
      setIdeas(t);
      if (!t.length) setMsg({ warn: true, noIdeas: true, text: "No verified topic ideas found this time. Try again, or type a real event, person or place." });
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy("");
  }

  async function go() {
    setBusy("create"); setMsg(null);
    try {
      const v = await api("/api/videos", {
        method: "POST",
        body: { channel_id: Number(cid), kind, topic, genre: genre || null, upload_immediately: true, skip_review: skipReview },
      });
      setMsg({
        warn: !!v.upload_warning,
        text: <>Started #{v.id}: “{v.topic}”. It uploads to YouTube as soon as it is finished and passes the checks.
          {v.upload_warning && <> <b>Note: {v.upload_warning}</b> - it will wait until that is fixed.</>}{" "}
          <a href={`/video/?id=${v.id}`}>Watch progress →</a></>,
      });
      setTopic(""); setIdeas([]); load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy("");
  }

  async function approve(id) {
    try {
      const r = await api(`/api/videos/${id}/approve`, { method: "POST" });
      setMsg({ warn: !!r.upload_warning, text: r.upload_warning ? `Approved #${id}, but upload is waiting: ${r.upload_warning}.` : `Approved #${id} - uploading now.` });
      load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
  }

  async function retry(id) {
    try { await api(`/api/videos/${id}/retry`, { method: "POST", body: {} }); load(); }
    catch (e) { setMsg({ err: true, text: e.message }); }
  }

  if (channels && !channels.length) return <div className="alert warn">Create a channel first on the <a href="/channel/">Channel & niche</a> page.</div>;

  const blocked = data?.blocked;
  const approvalMode = data?.upload_mode === "approval";
  return (
    <>
      <div className="topbar"><h1>Upload now</h1></div>
      <p className="muted" style={{ marginTop: -10 }}>Make one Short or long video in the story genre you pick and upload it to YouTube
        the moment it is ready - no schedule slot needed. Every video is still researched, fact-checked and quality-checked first.</p>
      {blocked && <div className="alert warn">Uploads are on hold right now: <b>{blocked}</b>.
        {blocked.includes("YouTube") && <> <a href="/youtube/">Connect YouTube</a>.</>} You can still start videos; they upload once this is fixed.</div>}
      {health && !health.pexels_configured && !health.pixabay_configured && (
        <div className="alert warn">🎥 Videos currently use real archive film where it exists, otherwise moving photos.
          For real-world video footage in every scene, add the 2 free stock-video keys in <a href="/settings/">Providers & setup</a> (about 2 minutes, no payment).</div>
      )}
      {msg && <div className={"alert" + (msg.err ? " err" : msg.warn ? " warn" : "")}>{msg.text}</div>}

      <div className="card">
        <h2>1. Format</h2>
        <div className="fmt">
          {FORMATS.map(([k, label, desc]) => (
            <button key={k} type="button" className={"mode" + (kind === k ? " on" : "")} onClick={() => setKind(k)}>
              <b>{label} {kind === k && "✓"}</b><span className="small muted">{desc}</span>
            </button>
          ))}
        </div>

        <h2 style={{ marginTop: 20 }}>2. Story type</h2>
        <p className="small muted" style={{ marginTop: -6 }}>The genre sets the storytelling style, voice, music mood, title style and
          search tags/hashtags viewers of that genre use - for faster discovery. Stories stay true and sourced.</p>
        <GenrePicker value={genre} onChange={(g) => { setGenre(g); setIdeas([]); }} />

        <h2 style={{ marginTop: 20 }}>3. Topic</h2>
        {channels && channels.length > 1 && (
          <>
            <label>Channel</label>
            <select value={cid} onChange={(e) => setCid(e.target.value)}>
              {channels.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.niche})</option>)}
            </select>
          </>
        )}
        <label>Topic (leave empty and the most in-demand fresh story for this genre is used)</label>
        <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. The Dyatlov Pass incident" />
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={suggest} disabled={!!busy || !cid}>{busy === "ideas" ? "Finding real stories…" : "Suggest topics for this genre"}</button>
          <span className="small muted">Real Wikipedia stories, ranked by how many people looked them up in the last 30 days (audience demand). Prepared in the background, so this is usually instant.</span>
        </div>        {ideas.length > 0 && ideas.map((t) => (
          <div key={t} className="row" style={{ padding: "4px 0" }}>
            <span>{t}</span><span className="spacer" />
            <button onClick={() => setTopic(t)}>{topic === t ? "✓ Selected" : "Use"}</button>
          </div>
        ))}

        {approvalMode && (
          <label className="toggle" style={{ marginTop: 14 }}>
            <input type="checkbox" checked={skipReview} onChange={(e) => setSkipReview(e.target.checked)} />
            <span>Skip my manual review for this video <b>only if it passes every quality check</b>.
              <span className="small muted"> (Your upload mode is “approval”. Videos with any quality problem always wait for you.)</span></span>
          </label>
        )}

        <div className="row" style={{ marginTop: 16 }}>
          <button className="primary" onClick={go} disabled={!!busy || !cid}>
            {busy === "create" ? "Starting…" : `Make & upload ${kind === "short" ? "Short" : "long video"} now`}
          </button>
          <span className="small muted">Uploads as public, right after it is finished.</span>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Upload-now videos</h2>
        {!data ? <p className="muted">Loading…</p> : !data.videos.length ? <p className="muted">None yet.</p> : (
          <div style={{ display: "grid", gap: 12 }}>
            {data.videos.map((v) => (
              <div key={v.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <div className="row">
                  <a href={`/video/?id=${v.id}`}><b>#{v.id} {v.title || v.topic}</b></a>
                  <span className="badge">{v.kind === "short" ? "Short" : "Long"}</span>
                  {v.genre && <span className="badge">{v.genre.replace("_", " ")}</span>}
                  <Badge s={v.status} />
                  <span className="spacer" />
                  {v.status === "WAITING_FOR_APPROVAL" && <button className="primary" onClick={() => approve(v.id)}>Review done - approve & upload</button>}
                  {v.status === "FAILED" && <button onClick={() => retry(v.id)}>Retry</button>}
                  {v.youtube_url && <a href={v.youtube_url} target="_blank" rel="noreferrer">Open on YouTube ↗</a>}
                </div>
                {!["PUBLISHED", "SCHEDULED", "FAILED", "PAUSED"].includes(v.status) && (
                  <div style={{ marginTop: 6 }}>
                    <div className="progress thin"><div style={{ width: progress(v) + "%" }} /></div>
                    <div className="small muted" style={{ marginTop: 3 }}>
                      {v.status === "WAITING_FOR_APPROVAL" ? "Finished - waiting for your review (open it to watch first)."
                        : v.status === "READY" ? "Finished - uploading as soon as possible."
                        : v.status === "UPLOADING" ? "Uploading to YouTube…" : `Step: ${v.stage}`}
                    </div>
                  </div>
                )}
                {v.status === "PUBLISHED" && <div className="small muted">Confirmed by YouTube: {v.yt_privacy || v.privacy}</div>}
                {v.error && v.status !== "PUBLISHED" && <div className="small" style={{ color: "var(--warn)", marginTop: 3 }}>{v.error}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
