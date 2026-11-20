"use client";
import { useEffect, useState } from "react";
import { api, tzAbbrev } from "../../lib/api";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const MODES = [
  ["automatic", "Automatic", "Generate, render, quality-check and upload on schedule. Videos that fail any quality check still wait for you."],
  ["approval", "Approval", "Each video is made a few hours before its slot, then waits for you. After you approve it, it goes public on YouTube at the slot time."],
  ["manual", "Manual", "Generate and keep in the library. You choose when to upload."],
];
const TIMEZONES = ["Asia/Kolkata", "UTC", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "America/Los_Angeles"];

export default function Scheduler() {
  const [s, setS] = useState(null);
  const [slots, setSlots] = useState([]);
  const [tz, setTz] = useState("Asia/Kolkata");
  const [msg, setMsg] = useState(null);
  const [auto, setAuto] = useState(false);
  const [newSlot, setNewSlot] = useState({ label: "", kind: "short", time_local: "10:00", days: DAYS.join(","), channel_id: null });
  const [channels, setChannels] = useState([]);

  const load = () => {
    api("/api/settings").then(setS);
    api("/api/channels").then(setChannels);
    api("/api/slots").then((r) => { setSlots(r.slots); setTz(r.timezone); });
    api("/api/system/autostart").then((r) => setAuto(r.enabled));
  };
  useEffect(() => { load(); }, []);

  const flash = (text, err) => { setMsg({ text, err }); if (!err) setTimeout(() => setMsg(null), 2500); };

  async function save(values) {
    try { setS(await api("/api/settings", { method: "PUT", body: { values } })); flash("Saved."); load(); }
    catch (e) { flash(e.message, true); }
  }
  async function slotUpdate(id, body) {
    try { await api(`/api/slots/${id}`, { method: "PUT", body }); load(); flash("Slot updated."); }
    catch (e) { flash(e.message, true); load(); }
  }
  async function slotAdd() {
    try { await api("/api/slots", { method: "POST", body: newSlot }); setNewSlot({ ...newSlot, label: "" }); load(); flash("Slot added."); }
    catch (e) { flash(e.message, true); }
  }
  async function slotDelete(id) {
    if (!confirm("Remove this time slot? Videos already produced for it are kept.")) return;
    await api(`/api/slots/${id}`, { method: "DELETE" }); load();
  }
  async function planNow() {
    try { const r = await api("/api/scheduler/plan-now", { method: "POST" }); flash(`Planned ${r.created.length} video(s) for upcoming slots.`); }
    catch (e) { flash(e.message, true); }
  }
  async function setAutostart(enabled) {
    const r = await api("/api/system/autostart", { method: "POST", body: { enabled } }); setAuto(r.enabled);
  }


  if (!s) return <div className="muted">Loading…</div>;
  const multi = channels.length > 1;
  const shortSlots = slots.filter((x) => x.kind === "short" && x.enabled && !x.paused).length;
  const longSlots = slots.filter((x) => x.kind === "long" && x.enabled && !x.paused).length;
  const Num = ({ k, label, min, max, hint }) => (
    <div><label>{label}</label>
      <input type="number" min={min} max={max} defaultValue={s[k]} key={`${k}-${s[k]}`}
        onBlur={(e) => { const v = Number(e.target.value); if (v !== s[k]) save({ [k]: v }); }} />
      {hint && <div className="small muted">{hint}</div>}</div>
  );
  const Toggle = ({ k, label, hint, value, onChange, disabled }) => (
    <div className="toggle"><input type="checkbox" checked={value ?? !!s[k]} disabled={disabled}
      onChange={(e) => (onChange ? onChange(e.target.checked) : save({ [k]: e.target.checked }))} />
      <div><b>{label}</b>{hint && <div className="small muted">{hint}</div>}</div></div>
  );

  return (
    <>
      <div className="topbar"><h1>Scheduler</h1>
        <div className="row"><button onClick={planNow}>Plan upcoming videos now</button><a className="btn" href="/calendar/">Open calendar</a></div>
      </div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Upload mode</h2>
        <div className="modes">
          {MODES.map(([k, label, desc]) => (
            <button key={k} className={"mode" + (s.upload_mode === k ? " on" : "")} onClick={() => save({ upload_mode: k })}>
              <b>{label} {s.upload_mode === k && "✓"}</b><span className="small muted">{desc}</span>
            </button>))}
        </div>
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Automation</h2>
          <Toggle k="automation_enabled" label="Automatic video generation" disabled={s.emergency_stop}
            hint={`Starts producing each slot's video ${s.production_lead_minutes >= 120 ? `${+(s.production_lead_minutes / 60).toFixed(1)} hours` : `${s.production_lead_minutes} minutes`} before its slot time (slot videos jump ahead of manual jobs).`} />
          <Toggle k="auto_upload" label="Automatic uploading" disabled={s.emergency_stop || s.upload_mode === "manual"}
            hint={s.upload_mode === "manual" ? "Not used in Manual mode." : "Uploads READY videos at their slot time (needs YouTube connected)."} />
          <Toggle k="slot_auto_publish" label="Scheduled videos upload without approval"
            hint="Slot videos go up automatically at their slot time, even with quality warnings (you get a notification). Broken renders never upload." />
          <Toggle k="growth_auto_genre" label="Growth: pick each slot's genre from what gets views"
            hint="Uses your channel's real YouTube numbers: mostly the best-performing genres, some slots test others. Choose genres on the Analytics page." />
          <Toggle k="engagement_comment" label="Growth: post a question comment on each new video"
            hint="One comment from your channel inviting replies, plus a link to your best video. Pin it in YouTube Studio (the API can't pin)." />
          <Toggle k="subscribe_prompt" label="Growth: on-screen follow / subscribe prompt"
            hint="Silent text in the last seconds of Shorts (keeps the replay loop) and once early in long videos." />
          <Toggle label="Manual approval before publishing" value={s.upload_mode === "approval"}
            onChange={(on) => save({ upload_mode: on ? "approval" : "automatic" })} hint="Same as choosing Approval mode above." />
          <Toggle label="Start AutoTube when I log in to Windows" value={auto} onChange={setAutostart}
            hint="Adds a launcher to your Windows Startup folder. Runs in the background; no browser window." />
          <div className="alert warn small" style={{ marginTop: 12 }}>Scheduling runs on this PC. While it is off or asleep nothing is generated or uploaded.
            The queue lives in the database, so nothing is lost: missed slots are caught up when AutoTube starts again.</div>
        </div>

        <div className="card">
          <h2>Daily production</h2>
          <div className="grid g2">
            <Num k="shorts_per_day" label={multi ? "Shorts per day, each account (0-10)" : "Shorts per day (0-10)"} min={0} max={10} hint={`${shortSlots} active Short slot${shortSlots === 1 ? "" : "s"}`} />
            <Num k="longs_per_day" label={multi ? "Long videos per day, each account (0-5)" : "Long videos per day (0-5)"} min={0} max={5} hint={`${longSlots} active long slot${longSlots === 1 ? "" : "s"}`} />
            <Num k="max_daily_videos" label="Daily total limit" min={0} max={15} />
            <Num k="max_concurrent_renders" label="Max concurrent rendering jobs" min={1} max={4} hint="CPU heavy: 1-2 recommended on this PC" />
            <Num k="upload_retry_count" label="Upload retries after failure" min={1} max={10} hint="Backoff 5 → 20 → 60 → 180 min" />
            <Num k="production_lead_minutes" label="Start production before slot (minutes)" min={15} max={720} hint="180 = 3 hours (recommended). A Short takes ~15-60 min and a long video ~1 h on this PC, plus time for retries and your approval." />
          </div>
          {(s.shorts_per_day > shortSlots || s.longs_per_day > longSlots) &&
            <div className="alert warn small" style={{ marginTop: 10 }}>You have fewer active slots than your daily target. Add time slots below; each video needs its own slot.</div>}
          <div className="grid g2">
            <div><label>Upload timezone</label>
              <select value={s.timezone} onChange={(e) => save({ timezone: e.target.value })}>
                {TIMEZONES.map((z) => <option key={z} value={z}>{z}{z === "Asia/Kolkata" ? " (IST)" : ""}</option>)}
              </select></div>
          </div>
          <Toggle k="allow_same_time" label="Allow two videos at the same time" hint="Off by default: slots and reschedules at an identical minute are rejected." />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row"><h2>Upload time slots ({tzAbbrev(tz)})</h2></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th>{multi && <th>YouTube account</th>}<th>Type</th><th>Time</th><th>Days</th><th>Enabled</th><th>Paused</th><th></th></tr></thead>
            <tbody>
              {slots.map((x) => (
                <tr key={x.id}>
                  <td><input defaultValue={x.label} onBlur={(e) => e.target.value !== x.label && slotUpdate(x.id, { label: e.target.value })} /></td>
                  {multi && <td><ChannelSelect channels={channels} value={x.channel_id || channels[0]?.id}
                    onChange={(id) => slotUpdate(x.id, { channel_id: id })} /></td>}
                  <td><select value={x.kind} onChange={(e) => slotUpdate(x.id, { kind: e.target.value })}>
                    <option value="short">Short</option><option value="long">Long</option></select></td>
                  <td><input type="time" defaultValue={x.time_local} key={x.time_local}
                    onBlur={(e) => e.target.value && e.target.value !== x.time_local && slotUpdate(x.id, { time_local: e.target.value })} /></td>
                  <td><div className="days">{DAYS.map((d) => {
                    const on = x.days.split(",").includes(d);
                    const next = on ? x.days.split(",").filter((y) => y !== d) : [...x.days.split(","), d];
                    return <button key={d} className={on ? "on" : ""} onClick={() => slotUpdate(x.id, { days: next.join(",") })}>{d}</button>;
                  })}</div></td>
                  <td><input type="checkbox" checked={!!x.enabled} onChange={(e) => slotUpdate(x.id, { enabled: e.target.checked })} /></td>
                  <td><input type="checkbox" checked={!!x.paused} onChange={(e) => slotUpdate(x.id, { paused: e.target.checked })} /></td>
                  <td><button onClick={() => slotDelete(x.id)}>Remove</button></td>
                </tr>))}
              <tr>
                <td><input placeholder="e.g. Short 4" value={newSlot.label} onChange={(e) => setNewSlot({ ...newSlot, label: e.target.value })} /></td>
                {multi && <td><ChannelSelect channels={channels} value={newSlot.channel_id || channels[0]?.id}
                  onChange={(id) => setNewSlot({ ...newSlot, channel_id: id })} /></td>}
                <td><select value={newSlot.kind} onChange={(e) => setNewSlot({ ...newSlot, kind: e.target.value })}>
                  <option value="short">Short</option><option value="long">Long</option></select></td>
                <td><input type="time" value={newSlot.time_local} onChange={(e) => setNewSlot({ ...newSlot, time_local: e.target.value })} /></td>
                <td><div className="days">{DAYS.map((d) => {
                  const on = newSlot.days.split(",").includes(d);
                  const next = on ? newSlot.days.split(",").filter((y) => y && y !== d) : [...newSlot.days.split(",").filter(Boolean), d];
                  return <button key={d} className={on ? "on" : ""} onClick={() => setNewSlot({ ...newSlot, days: next.join(",") })}>{d}</button>;
                })}</div></td>
                <td colSpan={3}><button className="primary" onClick={slotAdd}>+ Add slot</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Publishing settings</h2>
        <div className="grid g2">
          <div>
            <label>Default privacy</label>
            <select value={s.default_privacy} onChange={(e) => save({ default_privacy: e.target.value })}>
              <option value="public">Public</option>
              <option value="unlisted" disabled={!s.allow_unlisted_uploads}>Unlisted{!s.allow_unlisted_uploads ? " (enable below)" : ""}</option>
              <option value="private" disabled={!s.allow_private_uploads}>Private{!s.allow_private_uploads ? " (enable below)" : ""}</option>
            </select>
            <Toggle k="scheduled_publishing" label="Scheduled publishing"
              hint="Public videos are uploaded ahead of their slot as private with publishAt; YouTube makes them public at the slot time, and AutoTube verifies it happened." />
          </div>
          <div>
            <Toggle k="allow_private_uploads" label="Allow private uploads" hint="Off: nothing can be left private by accident." />
            <Toggle k="allow_unlisted_uploads" label="Allow unlisted uploads" />
          </div>
        </div>
        <p className="small muted" style={{ marginTop: 10 }}>Only videos you approved, or that passed every quality check in Automatic mode, are published.
          Each upload's privacy is read back from YouTube and shown as <b>YouTube: public/private</b>. If your Google project has not passed
          YouTube's API compliance audit, YouTube locks API uploads as private. AutoTube will show that rather than pretend they are public.</p>
      </div>
    </>
  );
}

function ChannelSelect({ channels, value, onChange }) {
  return (
    <select value={value || ""} onChange={(e) => onChange(Number(e.target.value))}>
      {channels.map((c) => <option key={c.id} value={c.id}>{c.youtube_title ? `${c.name} (${c.youtube_title})` : `${c.name} (not connected)`}</option>)}
    </select>
  );
}
