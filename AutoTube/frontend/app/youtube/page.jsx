"use client";
import { useEffect, useState } from "react";
import { api, fmtTime } from "../../lib/api";

export default function YouTube() {
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState(null);
  const [wizard, setWizard] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newNiche, setNewNiche] = useState("");

  const load = () => api("/api/youtube/status").then(setS).catch((e) => setMsg({ err: true, text: e.message }));
  useEffect(() => {
    load();
    const p = new URLSearchParams(location.search);
    if (p.get("connected")) setMsg({ text: "YouTube account connected. The authorization is saved securely and renews itself; you won't need to sign in again." });
    if (p.get("error")) setMsg({ err: true, text: "Google sign-in did not complete: " + p.get("error") });
  }, []);

  async function test(a) {
    try {
      await api(`/api/youtube/test?channel=${a.profile_id}`, { method: "POST" });
      setMsg({ text: `${a.channel_title || a.profile_name}: connection OK. Google issued a fresh access token just now.` }); load();
    } catch (e) { setMsg({ err: true, text: `${a.profile_name}: ${e.message}` }); }
  }
  async function disconnect(a) {
    if (!confirm(`Disconnect "${a.channel_title}" from the profile "${a.profile_name}"? AutoTube's access is revoked at Google and this profile's uploads stop until you connect again.`)) return;
    await api(`/api/youtube/disconnect?channel=${a.profile_id}`, { method: "POST" });
    setMsg({ text: `Disconnected ${a.channel_title}.` }); load();
  }
  async function addProfile() {
    if (!newName.trim() || !newNiche.trim()) return;
    try {
      const ch = await api("/api/channels", { method: "POST", body: { name: newName.trim(), niche: newNiche.trim() } });
      location.href = `/api/youtube/connect?channel=${ch.id}`;          // straight to Google sign-in for the new profile
    } catch (e) { setMsg({ err: true, text: e.message }); }
  }

  if (!s) return <div className="muted">{msg?.text || "Loading…"}</div>;
  const accounts = s.accounts || [];
  const showWizard = s.setup_required || wizard;
  const nConnected = accounts.filter((a) => a.connected).length;

  return (
    <>
      <div className="topbar"><h1>YouTube accounts</h1>
        {!showWizard && <span className="muted small">{nConnected} of {accounts.length} profile{accounts.length === 1 ? "" : "s"} connected</span>}</div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}

      {showWizard ? <SetupWizard s={s} onDone={() => { setWizard(false); load(); setMsg({ text: "Setup saved. Now click Connect YouTube." }); }}
        onCancel={s.setup_required ? null : () => setWizard(false)} /> : (
        <>
          <p className="muted small" style={{ marginTop: 0 }}>Each channel profile uploads to its own YouTube account. Videos, time slots and topics
            belong to a profile, so every account gets its own content.</p>
          {!accounts.length && <div className="alert warn">Create a channel profile first (below), then connect it.</div>}
          <div className="grid g2">
            {accounts.map((a) => <AccountCard key={a.profile_id} a={a} onTest={() => test(a)} onDisconnect={() => disconnect(a)} />)}
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            {!adding ? (
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div><b>Add another YouTube account</b>
                  <div className="small muted">Creates a new channel profile (its own niche and time slots) and opens Google sign-in.</div></div>
                <button className="primary" onClick={() => setAdding(true)}>+ Add YouTube account</button>
              </div>
            ) : (
              <>
                <h2>New channel profile</h2>
                <div className="grid g2">
                  <div><label>Profile name</label><input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Space Facts" /></div>
                  <div><label>Niche (what this channel is about)</label><input value={newNiche} onChange={(e) => setNewNiche(e.target.value)} placeholder="e.g. space science and astronomy" /></div>
                </div>
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="primary" disabled={!newName.trim() || !newNiche.trim()} onClick={addProfile}>Create and connect YouTube</button>
                  <button onClick={() => setAdding(false)}>Cancel</button>
                </div>
                <p className="small muted" style={{ marginTop: 10 }}>On Google's page, pick the account (or brand channel) for this profile. If your Google app is
                  still in “Testing” mode, first add that Google account as a <b>test user</b> in Google Auth Platform → Audience.</p>
              </>
            )}
          </div>

          <div className="alert small" style={{ marginTop: 16 }}>
            <b>Shared upload limit:</b> all accounts use the same Google app, which gets 10,000 YouTube API units per day. One upload costs about
            1,650 units, so expect roughly <b>6 uploads per day in total</b> across all accounts. To raise it, request more quota from Google.</div>

          <details className="card" style={{ marginTop: 16 }}>
            <summary><b>Advanced</b> <span className="small muted">(Google app credentials, redirect URI)</span></summary>
            <table className="small" style={{ marginTop: 10 }}><tbody>
              <tr><td>OAuth client ID</td><td className="mono">{s.client_id_masked || "-"}</td></tr>
              <tr><td>Redirect URI</td><td className="mono">{s.redirect_uri}</td></tr>
              <tr><td>Stored in</td><td className="mono">{s.env_file} (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)</td></tr>
            </tbody></table>
            <button style={{ marginTop: 10 }} onClick={() => setWizard(true)}>Change Google app credentials…</button>
          </details>
        </>
      )}
    </>
  );
}

