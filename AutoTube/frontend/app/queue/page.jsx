"use client";
import { useEffect, useState } from "react";
import { api, Badge, fmtTime, tzAbbrev } from "../../lib/api";
import { PrivacyBadge, ThumbStatus, YtActions, ThumbnailHelp } from "../../components/YtStatus";

const PRODUCING = ["DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK"];

export default function Queue() {
  const [q, setQ] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = () => api("/api/queue").then(setQ).catch((e) => setMsg({ err: true, text: e.message }));
  useEffect(() => { load(); const t = setInterval(load, 6000); return () => clearInterval(t); }, []);

  async function act(id, path, method = "POST", body, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(id); setMsg(null);
    try {
      const r = await api(path, { method, body });
      setMsg({ text: r && r.youtube_video_id ? `YouTube confirmed upload ${r.youtube_video_id} (${r.privacy}${r.publish_at ? ", publishes " + fmtTime(r.publish_at, q.timezone) : ""})` : "Done." });
      load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy(null);
  }

  if (!q) return <div className="muted">{msg?.text || "Loading…"}</div>;
  const tz = q.timezone;
  const Row = ({ v }) => {
    const qj = v.quality_json ? JSON.parse(v.quality_json) : null;
    const done = !PRODUCING.includes(v.status);
    return (
      <tr>
        <td>{v.thumbnail_path ? <img className={"qthumb" + (v.kind === "short" ? " v" : "")} src={`/api/videos/${v.id}/file/thumbnail`} alt="" /> : <div className={"qthumb" + (v.kind === "short" ? " v" : "")} />}</td>
        <td style={{ maxWidth: 340 }}><a href={`/video/?id=${v.id}`}>{v.title || v.topic}</a>
          <div className="small muted">#{v.id} · {v.kind === "short" ? "Short" : "Long video"}{qj ? ` · quality ${qj.score}` : ""}</div>
          {qj?.blockers?.length > 0 && v.status === "WAITING_FOR_APPROVAL" && <div className="small" style={{ color: "var(--warn)" }}>Review: {qj.blockers.join("; ")}</div>}
          {v.error && <div className="small" style={{ color: v.status === "FAILED" ? "var(--err)" : "var(--muted)" }}>{v.error.slice(0, 200)}</div>}</td>
        <td className="small">{v.scheduled_for ? <>{fmtTime(v.scheduled_for, tz)}<div className="muted">{tzAbbrev(tz)}{v.rescheduled ? " · rescheduled" : ""}</div></> : <span className="muted">manual</span>}</td>
        <td><Badge s={v.status} />{PRODUCING.includes(v.status) && <div className="small muted">{v.stage}</div>}</td>
        <td className="small">{v.attempts > 0 && <div>production {v.attempts}x</div>}
          {v.upload_attempts > 0 && <div>upload {v.upload_attempts}x</div>}
          {v.next_retry_at && <div className="muted">next try {fmtTime(v.next_retry_at, tz)}</div>}
          {!v.attempts && !v.upload_attempts && <span className="muted">-</span>}</td>
        <td className="small"><PrivacyBadge v={v} /><div><ThumbStatus v={v} /></div></td>
        <td>{v.youtube_url ? <><a href={v.youtube_url} target="_blank">{v.youtube_url.replace("https://", "")}</a><div style={{ marginTop: 6 }}><YtActions v={v} onMsg={setMsg} onChange={load} /></div></> : <span className="muted">-</span>}</td>
        <td><div className="row" style={{ gap: 6 }}>
          {v.status === "WAITING_FOR_APPROVAL" && <button className="primary" disabled={busy === v.id} onClick={() => act(v.id, `/api/videos/${v.id}/approve`)}>Approve</button>}
          {v.status === "READY" && <button disabled={busy === v.id} onClick={() => act(v.id, `/api/videos/${v.id}/upload`, "POST", null, "Upload this video to YouTube now?")}>Upload now</button>}
          {["FAILED"].includes(v.status) && <button disabled={busy === v.id} onClick={() => act(v.id, `/api/videos/${v.id}/retry`, "POST", {})}>Retry</button>}
          {!["PAUSED", "UPLOADING", "PUBLISHED", "SCHEDULED"].includes(v.status) && done && <button disabled={busy === v.id} onClick={() => act(v.id, `/api/videos/${v.id}/pause`, "POST", { paused: true })}>Pause</button>}
          {v.status === "PAUSED" && <button disabled={busy === v.id} onClick={() => act(v.id, `/api/videos/${v.id}/pause`, "POST", { paused: false })}>Resume</button>}
        </div></td>
      </tr>
    );
  };

  return (
    <>
      <div className="topbar"><h1>Upload queue</h1>
        <div className="row"><span className="badge b-info">{q.mode} mode</span><a className="btn" href="/calendar/">Calendar</a></div></div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      <div className="card table-wrap" style={{ marginBottom: 16 }}>
        <h2>Pending ({q.pending.length})</h2>
        <table>
          <thead><tr><th></th><th>Video</th><th>Scheduled</th><th>Status</th><th>Retries</th><th>Privacy</th><th>YouTube</th><th>Actions</th></tr></thead>
          <tbody>{q.pending.map((v) => <Row key={v.id} v={v} />)}
            {!q.pending.length && <tr><td colSpan={8} className="muted">Queue is empty. Turn on automatic generation in the Scheduler, or create a video.</td></tr>}</tbody>
        </table>
      </div>
      <div className="card table-wrap">
        <h2>Uploaded to YouTube</h2>
        {q.done.some((v) => v.thumbnail_status === "unavailable") && <ThumbnailHelp />}
        <table>
          <thead><tr><th></th><th>Video</th><th>Slot</th><th>Status</th><th>Retries</th><th>Privacy</th><th>YouTube</th><th></th></tr></thead>
          <tbody>{q.done.map((v) => <Row key={v.id} v={v} />)}
            {!q.done.length && <tr><td colSpan={8} className="muted">Nothing uploaded yet. Only uploads confirmed by YouTube appear here.</td></tr>}</tbody>
        </table>
      </div>
    </>
  );
}
