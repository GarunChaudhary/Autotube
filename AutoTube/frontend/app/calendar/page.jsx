"use client";
import { useEffect, useMemo, useState } from "react";
import { api, STATUS_LABEL, tzAbbrev } from "../../lib/api";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LEGEND = [["Published / scheduled", "var(--ok)"], ["In production", "var(--accent-2)"], ["Needs approval / paused", "var(--warn)"],
  ["Failed", "var(--err)"], ["Upcoming slot", "var(--muted)"]];
const MOVABLE = ["DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK", "READY", "WAITING_FOR_APPROVAL", "FAILED", "PAUSED"];

function ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }

// local wall time in the scheduler timezone -> UTC ISO
function zonedToUtc(dateStr, hhmm, tz) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const [H, M] = hhmm.split(":").map(Number);
  const guess = Date.UTC(y, m - 1, d, H, M);
  const fmt = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  const p = Object.fromEntries(fmt.formatToParts(new Date(guess)).map((x) => [x.type, x.value]));
  const asTz = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  return new Date(guess - (asTz - guess)).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export default function Calendar() {
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [items, setItems] = useState([]);
  const [tz, setTz] = useState("Asia/Kolkata");
  const [msg, setMsg] = useState(null);
  const [over, setOver] = useState(null);

  const grid = useMemo(() => {
    const first = new Date(month);
    const start = new Date(first); start.setDate(1 - ((first.getDay() + 6) % 7));
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  }, [month]);

  const load = () => api(`/api/calendar?start=${ymd(grid[0])}&end=${ymd(grid[41])}`)
    .then((r) => { setItems(r.items); setTz(r.timezone); }).catch((e) => setMsg({ err: true, text: e.message }));
  useEffect(() => { load(); }, [grid]);

  const byDay = useMemo(() => {
    const m = {};
    items.forEach((it) => { (m[it.local_date] = m[it.local_date] || []).push(it); });
    return m;
  }, [items]);

  async function drop(e, day) {
    e.preventDefault(); setOver(null);
    const data = JSON.parse(e.dataTransfer.getData("text/plain") || "{}");
    if (!data.id) return;
    const time = prompt(`Move to ${day} at what time (${tzAbbrev(tz)}, HH:MM)?`, data.time);
    if (!time || !/^\d{2}:\d{2}$/.test(time)) return;
    try {
      await api(`/api/videos/${data.id}/schedule`, { method: "PUT", body: { scheduled_for: zonedToUtc(day, time, tz) } });
      setMsg({ text: `Video #${data.id} rescheduled to ${day} ${time} ${tzAbbrev(tz)}.` }); load();
    } catch (err) { setMsg({ err: true, text: err.message }); }
  }

  const today = new Date().toLocaleDateString("en-CA", { timeZone: tz });
  return (
    <>
      <div className="topbar">
        <h1>Upload calendar</h1>
        <div className="row">
          <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
          <b style={{ minWidth: 140, textAlign: "center" }}>{month.toLocaleDateString("en-IN", { month: "long", year: "numeric" })}</b>
          <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
          <button onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); }}>Today</button>
        </div>
      </div>
      {msg && <div className={"alert" + (msg.err ? " err" : "")}>{msg.text}</div>}
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="legend">{LEGEND.map(([l, c]) => <span key={l} style={{ "--c": c }}>{l}</span>)}</div>
        <span className="spacer" /><span className="small muted">Drag a video to another day to reschedule. Times in {tzAbbrev(tz)}.</span>
      </div>
      <div className="cal">
        {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
        {grid.map((d) => {
          const key = ymd(d);
          const list = byDay[key] || [];
          return (
            <div key={key} className={"cell" + (d.getMonth() !== month.getMonth() ? " other" : "") + (key === today ? " today" : "") + (over === key ? " over" : "")}
              onDragOver={(e) => { e.preventDefault(); setOver(key); }} onDragLeave={() => setOver(null)} onDrop={(e) => drop(e, key)}>
              <div className="num">{d.getDate()}</div>
              {list.map((it, i) => {
                const movable = it.id && MOVABLE.includes(it.status);
                return (
                  <div key={(it.id || "s" + it.slot_id) + "-" + i} className={"ev " + it.status} draggable={!!movable}
                    title={`${it.local_time} · ${it.kind} · ${STATUS_LABEL[it.status] || it.status}${it.rescheduled ? " · rescheduled" : ""}\n${it.title || it.topic || ""}`}
                    onDragStart={(e) => e.dataTransfer.setData("text/plain", JSON.stringify({ id: it.id, time: it.local_time }))}
                    onClick={() => it.id && (location.href = `/video/?id=${it.id}`)}>
                    <b>{it.local_time}</b> {it.kind === "short" ? "▮" : "▬"} {it.title || it.topic}{it.rescheduled ? " ↻" : ""}
                  </div>);
              })}
            </div>);
        })}
      </div>
    </>
  );
}
