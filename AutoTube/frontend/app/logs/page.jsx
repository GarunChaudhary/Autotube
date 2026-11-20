"use client";
import { useEffect, useState } from "react";
import { api, fmtTime } from "../../lib/api";

export default function Logs() {
  const [logs, setLogs] = useState([]);
  const [level, setLevel] = useState("");
  const [notes, setNotes] = useState([]);
  const [reports, setReports] = useState([]);
  const [tab, setTab] = useState("logs");

  const load = () => {
    api("/api/logs" + (level ? `?level=${level}` : "")).then(setLogs);
    api("/api/notifications").then(setNotes);
    api("/api/reports").then(setReports);
  };
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [level]);

  return (
    <>
      <div className="topbar"><h1>Logs & reports</h1>
        <div className="row">
          {["logs", "notifications", "reports"].map((t) => <button key={t} className={tab === t ? "primary" : ""} onClick={() => setTab(t)}>{t}</button>)}
        </div>
      </div>
      {tab === "logs" && (
        <div className="card logs table-wrap">
          <div className="row" style={{ marginBottom: 10 }}>
            <div style={{ width: 200 }}><select value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="">All levels</option><option>ERROR</option><option>WARNING</option><option>INFO</option></select></div>
            <span className="small muted">Full log file: data/logs/autotube.log</span>
          </div>
          <table><thead><tr><th>Time</th><th>Level</th><th>Source</th><th>Video</th><th>Message</th></tr></thead><tbody>
            {logs.map((l) => (
              <tr key={l.id}><td className="small muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(l.created_at)}</td>
                <td><span className={"badge " + (l.level === "ERROR" ? "b-err" : l.level === "WARNING" ? "b-warn" : "")}>{l.level}</span></td>
                <td>{l.source}</td><td>{l.video_id ? <a href={`/video/?id=${l.video_id}`}>#{l.video_id}</a> : ""}</td>
                <td className="mono">{l.message.slice(0, 800)}</td></tr>))}
          </tbody></table>
        </div>
      )}
      {tab === "notifications" && (
        <div className="card">
          <div className="row"><h2>Notifications</h2><span className="spacer" /><button onClick={() => api("/api/notifications/read", { method: "POST" }).then(load)}>Mark all read</button></div>
          {notes.map((n) => (
            <div key={n.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)", opacity: n.read ? .6 : 1 }}>
              <span className={"badge " + (n.level === "ERROR" ? "b-err" : n.level === "WARNING" ? "b-warn" : "b-info")}>{n.level}</span>{" "}
              <b>{n.title}</b> <span className="small muted">{fmtTime(n.created_at)}</span>
              {n.body && <div className="small">{n.body}</div>}
            </div>))}
          {!notes.length && <div className="muted">No notifications.</div>}
        </div>
      )}
      {tab === "reports" && (
        <div className="card">
          <div className="row"><h2>Daily reports</h2><span className="spacer" /><button onClick={() => api("/api/reports/generate", { method: "POST" }).then(load)}>Generate today's report</button></div>
          {reports.map((r) => (
            <div key={r.day} style={{ marginBottom: 14 }}>
              <h3>{r.day}</h3>
              <div className="small">Videos: {r.report_json.videos.map((x) => `${x.n} ${x.kind} ${x.status}`).join(", ") || "none"} · errors: {r.report_json.errors}
                {" "}· YouTube quota: {r.report_json.quota.youtube.used}/{r.report_json.quota.youtube.limit}</div>
            </div>))}
          {!reports.length && <div className="muted">Reports are generated automatically at 23:30.</div>}
        </div>
      )}
    </>
  );
}
