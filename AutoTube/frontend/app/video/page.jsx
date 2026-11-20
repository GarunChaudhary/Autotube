"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, Badge, fmtDur, fmtTime, STAGES } from "../../lib/api";
import { PrivacyBadge, ThumbStatus, YtActions, ThumbnailHelp } from "../../components/YtStatus";

function VideoDetail() {
  const id = useSearchParams().get("id");
  const [v, setV] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [bust, setBust] = useState(Date.now());

  const load = () => api(`/api/videos/${id}`).then(setV).catch((e) => setMsg({ err: true, text: e.message }));
  useEffect(() => { if (id) { load(); const t = setInterval(load, 5000); return () => clearInterval(t); } }, [id]);

  async function act(path, body, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api(path, { method: "POST", body });
      setMsg({ text: r && r.youtube_video_id ? `Upload confirmed by YouTube: ${r.youtube_video_id} (${r.privacy})` : "Done." });
      setBust(Date.now()); load();
    } catch (e) { setMsg({ err: true, text: e.message }); }
    setBusy(false);
  }

  async function remove() {
    if (!confirm("Delete this video and its files from your PC? (Does not delete it from YouTube.)")) return;
    await api(`/api/videos/${id}`, { method: "DELETE" });
    location.href = "/library/";
  }

  if (!v) return <div className="muted">{msg?.text || "Loading…"}</div>;
  const script = v.script_json, fc = v.factcheck_json, q = v.quality_json, seo = v.seo_json;
  const si = STAGES.indexOf(v.stage);
  // playable as soon as the edit is rendered (thumbnail / SEO / quality steps don't change the MP4)
  const fresh = v.stage === "done" || si > STAGES.indexOf("editing");
  const working = ["GENERATING", "RENDERING", "QUALITY_CHECK"].includes(v.status);
  const rendered = !!v.video_path && (fresh || !working);
  const stale = rendered && !fresh;     // an older render kept while the video is being regenerated
  const pending = v.status === "DRAFT" ? "Waiting in the production queue - not made yet."
    : working ? `Being made right now - current step: ${v.stage}.`
    : v.status === "FAILED" ? "Production failed before the video was rendered (see the error above)."
    : "Not rendered yet.";

  return (
    <>
      <div className="topbar">
        <div>
          <h1>#{v.id} · {v.title || v.topic}</h1>
          <div className="row small muted" style={{ marginTop: 4 }}>
            <Badge s={v.status} />
            <span>{v.kind === "short" ? "Short 9:16" : "Long 16:9"}</span>
            <span>{fmtDur(v.duration_sec)}</span>
            <span>attempts {v.attempts}</span>
            {v.scheduled_for && <span>slot {fmtTime(v.scheduled_for)} IST</span>}{v.youtube_url && <a href={v.youtube_url} target="_blank">{v.youtube_url}</a>}
          </div>
        </div>
        <div className="row">
          {v.status === "WAITING_FOR_APPROVAL" && <button className="primary" disabled={busy} onClick={() => act(`/api/videos/${id}/approve`)}>Approve</button>}
          {v.status === "WAITING_FOR_APPROVAL" && <button disabled={busy} onClick={() => act(`/api/videos/${id}/reject`, null, "Reject this video? It will be paused and never uploaded.")}>Reject</button>}
          {v.status === "READY" && !v.youtube_video_id && <button className="primary" disabled={busy} onClick={() => act(`/api/videos/${id}/upload`, null, "Upload to YouTube now? Privacy: " + (v.target_privacy || "private") + ".")}>{busy ? "Uploading…" : "Upload now"}</button>}
          {v.status === "FAILED" && <button disabled={busy} onClick={() => act(`/api/videos/${id}/retry`, {})}>Retry</button>}
          {v.status === "PAUSED" && <button disabled={busy} onClick={() => act(`/api/videos/${id}/pause`, { paused: false })}>Resume</button>}
          {["READY", "WAITING_FOR_APPROVAL", "FAILED", "PAUSED"].includes(v.status) && <button disabled={busy} onClick={() => act(`/api/videos/${id}/retry`, { from_stage: "script" }, "Rewrite the script and re-render?")}>Regenerate</button>}
          {!v.youtube_video_id && <select style={{ width: "auto" }} value={v.target_privacy || "private"} onChange={(e) => api(`/api/videos/${id}/privacy`, { method: "PUT", body: { target_privacy: e.target.value } }).then((r) => { if (r.note) setMsg({ text: r.note }); load(); })}>
            <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select>}
          <button onClick={remove} disabled={busy || ["GENERATING", "RENDERING", "QUALITY_CHECK", "UPLOADING"].includes(v.status)}>Delete</button>
        </div>
      </div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      {v.error && <div className="alert err">{v.error}</div>}
      {v.youtube_video_id && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row"><h2 style={{ margin: 0 }}>On YouTube</h2><PrivacyBadge v={v} /><ThumbStatus v={v} />
            {v.yt_publish_at && <span className="small muted">goes public {fmtTime(v.yt_publish_at)} IST</span>}
            <span className="spacer" /><a href={v.youtube_url} target="_blank">{v.youtube_url}</a></div>
          <div className="small muted" style={{ margin: "6px 0 10px" }}>{v.yt_checked_at ? `Status confirmed by YouTube ${fmtTime(v.yt_checked_at)} IST` : "Status not checked yet"}</div>
          <YtActions v={v} onMsg={setMsg} onChange={load} />
          {v.thumbnail_status && v.thumbnail_status !== "set" && <div style={{ marginTop: 10 }}><ThumbnailHelp error={v.thumbnail_error} /></div>}
        </div>)}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="pipeline">
          {STAGES.slice(0, -1).map((s, j) => <span key={s} className={j < si || v.stage === "done" ? "done" : j === si && ["GENERATING", "RENDERING", "QUALITY_CHECK"].includes(v.status) ? "cur" : ""}>{s}</span>)}
        </div>
      </div>

      <div className="grid g2">
        <div className="card">
          <h2>Preview</h2>
          {rendered ? (
            <>
              {stale && <div className="alert" style={{ marginBottom: 10 }}>This is the previous render. A new version is being made ({v.stage}); it replaces this preview when editing finishes.</div>}
              <video controls preload="metadata" key={`${v.stage}-${bust}`} src={`/api/videos/${id}/file/video?b=${bust}`} style={v.kind === "short" ? { maxWidth: 360, display: "block", margin: "0 auto" } : {}} />
              <div className="row" style={{ marginTop: 10 }}>
                <a className="btn" href={`/api/videos/${id}/file/video`} download>Download MP4</a>
                <a className="btn" href={`/api/videos/${id}/file/srt`} download>Captions (.srt)</a>
                {v.youtube_url && <a className="btn" href={v.youtube_url} target="_blank">Open on YouTube</a>}
              </div>
            </>
          ) : <div className="muted">{pending}{v.scheduled_for && ` Scheduled slot: ${fmtTime(v.scheduled_for)} IST.`} The preview appears here automatically once it is rendered (this page refreshes every 5 seconds).</div>}
          {v.thumbnail_path && (<><h3>Thumbnail</h3><img className="thumb" src={`/api/videos/${id}/file/thumbnail?b=${bust}`} alt="thumbnail" /></>)}
        </div>

        <div className="card">
          <h2>Quality check</h2>
          {q ? (
            <>
              <div className="stat"><span className="n">{q.score}</span><span className="muted"> / 100</span>
                {q.approved_by_user && <span className="badge b-ok" style={{ marginLeft: 8 }}>approved by you</span>}</div>
              {q.blockers?.length > 0 && <div className="alert warn small">Needs your review: {q.blockers.join("; ")}</div>}
              <table><tbody>{Object.entries(q.parts).map(([k, val]) => <tr key={k}><td>{k}</td><td>{val}</td></tr>)}
                <tr><td>words</td><td>{q.words}</td></tr><tr><td>max similarity to past videos</td><td>{q.max_similarity}</td></tr>
                <tr><td>render</td><td>{q.render.resolution} · {q.render.size_mb} MB {q.render.ok ? "✓" : q.render.problems.join(", ")}</td></tr></tbody></table>
            </>
          ) : <div className="muted">Pending.</div>}
          {fc && (<><h3>Fact check ({fc.method})</h3>
            <div className="small">Score {fc.score} · {fc.rewritten} rewritten · {fc.removed} removed</div>
            {fc.results.filter((r) => !r.supported).map((r) => <div key={r.i} className="small" style={{ color: "var(--warn)" }}>Segment {r.i}: {r.problem}</div>)}</>)}
          {v.research_json?.sources && (<><h3>Sources</h3>{v.research_json.sources.map((s) => <div key={s.url}><a href={s.url} target="_blank">{s.title}</a></div>)}</>)}
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Script {script?.provider && <span className="badge">{script.provider}</span>}</h2>
          {script ? script.segments.map((s, i) => (
            <p key={i} style={{ margin: "0 0 10px" }}><span className="small muted">[{s.visual}]</span><br />{s.text}</p>
          )) : <div className="muted">Pending.</div>}
        </div>
        <div className="card">
          <h2>SEO</h2>
          {seo ? (<>
            <label>Title</label><div>{seo.title}</div>
            <label>Tags</label><div className="small">{seo.tags.join(", ")}</div>
            <label>Description</label><pre>{v.description}</pre>
          </>) : <div className="muted">Pending.</div>}
          {v.uploads?.length > 0 && (<><h3>Upload history</h3>
            {v.uploads.map((u) => <div key={u.id} className="small">{fmtTime(u.created_at)} · {u.status} {u.youtube_video_id} {u.privacy} {u.error}</div>)}</>)}
        </div>
      </div>

      <div className="card logs" style={{ marginTop: 16 }}>
        <h2>Activity log</h2>
        <table><tbody>{v.logs.map((l, i) => (
          <tr key={i}><td className="small muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(l.created_at)}</td>
            <td><span className={"badge " + (l.level === "ERROR" ? "b-err" : l.level === "WARNING" ? "b-warn" : "")}>{l.source}</span></td>
            <td className="mono">{l.message.slice(0, 600)}</td></tr>))}</tbody></table>
      </div>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<div className="muted">Loading…</div>}><VideoDetail /></Suspense>;
}
