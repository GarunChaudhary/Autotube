"use client";
import { useEffect, useMemo, useState } from "react";
import { api, fmtTime } from "../../lib/api";

const SORTS = [
  ["title_asc", "Name A → Z"], ["title_desc", "Name Z → A"], ["newest", "Newest first"], ["oldest", "Oldest first"],
  ["views", "Most views"], ["likes", "Most likes"], ["comments", "Most comments"],
];
const TYPES = [["all", "All videos"], ["short", "Shorts"], ["long", "Long videos"]];

const n = (x) => Number(x || 0).toLocaleString();
const dur = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
  : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);

export default function MyYouTube() {
  const [d, setD] = useState(null);
  const [profile, setProfile] = useState("");
  const [type, setType] = useState("all");
  const [sort, setSort] = useState("title_asc");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);

  async function load(p = profile, refresh = false) {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      if (p) qs.set("channel", p);
      if (refresh) qs.set("refresh", "true");
      setD(await api("/api/my-youtube" + (qs.toString() ? `?${qs}` : "")));
    } catch (e) { setD({ error: e.message, accounts: d?.accounts || [] }); }
    setLoading(false);
  }
  useEffect(() => { load(); }, []);

  const rows = useMemo(() => {
    let v = (d?.videos || []).filter((x) => type === "all" || x.kind === type);
    if (q.trim()) v = v.filter((x) => x.title.toLowerCase().includes(q.trim().toLowerCase()));
    const by = {
      title_asc: (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
      title_desc: (a, b) => b.title.localeCompare(a.title, undefined, { sensitivity: "base" }),
      newest: (a, b) => (b.published_at || "").localeCompare(a.published_at || ""),
      oldest: (a, b) => (a.published_at || "").localeCompare(b.published_at || ""),
      views: (a, b) => b.views - a.views, likes: (a, b) => b.likes - a.likes, comments: (a, b) => b.comments - a.comments,
    }[sort];
    return [...v].sort(by);
  }, [d, type, sort, q]);

  const c = d?.channel;
  const t = d?.totals;
  const sel = profile || d?.profile_id || "";

  return (
    <>
      <div className="topbar"><h1>My YouTube</h1>
        <div className="row">
          <label className="small muted" htmlFor="ch">Channel</label>
          <select id="ch" value={sel} onChange={(e) => { setProfile(e.target.value); load(e.target.value); }}>
            {(d?.accounts || []).map((a) => (
              <option key={a.id} value={a.id}>{a.youtube_title ? `${a.youtube_title} (${a.name})` : `${a.name} - not connected`}</option>))}
          </select>
          <button onClick={() => load(sel, true)} disabled={loading}>{loading ? "Loading…" : "Refresh from YouTube"}</button>
        </div></div>

      {d?.error && <div className="alert err">{d.error}</div>}
      {!d && <div className="muted">Loading your channel…</div>}

      {c && (
        <>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="row" style={{ gap: 14, alignItems: "center" }}>
              {c.thumbnail && <img src={c.thumbnail} alt="" width={56} height={56} style={{ borderRadius: "50%" }} />}
              <div><h2 style={{ margin: 0 }}><a href={c.url} target="_blank" rel="noreferrer">{c.title}</a></h2>
                <div className="small muted">Live from YouTube · updated {fmtTime(d.fetched_at)}</div></div>
            </div>
          </div>
          <div className="grid g4" style={{ marginBottom: 16 }}>
            <div className="card stat"><div className="l">Subscribers</div><div className="n">{n(c.subscribers)}</div></div>
            <div className="card stat"><div className="l">Total uploads</div><div className="n">{n(t.all.uploads)}</div></div>
            <div className="card stat"><div className="l">Total views</div><div className="n">{n(c.views)}</div></div>
            <div className="card stat"><div className="l">Likes · Comments</div><div className="n">{n(t.all.likes)} · {n(t.all.comments)}</div></div>
          </div>
          <div className="grid g2" style={{ marginBottom: 16 }}>
            {[["short", "Shorts"], ["long", "Long videos"]].map(([k, label]) => (
              <div key={k} className="card">
                <h2>{label}</h2>
                <div className="grid g4">
                  <div><div className="small muted">Uploads</div><b>{n(t[k].uploads)}</b></div>
                  <div><div className="small muted">Views</div><b>{n(t[k].views)}</b></div>
                  <div><div className="small muted">Likes</div><b>{n(t[k].likes)}</b></div>
                  <div><div className="small muted">Comments</div><b>{n(t[k].comments)}</b></div>
                </div>
              </div>))}
          </div>

          <div className="card table-wrap">
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
              <div className="row" style={{ gap: 6 }}>
                {TYPES.map(([k, label]) => (
                  <button key={k} className={type === k ? "primary" : ""} onClick={() => setType(k)}>
                    {label} ({k === "all" ? t.all.uploads : t[k].uploads})</button>))}
              </div>
              <div className="row" style={{ gap: 6 }}>
                <input placeholder="Search titles" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search titles" />
                <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
                  {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
              </div>
            </div>
            <table><thead><tr><th></th><th>Title</th><th>Type</th><th>Length</th><th>Published</th><th>Views</th><th>Likes</th><th>Comments</th><th>Privacy</th></tr></thead>
              <tbody>
                {rows.map((v) => (
                  <tr key={v.youtube_video_id}>
                    <td>{v.thumbnail && <img src={v.thumbnail} alt="" width={80} style={{ borderRadius: 6, display: "block" }} />}</td>
                    <td><a href={v.url} target="_blank" rel="noreferrer">{v.title}</a>
                      {v.autotube_id && <div className="small"><a href={`/video/?id=${v.autotube_id}`}>AutoTube #{v.autotube_id}</a></div>}</td>
                    <td><span className="badge" title={v.kind_source === "length" ? "Detected from length (≤ 3 min = Short)" : "Made by AutoTube"}>{v.kind === "short" ? "Short" : "Long"}</span></td>
                    <td>{dur(v.duration_sec)}</td>
                    <td className="small">{fmtTime(v.published_at)}</td>
                    <td>{n(v.views)}</td><td>{n(v.likes)}</td><td>{n(v.comments)}</td>
                    <td className="small">{v.privacy}</td>
                  </tr>))}
                {!rows.length && <tr><td colSpan={9} className="muted">No videos match.</td></tr>}
              </tbody></table>
            <div className="small muted" style={{ marginTop: 8 }}>Showing {rows.length} of {d.videos.length}. Data is cached for 10 minutes; “Refresh from YouTube” reloads it now.</div>
          </div>
        </>)}
    </>
  );
}
