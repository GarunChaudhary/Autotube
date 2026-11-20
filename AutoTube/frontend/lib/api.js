"use client";
// Same-origin in production (served by FastAPI). For `npm run dev`, set NEXT_PUBLIC_API=http://localhost:8000
export const API = process.env.NEXT_PUBLIC_API || "";

export async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(API + path, init);
  if (r.status === 401 && typeof window !== "undefined") {   // not logged in / session expired -> login page
    window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search);
    throw new Error("login required");
  }
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

export const STAGES = ["research", "script", "factcheck", "storyboard", "visuals", "voiceover", "editing",
  "thumbnail", "seo", "quality", "done"];

export const STATUSES = ["DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK", "READY", "WAITING_FOR_APPROVAL",
  "SCHEDULED", "UPLOADING", "PUBLISHED", "FAILED", "PAUSED"];

export const STATUS_LABEL = {
  DRAFT: "Draft", GENERATING: "Generating", RENDERING: "Rendering", QUALITY_CHECK: "Quality check", READY: "Ready",
  WAITING_FOR_APPROVAL: "Waiting for approval", SCHEDULED: "Scheduled", UPLOADING: "Uploading",
  PUBLISHED: "Published", FAILED: "Failed", PAUSED: "Paused", UPCOMING: "Upcoming slot",
};

export function statusClass(s) {
  return {
    PUBLISHED: "b-ok", SCHEDULED: "b-ok", READY: "b-ok",
    GENERATING: "b-info", RENDERING: "b-info", QUALITY_CHECK: "b-info", UPLOADING: "b-info", DRAFT: "b-info",
    WAITING_FOR_APPROVAL: "b-warn", PAUSED: "b-warn", UPCOMING: "",
    FAILED: "b-err",
  }[s] || "";
}

export function Badge({ s }) {
  return <span className={"badge " + statusClass(s)}>{STATUS_LABEL[s] || s}</span>;
}

export function fmtDur(s) {
  if (!s) return "-";
  const m = Math.floor(s / 60), x = Math.round(s % 60);
  return `${m}:${String(x).padStart(2, "0")}`;
}

function toDate(t) {
  if (!t) return null;
  return new Date(t.includes("T") ? t : t.replace(" ", "T") + "Z");
}

// Times are shown in the scheduler timezone (default Asia/Kolkata), not the browser's.
export function fmtTime(t, tz = "Asia/Kolkata") {
  const d = toDate(t);
  if (!d) return "-";
  return d.toLocaleString("en-IN", { timeZone: tz, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

export function fmtTimeOnly(t, tz = "Asia/Kolkata") {
  const d = toDate(t);
  if (!d) return "-";
  return d.toLocaleTimeString("en-IN", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: true });
}

export function countdown(t) {
  const d = toDate(t);
  if (!d) return "";
  let s = Math.max(0, Math.round((d - Date.now()) / 1000));
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(s).padStart(2, "0")}s`;
}

// "2026-11-20" + "18:30" in a named timezone -> "2026-11-20T13:00:00Z"
export function zonedToUtc(dateStr, hhmm, tz) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const [H, M] = hhmm.split(":").map(Number);
  const guess = Date.UTC(y, m - 1, d, H, M);
  const fmt = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const p = Object.fromEntries(fmt.formatToParts(new Date(guess)).map((x) => [x.type, x.value]));
  const asTz = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  return new Date(guess - (asTz - guess)).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function tzAbbrev(tz) {
  return tz === "Asia/Kolkata" ? "IST" : tz;
}