function AccountCard({ a, onTest, onDisconnect }) {
  const connectUrl = `/api/youtube/connect?channel=${a.profile_id}`;
  return (
    <div className="card yt-hero" style={{ padding: 20 }}>
      <div className="small muted">Profile: <b>{a.profile_name}</b>{a.profile_niche ? ` · ${a.profile_niche}` : ""}</div>
      {a.status === "connected" && a.channel_id ? (
        <>
          {(a.details?.thumbnail || a.channel_thumbnail) ? <img src={a.details?.thumbnail || a.channel_thumbnail} alt="" className="yt-avatar" /> : <div className="yt-logo">▶</div>}
          <div className="yt-name">{a.details?.title || a.channel_title}</div>
          {a.details && (
            <div className="small" style={{ margin: "4px 0 8px" }}>
              {a.details.handle && <div className="muted">{a.details.handle}</div>}
              <div><b>{a.details.subscribers_hidden ? "hidden" : Number(a.details.subscribers).toLocaleString()}</b> subscribers
                {" · "}<b>{Number(a.details.videos).toLocaleString()}</b> videos
                {" · "}<b>{Number(a.details.views).toLocaleString()}</b> views</div>
              <div className="muted">{a.details.country ? `${a.details.country} · ` : ""}created {fmtTime(a.details.created_at)} · channel ID <span className="mono">{a.details.id}</span></div>
            </div>)}
          <div className="row" style={{ justifyContent: "center" }}><span className="badge b-ok">connected</span>
            <a className="small" href={`https://www.youtube.com/channel/${a.channel_id}`} target="_blank">View channel ↗</a></div>
          {!a.can_manage_videos && <div className="alert warn small" style={{ margin: "12px auto 0" }}>
            Click <b>Reconnect</b> once and allow <b>“Manage your YouTube videos”</b>. It's needed for Publish now and privacy changes.</div>}
          <div className="small muted" style={{ marginTop: 6 }}>Signed in {fmtTime(a.connected_at)}{a.last_refresh_at ? ` · renewed ${fmtTime(a.last_refresh_at)}` : ""}</div>
          {a.last_error && <div className="small" style={{ color: "var(--err)", marginTop: 4 }}>{a.last_error}</div>}
          <div className="row" style={{ justifyContent: "center", marginTop: 14 }}>
            <a className="btn" href={connectUrl}>Reconnect</a>
            <button onClick={onTest}>Test</button>
            <button onClick={onDisconnect}>Disconnect</button>
          </div>
        </>
      ) : (
        <>
          <div className="yt-logo">▶</div>
          <div className="yt-name">{a.status === "reauth_required" ? "Reconnect needed" : "Not connected"}</div>
          <p className="muted small" style={{ margin: "6px auto 14px" }}>
            {a.status === "reauth_required"
              ? "Google no longer accepts the saved authorization (revoked, or the Google app is in Testing mode). Sign in once more."
              : "Opens Google's sign-in page. Choose the account or brand channel for this profile and allow the permissions."}
          </p>
          <a className="btn primary" href={connectUrl}>{a.status === "reauth_required" ? "Reconnect" : "Connect YouTube"}</a>
        </>
      )}
      {a.signin_unfinished && !a.connected && <SigninHelp />}
    </div>
  );
}

function SigninHelp() {
  return (
    <div className="alert warn small" style={{ textAlign: "left", marginTop: 14 }}>
      <b>The last Google sign-in for this profile did not finish.</b> Google stopped it on its own page, so AutoTube never got an answer. Fix the cause, then click Connect again:
      <ul style={{ margin: "6px 0 0 18px", padding: 0 }}>
        <li><b>“Access blocked … has not completed the Google verification process” (Error 403: access_denied)</b>: your Google app is in Testing mode.
          Open <a href="https://console.cloud.google.com/auth/audience" target="_blank">Google Auth Platform → Audience ↗</a> (signed in with the Google account that
          created the app) and either click <b>Publish app</b> (recommended) or add this Gmail under <b>Test users</b>.</li>
        <li><b>“Google hasn't verified this app”</b>: click <b>Advanced</b> → <b>Go to AutoTube (unsafe)</b> → Continue. It is your own app, so this is expected.</li>
        <li><b>Error 400: redirect_uri_mismatch</b>: open the dashboard at <b>http://localhost:8000</b> on this PC (not the phone link) and try again.</li>
        <li>Tick <b>every</b> permission box on Google's consent page.</li>
      </ul>
    </div>
  );
}

function SetupWizard({ s, onDone, onCancel }) {
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true); setErr("");
    try { await api("/api/youtube/setup", { method: "PUT", body: { client_id: cid, client_secret: secret } }); onDone(); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  }

  return (
    <div className="card">
      <h2>One-time setup: create your Google app (about 10 minutes, free)</h2>
      <p className="muted small">Google requires every app that uploads to YouTube to have its own OAuth credentials. You do this <b>once</b>.
        After that, connecting your channel is a single click. No billing account is needed.</p>
      <ol className="wizard">
        <li><a href="https://console.cloud.google.com/projectcreate" target="_blank">Create a Google Cloud project ↗</a>, named e.g. <b>AutoTube</b>, signed in with your channel's Google account.</li>
        <li>With that project selected, enable <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank">YouTube Data API v3 ↗</a> and
          {" "}<a href="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com" target="_blank">YouTube Analytics API ↗</a>.</li>
        <li>Open <a href="https://console.cloud.google.com/auth/overview" target="_blank">Google Auth Platform ↗</a> → <b>Get started</b>: app name <b>AutoTube</b>, your email,
          audience <b>External</b> → Create.</li>
        <li>Go to <b>Audience</b> → click <b>Publish app</b> so it says <b>In production</b>. <i>Important:</i> in “Testing” mode Google expires your sign-in every 7 days.</li>
        <li>Go to <a href="https://console.cloud.google.com/auth/clients" target="_blank">Clients ↗</a> → <b>Create client</b> → type <b>Web application</b> → under
          <b> Authorised redirect URIs</b> add exactly:
          <div className="row" style={{ margin: "6px 0" }}><input readOnly value={s.redirect_uri} className="mono" style={{ maxWidth: 520 }} />
            <button onClick={() => navigator.clipboard.writeText(s.redirect_uri)}>Copy</button></div>
          → Create.</li>
        <li>Copy the <b>Client ID</b> and <b>Client secret</b> shown by Google into the boxes below.</li>
      </ol>
      <div className="grid g2">
        <div><label>Client ID</label><input value={cid} onChange={(e) => setCid(e.target.value.trim())} placeholder="123456789012-abc…apps.googleusercontent.com" /></div>
        <div><label>Client secret</label><input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value.trim())} placeholder="GOCSPX-…" /></div>
      </div>
      {err && <div className="alert err" style={{ marginTop: 12 }}>{err}</div>}
      <div className="row" style={{ marginTop: 14 }}>
        <button className="primary" onClick={save} disabled={busy || !cid || !secret}>{busy ? "Saving…" : "Save and continue"}</button>
        {onCancel && <button onClick={onCancel}>Cancel</button>}
        <span className="small muted">Saved to your local <code>.env</code> file on this PC only.</span>
      </div>
      <p className="small muted" style={{ marginTop: 14 }}>When you connect, Google may show <b>“Google hasn't verified this app”</b>. That's expected for a personal app you created yourself:
        click <b>Advanced → Go to AutoTube (unsafe)</b> → Continue.</p>
    </div>
  );
}
