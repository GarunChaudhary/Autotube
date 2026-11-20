"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Badge, fmtTime, STAGES } from "../../lib/api";

const compact = (x) => Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(x || 0);
const done = (v) => v.stage === "done" || ["READY", "WAITING_FOR_APPROVAL", "UPLOADING", "SCHEDULED", "PUBLISHED"].includes(v.status);
const istToUtc = (local) => new Date(`${local}:00+05:30`).toISOString().replace(/\.\d{3}Z$/, "Z");

function Copy({ text }) {
  const [ok, setOk] = useState(false);
  return <button type="button" className="small" onClick={() => { navigator.clipboard?.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500); }}>
    {ok ? "✓ Copied" : "Copy"}</button>;
}

function progress(v) {
  if (["PUBLISHED", "SCHEDULED", "UPLOADING", "READY", "WAITING_FOR_APPROVAL"].includes(v.status)) return 100;
  const i = STAGES.indexOf(v.stage);
  return i < 0 ? 0 : Math.round((i / (STAGES.length - 1)) * 100);
}

export default function Trends() {
  const [tab, setTab] = useState("short");             // short | long
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");            // active search ("" = viral right now)
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);
  const [pick, setPick] = useState(null);
  const formRef = useRef(null);

  // Shorts: most-viewed Shorts of the week. Long: YouTube's trending chart. Search: most-viewed of that type, 30 days.
  async function load(t = tab, s = search, refresh = false) {
    setLoading(true); setErr(null);
    try {
      const qs = s ? new URLSearchParams({ region: "US", mode: "search", q: s, duration: t })
        : new URLSearchParams({ region: "US", mode: t === "short" ? "shorts" : "trending" });
      if (refresh) qs.set("refresh", "true");
      setD(await api(`/api/trends?${qs}`));
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }
  useEffect(() => { load(); }, []);

  const rows = useMemo(() => (d?.videos || []).filter((x) => x.kind === tab).sort((a, b) => b.views_per_hour - a.views_per_hour), [d, tab]);
  const runSearch = (s) => { setSearch(s); setQ(s); load(tab, s); };
  const choose = (v) => { setPick(v); setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };

  return (
    <>
      <div className="topbar"><h1>🔥 Viral Videos · USA</h1>
        <button onClick={() => load(tab, search, true)} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button></div>

      <div ref={formRef}>{pick && <MakeForm v={pick} onClose={() => setPick(null)} />}</div>
      <TrendVideos />

      <div className="row" style={{ flexWrap: "wrap", gap: 8, margin: "16px 0 12px" }}>
        {[["short", "📱 Shorts"], ["long", "🎬 Long videos"]].map(([k, label]) => (
          <button key={k} className={tab === k ? "primary" : ""} style={{ fontSize: 15, padding: "8px 18px" }}
            onClick={() => { setTab(k); load(k, search); }}>{label}</button>))}
        <span className="spacer" />
        <form className="row" style={{ gap: 6, margin: 0 }} onSubmit={(e) => { e.preventDefault(); runSearch(q.trim()); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search viral videos" aria-label="Search viral videos" style={{ maxWidth: 200 }} />
          <button type="submit" disabled={loading}>{q.trim() ? "Search" : "Show all"}</button>
        </form>
      </div>
      {search && <div className="small muted" style={{ marginBottom: 10 }}>Most-viewed {tab === "short" ? "Shorts" : "long videos"} about “{search}”, last 30 days.</div>}

      {err && <div className="alert err">{err}</div>}
      {!d && !err && <div className="muted">Loading viral videos…</div>}

      <div className="trend-grid">
        {rows.map((v, i) => (
          <div key={v.youtube_video_id} className="card trend">
            <a href={v.url} target="_blank" rel="noreferrer" className="trend-thumb">
              {v.thumbnail && <img src={v.thumbnail} alt="" />}<span className="rank">#{i + 1}</span></a>
            <div style={{ padding: "10px 2px 0" }}>
              <a href={v.url} target="_blank" rel="noreferrer"><b>{v.title}</b></a>
              <div className="small muted" style={{ margin: "4px 0" }}>{compact(v.views)} views · {compact(v.views_per_hour)}/hour · {v.channel}</div>
              <button className="primary" style={{ marginTop: 8, width: "100%", justifyContent: "center" }} onClick={() => choose(v)}>
                Make like this</button>
            </div>
          </div>))}
      </div>
      {d && !rows.length && <div className="muted">No {tab === "short" ? "Shorts" : "long videos"} here right now. Try the other tab or a search.</div>}
    </>
  );
}

/* ------------------------------------------------ make a video like the chosen viral one */
function MakeForm({ v, onClose }) {
  const [channels, setChannels] = useState([]);
  const [cid, setCid] = useState("");
  const [format, setFormat] = useState(v.kind);
  const [topic, setTopic] = useState("");
  const [when, setWhen] = useState("now");
  const [at, setAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => { api("/api/channels").then((c) => { setChannels(c); if (c[0]) setCid(String(c[0].id)); }); }, []);
  useEffect(() => { setMsg(null); setTopic(""); setFormat(v.kind); }, [v.youtube_video_id]);

  async function go() {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/api/trends/make", { method: "POST", body: {
        channel_id: Number(cid), topic, kinds: format === "both" ? ["short", "long"] : [format], mode: when,
        when_local: when === "schedule" ? at : null, source: { youtube_video_id: v.youtube_video_id, title: v.title } } });
      setMsg({ warn: !!r.upload_warning, text: "✓ Started. AutoTube is picking the topic, title, description, hashtags and thumbnail; "
        + "the video appears in My videos below within about a minute. "
        + (when === "now" ? "It uploads as soon as it's finished." : when === "make" ? "It is only made; upload or schedule it when finished."
          : `It uploads at ${at.replace("T", " ")} IST.`) + (r.upload_warning ? ` Note: ${r.upload_warning}` : "") });
      [5000, 20000, 60000, 120000].forEach((ms) => setTimeout(() => window.dispatchEvent(new Event("trend-videos-changed")), ms));
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy(false);
  }

  return (
    <div className="card" style={{ marginBottom: 16, borderColor: "var(--accent-2)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Make like this</h2><button onClick={onClose}>Close</button></div>
      <div className="row" style={{ gap: 12, margin: "10px 0", alignItems: "center" }}>
        {v.thumbnail && <img src={v.thumbnail} alt="" style={{ width: 120, borderRadius: 8 }} />}
        <div className="small"><b>{v.title}</b><div className="muted">{compact(v.views)} views · same subject and hook style, your own original video</div></div>
      </div>
      {msg && <div className={"alert small" + (msg.err ? " err" : msg.warn ? " warn" : "")}>{msg.text}</div>}

      <label>Topic (optional)</label>
      <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Leave empty: AutoTube picks the best topic for you" />
      <div className="small muted" style={{ marginTop: 4 }}>Title, description, hashtags and thumbnail are written automatically.</div>

      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 12 }}>
        {[["short", "📱 Short"], ["long", "🎬 Long"], ["both", "📱+🎬 Both"]].map(([k, l]) => (
          <button key={k} className={format === k ? "primary" : ""} onClick={() => setFormat(k)}>{l}</button>))}
        {channels.length > 1 && (
          <select value={cid} onChange={(e) => setCid(e.target.value)} aria-label="Channel" style={{ maxWidth: 220 }}>
            {channels.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>)}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        {[["now", "⚡ Upload now"], ["schedule", "🗓 Schedule"], ["make", "💾 Just make"]].map(([k, l]) => (
          <button key={k} className={when === k ? "primary" : ""} onClick={() => setWhen(k)}>{l}</button>))}
        {when === "schedule" && <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} aria-label="Time (IST)" style={{ maxWidth: 220 }} />}
      </div>

      <button className="primary" style={{ marginTop: 14 }} onClick={go}
        disabled={busy || !cid || (when === "schedule" && !at)}>
        {busy ? "Starting…" : "Make video"}</button>
      {when === "schedule" && !at && <span className="small muted" style={{ marginLeft: 8 }}>Pick a date and time first.</span>}
    </div>
  );
}

