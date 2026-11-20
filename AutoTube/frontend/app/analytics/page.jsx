"use client";
import { useEffect, useState } from "react";
import { api, fmtTime } from "../../lib/api";

export default function Analytics() {
  const [a, setA] = useState(null);
  const [msg, setMsg] = useState(null);
  const [profile, setProfile] = useState("");
  const [g, setG] = useState(null);
  const load = (p = profile) => {
    api("/api/analytics" + (p ? `?channel=${p}` : "")).then(setA).catch((e) => setMsg({ err: true, text: e.message }));
    api("/api/growth" + (p ? `?channel=${p}` : "")).then(setG).catch(() => setG(null));
  };
  async function setGenres(list) {
    try { await api("/api/settings", { method: "PUT", body: { values: { growth_genres: list } } }); load(); }
    catch (e) { setMsg({ err: true, text: e.message }); }
  }
  useEffect(() => { load(); }, []);

  async function refresh() {
    try { const r = await api("/api/analytics/refresh", { method: "POST" }); setMsg({ text: `Updated ${r.updated} videos.` }); load(); }
    catch (e) { setMsg({ err: true, text: e.message }); }
  }

  const ch = a?.channel;
  const idx = (n) => ch?.columns.indexOf(n);
  const rows = ch?.rows || [];
  const total = (n) => rows.reduce((s, r) => s + (r[idx(n)] || 0), 0);
  const maxViews = Math.max(1, ...rows.map((r) => r[idx("views")] || 0));

  return (
    <>
      <div className="topbar"><h1>Analytics</h1>
        <div className="row">
          {a?.profiles?.length > 1 && (
            <select value={profile || a.profile_id} onChange={(e) => { setProfile(e.target.value); load(e.target.value); }} aria-label="YouTube account">
              {a.profiles.map((p) => <option key={p.id} value={p.id}>{p.youtube_title ? `${p.name} (${p.youtube_title})` : p.name}</option>)}
            </select>)}
          <button onClick={() => refresh()}>Refresh video stats</button>
        </div></div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      {a?.channel_error && <div className="alert">Channel analytics unavailable: {a.channel_error}. Connect YouTube first.</div>}
      {ch && (
        <>
          <div className="grid g4" style={{ marginBottom: 16 }}>
            <div className="card stat"><div className="l">Views (28d)</div><div className="n">{total("views").toLocaleString()}</div></div>
            <div className="card stat"><div className="l">Watch minutes</div><div className="n">{Math.round(total("estimatedMinutesWatched")).toLocaleString()}</div></div>
            <div className="card stat"><div className="l">Subscribers gained</div><div className="n">{total("subscribersGained")}</div></div>
            <div className="card stat"><div className="l">Likes</div><div className="n">{total("likes")}</div></div>
          </div>
          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Daily views, last 28 days</h2>
            <svg viewBox={`0 0 ${Math.max(1, rows.length) * 22} 140`} style={{ width: "100%", height: 160 }} role="img" aria-label="Daily views bar chart">
              {rows.map((r, i) => {
                const h = (r[idx("views")] / maxViews) * 120;
                return <rect key={i} x={i * 22 + 3} y={130 - h} width={16} height={Math.max(1, h)} rx={3} fill="var(--accent-2)"><title>{r[0]}: {r[idx("views")]} views</title></rect>;
              })}
            </svg>
          </div>
        </>
      )}
      {g && <Growth g={g} setGenres={setGenres} />}
      <div className="card table-wrap">
        <h2>Uploaded videos</h2>
        <table><thead><tr><th>Video</th><th>Type</th><th>Views</th><th>Likes</th><th>Comments</th><th>Fetched</th></tr></thead><tbody>
          {a?.videos.map((v) => (
            <tr key={v.youtube_video_id}><td><a href={`/video/?id=${v.id}`}>{v.title}</a></td><td>{v.kind}</td>
              <td>{v.views}</td><td>{v.likes}</td><td>{v.comments}</td><td className="small muted">{fmtTime(v.fetched_at)}</td></tr>))}
          {!a?.videos.length && <tr><td colSpan={6} className="muted">No stats yet. Stats refresh automatically every 6 hours after uploads.</td></tr>}
        </tbody></table>
      </div>
    </>
  );
}

const num = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString());

function Bar({ label, have, need }) {
  const pct = have === null || have === undefined ? 0 : Math.min(100, (100 * have) / need);
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}><span className="small">{label}</span>
        <span className="small muted">{num(have)} / {num(need)}</span></div>
      <div className="progress thin"><div style={{ width: `${pct}%` }} /></div>
    </div>);
}

