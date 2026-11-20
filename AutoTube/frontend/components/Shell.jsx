"use client";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

const NAV = [
  ["Overview", [["/", "Dashboard"], ["/queue/", "Upload queue"], ["/calendar/", "Calendar"], ["/library/", "Video library"]]],
  ["Production", [["/trends/", "🔥 Viral Videos"], ["/upload-now/", "⚡ Upload now"], ["/scheduler/", "Scheduler"], ["/create/", "Create video"], ["/channel/", "Channel & niche"]]],
  ["AI Characters", [["/characters/", "🎭 AI Characters"]]],
  ["YouTube", [["/my-youtube/", "My YouTube"], ["/youtube/", "Accounts"], ["/analytics/", "Analytics"]]],
  ["System", [["/help/", "❓ Help & guides"], ["/logs/", "Logs & reports"], ["/settings/", "Providers & setup"]]],
];

// phone: bottom tab bar (the full menu stays one tap away under "More")
const TABS = [["/", "🏠", "Home"], ["/queue/", "📋", "Queue"], ["/upload-now/", "⚡", "Upload"], ["/my-youtube/", "▶️", "My YouTube"]];

export default function Shell({ children }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState(null);

  const load = () => api("/api/health").then(setHealth).catch(() => setHealth(null));
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  async function toggleStop() {
    const active = !health?.emergency_stop;
    if (active && !confirm("EMERGENCY STOP: halt production and block all uploads now?")) return;
    await api("/api/emergency-stop", { method: "POST", body: { active } });
    load();
  }

  async function togglePause() {
    await api("/api/automation/pause", { method: "POST", body: { paused: !health?.automation_paused } });
    load();
  }

  const conn = health?.youtube_status;
  const accts = health?.youtube_accounts || [];
  return (
    <div className="shell">
      <aside className={"side" + (open ? " open" : "")}>
        <div className="brand"><span className="logo" />AutoTube AI</div>
        <nav className="nav">
          {NAV.map(([sec, items]) => (
            <div key={sec}>
              <div className="sec">{sec}</div>
              {items.map(([href, label]) => (
                <a key={href} href={href} className={path === href ? "active" : ""}>{label}</a>
              ))}
            </div>
          ))}
        </nav>
        <div style={{ padding: "18px 10px", display: "grid", gap: 8 }}>
          <button onClick={togglePause} disabled={!health || health.emergency_stop} style={{ justifyContent: "center" }}>
            {health?.automation_paused ? "▶ Resume automation" : "❚❚ Pause automation"}
          </button>
          <button className={health?.emergency_stop ? "" : "danger"} style={{ justifyContent: "center" }}
            onClick={toggleStop} disabled={!health}>
            {health?.emergency_stop ? "Release emergency stop" : "EMERGENCY STOP"}
          </button>
          <form method="post" action="/api/logout" style={{ margin: 0 }}>
            <button type="submit" style={{ justifyContent: "center", width: "100%" }}>Log out</button>
          </form>
          <div className="small muted" style={{ marginTop: 4, lineHeight: 1.7 }}>
            <div><span className="dot" style={{ background: health ? "var(--ok)" : "var(--err)" }} /> Backend {health ? "online" : "offline"}</div>
            <div><span className="dot" style={{ background: health?.ollama?.running ? "var(--ok)" : "var(--err)" }} /> Local AI {health?.ollama?.running ? "running" : "off"}</div>
            <div><span className="dot" style={{ background: conn === "connected" ? "var(--ok)" : conn === "reauth_required" ? "var(--err)" : "var(--warn)" }} /> YouTube {conn === "reauth_required" ? "needs reconnect" : accts.length > 1 ? `${accts.filter((a) => a.status === "connected").length}/${accts.length} connected` : conn === "connected" ? "connected" : "not connected"}</div>
          </div>
        </div>
      </aside>
      <main className="main">
        <button className="hamb" onClick={() => setOpen(!open)} style={{ marginBottom: 12 }}>☰ Menu</button>
        {health?.emergency_stop && <div className="stopbanner">Emergency stop is active. Production and uploads are halted.</div>}
        {!health?.emergency_stop && health?.automation_paused && <div className="alert warn"><b>Automation paused.</b> No new videos are planned and nothing is uploaded until you resume.</div>}
        {conn === "reauth_required" && <div className="alert err">YouTube authorization expired or was revoked
          {accts.length > 1 ? ` for ${accts.filter((a) => a.status === "reauth_required").map((a) => a.channel_title || a.profile_name).join(", ")}` : ""}.
          {" "}<a href="/youtube/">Reconnect YouTube</a> to resume uploads.</div>}
        {!health && <div className="alert err">Cannot reach the AutoTube backend. Start it with START_AUTOTUBE.bat.</div>}
        {children}
      </main>
      <nav className="tabbar" aria-label="Main">
        {TABS.map(([href, icon, label]) => (
          <a key={href} href={href} className={path === href ? "active" : ""}><span aria-hidden="true">{icon}</span>{label}</a>))}
        <button className={open ? "active" : ""} onClick={() => { setOpen(!open); window.scrollTo(0, 0); }}>
          <span aria-hidden="true">☰</span>More</button>
      </nav>
    </div>
  );
}