/* ------------------------------------------------ progress of videos made here (like Upload now) */
function TrendVideos() {
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = () => api("/api/trends/videos").then(setData).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    window.addEventListener("trend-videos-changed", load);
    return () => { clearInterval(t); window.removeEventListener("trend-videos-changed", load); };
  }, []);

  async function approve(id) {
    try {
      const r = await api(`/api/videos/${id}/approve`, { method: "POST" });
      setMsg({ warn: !!r.upload_warning, text: r.upload_warning ? `Approved #${id}, but upload is waiting: ${r.upload_warning}.` : `Approved #${id}.` });
      load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
  }
  async function retry(id) {
    try { await api(`/api/videos/${id}/retry`, { method: "POST", body: {} }); load(); }
    catch (e) { setMsg({ err: true, text: e.message }); }
  }
  async function uploadNow(id) {
    setMsg({ text: `Uploading #${id}… (large files can take a minute)` });
    try { const r = await api(`/api/videos/${id}/upload`, { method: "POST" }); setMsg({ text: `#${id} uploaded: ${r.youtube_video_id || "done"}.` }); load(); }
    catch (e) { setMsg({ err: true, text: e.message }); }
  }
  async function schedule(id, local) {
    try {
      await api(`/api/videos/${id}/schedule`, { method: "PUT", body: { scheduled_for: istToUtc(local) } });
      setMsg({ text: `#${id} scheduled for ${local.replace("T", " ")} IST.` }); load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
  }

  if (!data || !data.videos.length) return null;
  return (
    <div className="card">
      <h2>My videos</h2>
      {data.blocked && <div className="alert warn small">Uploads are on hold right now: <b>{data.blocked}</b>.</div>}
      {!data.auto_upload && data.videos.some((v) => v.scheduled_for && !v.youtube_video_id) &&
        <div className="alert warn small">Scheduled videos need <b>Automatic uploading</b> on in <a href="/scheduler/">Scheduler</a>.</div>}
      {msg && <div className={"alert small" + (msg.err ? " err" : msg.warn ? " warn" : "")}>{msg.text}</div>}
      <div style={{ display: "grid", gap: 12 }}>
        {data.videos.map((v) => (
          <div key={v.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <a href={`/video/?id=${v.id}`}><b>#{v.id} {v.title || v.topic}</b></a>
              <span className="badge">{v.kind === "short" ? "Short" : "Long"}</span>
              <span className="badge">{v.scheduled_for ? `🗓 ${fmtTime(v.scheduled_for)}` : v.upload_immediately ? "⚡ upload now" : "💾 kept (not uploaded)"}</span>
              <Badge s={v.status} />
              <span className="spacer" />
              {v.status === "WAITING_FOR_APPROVAL" && <button className="primary" onClick={() => approve(v.id)}>Approve & upload</button>}
              {v.status === "FAILED" && <button onClick={() => retry(v.id)}>Retry</button>}
              {v.youtube_url && <a href={v.youtube_url} target="_blank" rel="noreferrer">Open on YouTube ↗</a>}
            </div>
            {!["PUBLISHED", "SCHEDULED", "FAILED", "PAUSED"].includes(v.status) && (
              <div style={{ marginTop: 6 }}>
                <div className="progress thin"><div style={{ width: progress(v) + "%" }} /></div>
                <div className="small muted" style={{ marginTop: 3 }}>
                  {v.status === "WAITING_FOR_APPROVAL" ? "Finished - waiting for your review."
                    : v.status === "READY" ? (v.scheduled_for ? `Finished - uploads at ${fmtTime(v.scheduled_for)}.`
                      : v.upload_immediately ? "Finished - uploading as soon as possible." : "Finished - kept in your library. Upload or schedule it below.")
                    : v.status === "UPLOADING" ? "Uploading to YouTube…" : `Step: ${v.stage}`}
                </div>
              </div>)}
            {v.status === "SCHEDULED" && <div className="small muted">Uploaded; YouTube makes it public at {fmtTime(v.yt_publish_at || v.scheduled_for)}.</div>}
            {v.status === "PUBLISHED" && <div className="small muted">Confirmed by YouTube: {v.yt_privacy || v.privacy}</div>}
            {v.error && v.status !== "PUBLISHED" && <div className="small" style={{ color: "var(--warn)", marginTop: 3 }}>{v.error}</div>}
            {done(v) && <UploadKit v={v} onUpload={uploadNow} onSchedule={schedule} />}
          </div>))}
      </div>
    </div>
  );
}

/* ------------------------------------------------ finished video: thumbnail, title, description, hashtags */
function UploadKit({ v, onUpload, onSchedule }) {
  const [open, setOpen] = useState(!v.youtube_video_id);
  const [at, setAt] = useState("");
  const kept = v.status === "READY" && !v.youtube_video_id && !v.scheduled_for && !v.upload_immediately;
  const tags = (v.hashtags || []).map((h) => `#${h}`).join(" ");
  return (
    <div style={{ marginTop: 8 }}>
      <button type="button" className="small" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} thumbnail, title, description & hashtags</button>
      {open && (
        <div className="kit">
          <div>
            <img src={`/api/videos/${v.id}/file/thumbnail`} alt={`Thumbnail for ${v.title || v.topic}`} className="kit-thumb" />
            <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
              <a className="btn small" href={`/api/videos/${v.id}/file/thumbnail`} download>Download thumbnail</a>
              <a className="btn small" href={`/api/videos/${v.id}/file/video`} download>Download video</a>
            </div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="small muted">Topic</div><div style={{ marginBottom: 8 }}>{v.topic}{v.genre ? ` · ${v.genre.replace("_", " ")}` : ""}</div>
            <div className="row" style={{ justifyContent: "space-between" }}><span className="small muted">Title</span><Copy text={v.title || ""} /></div>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{v.title}</div>
            {v.title_options?.length > 1 && <div className="small muted" style={{ marginBottom: 8 }}>Other options: {v.title_options.filter((t) => !(v.title || "").startsWith(t)).join(" · ")}</div>}
            <div className="row" style={{ justifyContent: "space-between" }}><span className="small muted">Description</span><Copy text={v.description || ""} /></div>
            <pre className="kit-desc">{v.description}</pre>
            <div className="row" style={{ justifyContent: "space-between" }}><span className="small muted">Hashtags</span><Copy text={tags} /></div>
            <div style={{ marginBottom: 8 }}>{(v.hashtags || []).map((h) => <span key={h} className="chip" style={{ marginRight: 4 }}>#{h}</span>)}</div>
            <div className="row" style={{ justifyContent: "space-between" }}><span className="small muted">Tags</span><Copy text={(v.tags || []).join(", ")} /></div>
            <div className="small" style={{ marginBottom: 8 }}>{(v.tags || []).join(", ")}</div>
            {kept && (
              <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                <button className="primary" onClick={() => onUpload(v.id)}>⚡ Upload now</button>
                <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} aria-label="Schedule time (IST)" style={{ maxWidth: 220 }} />
                <button disabled={!at} onClick={() => onSchedule(v.id, at)}>🗓 Schedule (IST)</button>
              </div>)}
          </div>
        </div>)}
    </div>);
}