function Growth({ g, setGenres }) {
  const m = g.monetization;
  const cands = new Set(g.candidate_genres);
  const toggle = (k) => {
    const next = cands.has(k) ? g.candidate_genres.filter((x) => x !== k) : [...g.candidate_genres, k];
    if (next.length) setGenres(next);
  };
  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Monetization progress (YouTube Partner Program)</h2>
        {g.monetization_error && <div className="alert small">Unavailable: {g.monetization_error}</div>}
        {m && (
          <div className="grid g2">
            {Object.entries(m.tiers).map(([k, t]) => (
              <div key={k}>
                <b>{t.label}</b> {t.eligible ? <span className="badge ok">eligible - apply in YouTube Studio</span> : null}
                <div style={{ marginTop: 8 }}>
                  <Bar label="Subscribers" have={t.subs.have} need={t.subs.need} />
                  {t.uploads_90d && <Bar label="Public uploads (90 days)" have={t.uploads_90d.have} need={t.uploads_90d.need} />}
                  <Bar label="Long-video watch hours (365 days)" have={t.watch_hours.have} need={t.watch_hours.need} />
                  <div className="small muted" style={{ margin: "-4px 0 6px" }}>or</div>
                  <Bar label="Shorts views (90 days)" have={t.shorts_views_90d.have} need={t.shorts_views_90d.need} />
                </div>
              </div>))}
          </div>)}
        {m?.analytics_error && <div className="small muted">Watch hours / Shorts views unavailable: {m.analytics_error}</div>}
        <div className="small muted" style={{ marginTop: 8 }}>Numbers come from YouTube Analytics and are close to what YouTube counts for the
          Partner Program (it uses public videos only). Mass-produced or repetitive videos can be refused under YouTube&apos;s
          &quot;inauthentic content&quot; policy, so originality and variety matter as much as volume.</div>
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <div className="card table-wrap">
          <h2>What works: genres</h2>
          <table><thead><tr><th>Genre</th><th>Type</th><th>Videos</th><th>Avg views</th><th>Views/day</th><th>Like rate</th></tr></thead><tbody>
            {["short", "long"].flatMap((kind) => g.genres[kind].map((r) => (
              <tr key={kind + r.genre}><td>{r.genre}</td><td>{kind}</td><td>{r.videos_with_stats}/{r.videos_made}</td>
                <td>{num(r.avg_views)}</td><td>{r.avg_views_per_day}</td><td>{(r.avg_like_rate * 100).toFixed(1)}%</td></tr>)))}
            {!g.genres.short.length && !g.genres.long.length && <tr><td colSpan={6} className="muted">No genre data yet.</td></tr>}
          </tbody></table>
          <div style={{ marginTop: 12 }}><b className="small">Genres the scheduler may use</b>
            <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              {g.all_genres.map((x) => (
                <button key={x.key} className={cands.has(x.key) ? "primary" : ""} onClick={() => toggle(x.key)}>{x.emoji} {x.label}</button>))}
            </div>
            <div className="small muted" style={{ marginTop: 6 }}>Keep a focused set: a consistent niche turns viewers into subscribers.</div>
          </div>
        </div>
        <div className="card">
          <h2>Best posting times</h2>
          {g.hours.recommended
            ? <div className="alert small">Your videos do best when they go live at <b>{g.hours.recommended.join(", ")}</b> ({g.hours.timezone}). Consider moving slots there.</div>
            : <div className="small muted">Needs {g.hours.needed} videos that are 2+ days old (have {g.hours.videos_counted}) before recommending times.</div>}
          <table style={{ marginTop: 8 }}><thead><tr><th>Go-live hour</th><th>Videos</th><th>Views/day</th></tr></thead><tbody>
            {g.hours.hours.map((h) => <tr key={h.hour}><td>{h.hour}</td><td>{h.videos}</td><td>{h.avg_views_per_day}</td></tr>)}
          </tbody></table>
          <h2 style={{ marginTop: 16 }}>Top titles (used as style examples)</h2>
          {["short", "long"].map((k) => g.top_titles[k].length ? (
            <div key={k} className="small"><b>{k}</b><ul>{g.top_titles[k].map((t) => <li key={t}>{t}</li>)}</ul></div>) : null)}
          {!g.top_titles.short.length && !g.top_titles.long.length && <div className="small muted">Needs 4+ videos with views per format.</div>}
        </div>
      </div>
    </>);
}
