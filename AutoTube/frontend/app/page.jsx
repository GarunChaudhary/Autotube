"use client";
import { useEffect, useState } from "react";
import { api, Badge, fmtTime, fmtTimeOnly, countdown, tzAbbrev, STAGES } from "../lib/api";

export default function Dashboard() {
  const [d, setD] = useState(null);
  const [h, setH] = useState(null);
  const [err, setErr] = useState("");
  const [, tick] = useState(0);

  const load = () => {
    api("/api/dashboard").then(setD).catch((e) => setErr(e.message));
    api("/api/health").then(setH).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 8000); const c = setInterval(() => tick((x) => x + 1), 1000);
    return () => { clearInterval(t); clearInterval(c); }; }, []);

  if (!d) return <div className="muted">{err || "Loading…"}</div>;
  const t = d.today, tz = t.timezone, s = d.settings, yq = d.quota.youtube, conn = d.connection;
  const next = t.next_upload || (t.next_slot && { scheduled_for: t.next_slot.at, title: t.next_slot.label, kind: t.next_slot.kind, status: "UPCOMING" });
  const modeLabel = { automatic: "Automatic", approval: "Approval", manual: "Manual" }[s.upload_mode];

  return (
    <>
      <div className="topbar">
        <div>
          <h1>Today</h1>
          <div className="muted small">{new Date().toLocaleDateString("en-IN", { timeZone: tz, weekday: "long", day: "numeric", month: "long" })} · {tzAbbrev(tz)}</div>
        </div>
        <div className="row">
          <span className={"badge " + (s.automation_enabled ? "b-ok" : "")}>generation {s.automation_enabled ? "on" : "off"}</span>
          <span className={"badge " + (s.auto_upload ? "b-ok" : "")}>uploading {s.auto_upload ? "on" : "off"}</span>
          <span className="badge b-info">{modeLabel} mode</span>
          <a className="btn" href="/scheduler/">Scheduler settings</a>
        </div>
      </div>

      {d.channels.length === 0 && <div className="alert warn">No channel yet. <a href="/channel/">Set up your channel & niche</a> to start.</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Daily production progress</h2><span className="spacer" />
          <span className="muted small">{t.short.uploaded + t.long.uploaded} of {t.short.target + t.long.target} published today</span>
        </div>
        <div className="progress"><div style={{ width: `${t.progress}%` }} /></div>
        <div className="grid g4" style={{ marginTop: 16 }}>
          <Stat label="Shorts target" value={t.short.target} sub={`${t.short.slots} slots today`} />
          <Stat label="Shorts generated" value={`${t.short.generated}/${t.short.target}`} sub={t.short.in_production ? `${t.short.in_production} in production` : ""} />
          <Stat label="Shorts uploaded" value={`${t.short.uploaded}/${t.short.target}`} />
          <Stat label="Long target" value={t.long.target} sub={`${t.long.slots} slot${t.long.slots === 1 ? "" : "s"} today`} />
          <Stat label="Long generated" value={`${t.long.generated}/${t.long.target}`} sub={t.long.in_production ? `${t.long.in_production} in production` : ""} />
          <Stat label="Long uploaded" value={`${t.long.uploaded}/${t.long.target}`} />
          <Stat label="Pending uploads" value={t.pending_uploads} sub={t.waiting_approval ? <a href="/queue/">{t.waiting_approval} need approval</a> : ""} />
          <Stat label="Failed jobs" value={t.failed_today} danger={t.failed_today > 0} sub={t.failed_today ? <a href="/queue/">review</a> : ""} />
        </div>
      </div>

      {conn.status === "connected" && !d.can_manage_videos && (
        <div className="alert warn"><b>One more permission needed:</b> to publish already-uploaded videos (Publish now) and verify privacy changes,
          {" "}<a href="/youtube/">reconnect YouTube</a> once and allow “Manage your YouTube videos”.</div>)}
      {d.thumbnails_unavailable > 0 && (
        <div className="alert warn"><b>Custom thumbnail unavailable</b> on {d.thumbnails_unavailable} video(s). If your channel is not eligible for custom
          thumbnails, verify your account and enable the required YouTube features: <a href="https://www.youtube.com/verify" target="_blank">https://www.youtube.com/verify</a>,
          {" "}then use <b>Retry thumbnail</b> in the <a href="/queue/">Upload queue</a>.</div>)}

      <div className="grid g2" style={{ marginBottom: 16 }}>
        {[["short", "Shorts", "1080×1920 · 9:16 · 30-60 s"], ["long", "Long videos", "1920×1080 · 16:9 · 6-12 min"]].map(([k, label, spec]) => {
          const b = d.by_kind[k];
          return (
            <div className="card" key={k}>
              <div className="row"><h2 style={{ margin: 0 }}>{label}</h2><span className="spacer" /><span className="small muted">{spec}</span></div>
              <div className="grid g4" style={{ marginTop: 12 }}>
                <Stat label="Generated" value={b.generated} sub={b.in_production ? `${b.in_production} in production` : ""} />
                <Stat label="Published (public)" value={b.published} sub={b.uploaded_not_public ? <a href="/queue/">{b.uploaded_not_public} uploaded but not public</a> : ""} />
                <Stat label="Scheduled" value={b.scheduled} />
                <Stat label="Failed" value={b.failed} danger={b.failed > 0} />
              </div>
            </div>);
        })}
      </div>

      <div className="grid g3" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Next upload</h2>
          {next ? (
            <>
              <div className="countdown">{countdown(next.scheduled_for)}</div>
              <div style={{ margin: "6px 0" }}>{next.id ? <a href={`/video/?id=${next.id}`}>{next.title || next.topic}</a> : next.title}</div>
              <div className="row small muted"><span>{next.kind === "short" ? "Short" : "Long video"}</span>·<span>{fmtTime(next.scheduled_for, tz)} {tzAbbrev(tz)}</span><Badge s={next.status} /></div>
            </>
          ) : <div className="muted">No upcoming slots. <a href="/scheduler/">Add time slots</a>.</div>}
        </div>

        <div className="card">
          <h2>Connection health</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            {conn.channel_thumbnail && <img src={conn.channel_thumbnail} alt="" width={36} height={36} style={{ borderRadius: "50%" }} />}
            <div>
              <b>{conn.channel_title || "YouTube"}</b>
              <div className="small muted">{conn.status === "connected" ? `token refreshed ${fmtTime(conn.last_refresh_at, tz)}` : conn.status === "reauth_required" ? "authorization expired: reconnect" : "not connected"}</div>
            </div>
          </div>
          {h && (<>
            <Check ok={h.ollama.running && h.ollama.model_ready} label="Local AI (Ollama)" />
            <Check ok={h.kokoro} label="Kokoro narration voice" />
            <Check ok={h.ffmpeg} label="FFmpeg render engine" />
            <Check ok={h.pexels_configured || h.pixabay_configured} optional label="Stock video keys" hint={!(h.pexels_configured || h.pixabay_configured) ? "archives only" : ""} />
            <div className="small muted">Disk free: {h.disk_free_gb} GB</div>
          </>)}
        </div>

        <div className="card">
          <h2>API quota today</h2>
          <Meter label="YouTube Data API" used={yq.used} limit={yq.limit} />
          {["gemini", "pexels", "pixabay"].map((p) => d.quota[p] && <Meter key={p} label={p[0].toUpperCase() + p.slice(1)} used={d.quota[p].used} limit={d.quota[p].limit} />)}
          <div className="small muted">YouTube quota resets at midnight Pacific (1:30 PM IST).</div>
        </div>
      </div>

      <div className="grid g2">
        <div className="card">
          <div className="row"><h2>Now producing</h2><span className="spacer" /><span className="small muted">{d.pipeline.worker_count} worker{d.pipeline.worker_count === 1 ? "" : "s"} · {d.pipeline.queue_length} queued</span></div>
          {d.pipeline.workers.length ? d.pipeline.workers.map((w) => (
            <div key={w.video_id} style={{ marginBottom: 10 }}>
              <a href={`/video/?id=${w.video_id}`}>Video #{w.video_id}</a> · <b>{w.stage}</b>
              <div className="pipeline" style={{ marginTop: 6 }}>
                {STAGES.slice(0, -1).map((st) => { const i = STAGES.indexOf(w.stage), j = STAGES.indexOf(st);
                  return <span key={st} className={j < i ? "done" : j === i ? "cur" : ""}>{st}</span>; })}
              </div>
            </div>)) : <div className="muted">Idle.</div>}
        </div>
        <div className="card">
          <div className="row"><h2>Recent uploads</h2><span className="spacer" /><a href="/queue/">Upload queue →</a></div>
          <table><tbody>
            {d.recent_uploads.map((u) => (
              <tr key={u.id}><td><a href={`/video/?id=${u.id}`}>{u.title}</a><div className="small muted">{u.kind} · {fmtTime(u.published_at, tz)}</div></td>
                <td><Badge s={u.status} /> <span className={"badge " + ((u.yt_privacy || u.privacy) === "public" ? "b-ok" : "b-err")}>YouTube: {u.yt_privacy || u.privacy}</span></td>
                <td>{u.youtube_url && <a href={u.youtube_url} target="_blank">Open ↗</a>}</td></tr>))}
            {!d.recent_uploads.length && <tr><td className="muted">No uploads yet.</td></tr>}
          </tbody></table>
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, sub, danger }) {
  return <div className="stat"><div className="l">{label}</div><div className="n" style={danger ? { color: "var(--err)" } : {}}>{value}</div>{sub && <div className="small muted">{sub}</div>}</div>;
}

function Check({ ok, label, hint, optional }) {
  const color = ok ? "var(--ok)" : optional ? "var(--warn)" : "var(--err)";
  return <div className="check"><span className="dot" style={{ background: color }} />{label}{hint && <span className="small muted">({hint})</span>}</div>;
}

function Meter({ label, used, limit }) {
  const pct = Math.min(100, Math.round((100 * used) / Math.max(1, limit)));
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row small"><span>{label}</span><span className="spacer" /><span className="muted">{used} / {limit}</span></div>
      <div className="progress thin"><div style={{ width: `${pct}%`, background: pct > 85 ? "var(--err)" : pct > 60 ? "var(--warn)" : "var(--accent-2)" }} /></div>
    </div>
  );
}
