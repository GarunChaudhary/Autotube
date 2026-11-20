"use client";
import { useEffect, useState } from "react";
import { api, Badge, fmtDur, fmtTime, STATUSES, STATUS_LABEL } from "../../lib/api";

const FILTERS = ["", ...STATUSES];

export default function Library() {
  const [videos, setVideos] = useState([]);
  const [status, setStatus] = useState("");

  const load = () => api("/api/videos" + (status ? `?status=${status}` : "")).then(setVideos).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 6000); return () => clearInterval(t); }, [status]);

  return (
    <>
      <div className="topbar">
        <h1>Video library</h1>
        <div className="row" style={{ width: 220 }}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {FILTERS.map((f) => <option key={f} value={f}>{f ? STATUS_LABEL[f] : "All statuses"}</option>)}
          </select>
        </div>
      </div>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>#</th><th>Thumb</th><th>Type</th><th>Title</th><th>Length</th><th>Quality</th><th>Status</th><th>YouTube</th><th>Created</th></tr></thead>
          <tbody>
            {videos.map((v) => {
              const q = v.quality_json ? JSON.parse(v.quality_json) : null;
              return (
                <tr key={v.id}>
                  <td>{v.id}</td>
                  <td style={{ width: 96 }}>
                    {v.thumbnail_path &&
                      <img className="thumb" src={`/api/videos/${v.id}/file/thumbnail`} alt="" />}
                  </td>
                  <td>{v.kind}</td>
                  <td><a href={`/video/?id=${v.id}`}>{v.title || v.topic}</a>
                    {v.error && <div className="small" style={{ color: "var(--err)" }}>{v.error.slice(0, 160)}</div>}</td>
                  <td>{fmtDur(v.duration_sec)}</td>
                  <td>{q ? q.score : "-"}</td>
                  <td><Badge s={v.status} /><div className="small muted">{v.stage}</div></td>
                  <td>{v.youtube_url ? <a href={v.youtube_url} target="_blank">{v.privacy}</a> : "-"}</td>
                  <td className="small muted">{fmtTime(v.created_at)}</td>
                </tr>
              );
            })}
            {!videos.length && <tr><td colSpan={9} className="muted">No videos.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
