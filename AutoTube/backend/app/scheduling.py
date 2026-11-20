"""Daily production & upload scheduler (persistent - everything lives in SQLite, so nothing is lost on restart).

Slots      : named times (HH:MM in the scheduler timezone, default Asia/Kolkata) for Shorts / long videos,
             with weekday selection and per-slot enable/pause.
Planner    : every minute, creates ONE video per slot occurrence starting `production_lead_minutes` (default 180)
             before the slot (unique DB index = no duplicates),
             within the daily Shorts / long / total limits, and queues it for production.
Dispatcher : every minute, uploads videos whose slot time has come (or, for approved public scheduling,
             uploads early with publishAt). Atomic READY->UPLOADING claim = no double publishing.
Modes      : automatic (upload when quality passes) | approval (wait for your approval) | manual (you upload).
NOTE: this runs on this PC. While the PC is off/asleep nothing runs; on next start the queue is recovered
from the database and missed work continues.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import config, db, logs

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UPLOAD_BACKOFF_MIN = [5, 20, 60, 180]


class ScheduleError(ValueError):
    pass


# ------------------------------------------------------------------ time helpers
def tz() -> ZoneInfo:
    return ZoneInfo(db.get_setting("timezone") or "Asia/Kolkata")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_to_utc(day: dt.date, hhmm: str) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=tz()).astimezone(dt.timezone.utc)


def today_local(now: dt.datetime | None = None) -> dt.date:
    return (now or now_utc()).astimezone(tz()).date()


# ------------------------------------------------------------------ slots
def _validate_slot(kind: str, time_local: str, days: str, slot_id: int | None = None,
                   channel_id: int | None = None) -> str:
    if kind not in ("short", "long"):
        raise ScheduleError("kind must be 'short' or 'long'")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_local or ""):
        raise ScheduleError("time must be HH:MM (24h)")
    ds = [d.strip().lower()[:3] for d in (days or "").split(",") if d.strip()]
    if not ds or any(d not in DAYS for d in ds):
        raise ScheduleError("select at least one valid day")
    days = ",".join(d for d in DAYS if d in ds)
    if channel_id is not None and not db.one("SELECT id FROM channels WHERE id=?", (channel_id,)):
        raise ScheduleError("channel profile not found")
    if not db.get_setting("allow_same_time"):
        mine = slot_channel({"channel_id": channel_id})
        for s in db.query("SELECT * FROM schedule_slots WHERE enabled=1 AND time_local=? AND id IS NOT ?",
                          (time_local, slot_id)):
            if slot_channel(s) != mine:
                continue                     # different YouTube accounts may publish at the same time
            if set(s["days"].split(",")) & set(days.split(",")):
                raise ScheduleError(f"'{s['label']}' already uses {time_local} on overlapping days "
                                    "(enable 'allow same time' to permit this)")
    return days


def list_slots() -> list[dict]:
    return db.query("SELECT * FROM schedule_slots ORDER BY time_local, id")


def create_slot(label: str, kind: str, time_local: str, days: str = ",".join(DAYS), enabled: bool = True,
                channel_id: int | None = None) -> dict:
    days = _validate_slot(kind, time_local, days, channel_id=channel_id)
    sid = db.execute("INSERT INTO schedule_slots(label, kind, time_local, days, enabled, channel_id) VALUES (?,?,?,?,?,?)",
                     (label.strip() or f"{kind} {time_local}", kind, time_local, days, int(enabled), channel_id))
    return db.one("SELECT * FROM schedule_slots WHERE id=?", (sid,))


def update_slot(slot_id: int, **f: Any) -> dict:
    s = db.one("SELECT * FROM schedule_slots WHERE id=?", (slot_id,))
    if not s:
        raise ScheduleError("slot not found")
    s.update({k: v for k, v in f.items() if v is not None})
    if s["enabled"]:
        s["days"] = _validate_slot(s["kind"], s["time_local"], s["days"], slot_id, s.get("channel_id"))
    elif s.get("channel_id") is not None and not db.one("SELECT id FROM channels WHERE id=?", (s["channel_id"],)):
        raise ScheduleError("channel profile not found")
    db.execute("UPDATE schedule_slots SET label=?, kind=?, time_local=?, days=?, enabled=?, paused=?, channel_id=? WHERE id=?",
               (s["label"], s["kind"], s["time_local"], s["days"], int(bool(s["enabled"])), int(bool(s["paused"])),
                s.get("channel_id"), slot_id))
    return db.one("SELECT * FROM schedule_slots WHERE id=?", (slot_id,))


def delete_slot(slot_id: int) -> None:
    # keep already-produced videos; just detach them from the slot definition
    db.execute("DELETE FROM schedule_slots WHERE id=?", (slot_id,))


def occurrences(start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Active slot occurrences (UTC) within [start, end)."""
    out = []
    d0, d1 = start.astimezone(tz()).date(), end.astimezone(tz()).date()
    slots = [s for s in list_slots() if s["enabled"]]
    day = d0
    while day <= d1:
        for s in slots:
            if DAYS[day.weekday()] not in s["days"].split(","):
                continue
            t = local_to_utc(day, s["time_local"])
            if start <= t < end:
                out.append({"slot": s, "date": day.isoformat(), "at": t})
        day += dt.timedelta(days=1)
    return sorted(out, key=lambda o: o["at"])


# ------------------------------------------------------------------ planner
def lead_time() -> dt.timedelta:
    """How long before its slot a video's production starts (default 180 min)."""
    return dt.timedelta(minutes=float(db.get_setting("production_lead_minutes") or 180))


def _default_channel() -> dict | None:
    return db.one("SELECT * FROM channels ORDER BY id LIMIT 1")


def daily_counts(day: str, channel_id: int | None = None) -> dict:
    """Videos already planned for a day (one profile, or all profiles when channel_id is None)."""
    rows = db.query("SELECT kind, COUNT(*) n FROM videos WHERE slot_date=?" + (" AND channel_id=?" if channel_id else "")
                    + " GROUP BY kind", (day, channel_id) if channel_id else (day,))
    c = {"short": 0, "long": 0}
    for r in rows:
        c[r["kind"]] = r["n"]
    c["total"] = c["short"] + c["long"]
    return c


def slot_channel(slot: dict) -> int | None:
    """The channel profile a slot produces for (slots without one belong to the first profile)."""
    if slot.get("channel_id"):
        return slot["channel_id"]
    ch = _default_channel()
    return ch["id"] if ch else None


def plan(now: dt.datetime | None = None, suggest=None, force: bool = False) -> list[int]:
    """Create videos for upcoming slot occurrences, for every channel profile. Idempotent; safe to run any time.
    force=True (\"Plan now\" button) runs even when automatic generation is switched off.
    shorts/longs per day apply to each profile; max_daily_videos caps all profiles together (one PC renders them)."""
    from . import pipeline
    from .agents import research
    if db.get_setting("emergency_stop") or db.get_setting("automation_paused"):
        return []
    if not force and not db.get_setting("automation_enabled"):
        return []
    channels = {c["id"]: c for c in db.query("SELECT * FROM channels ORDER BY id")}
    if not channels:
        return []
    now = now or now_utc()
    horizon = now + lead_time() + dt.timedelta(seconds=1)     # inclusive: exactly lead-time before the slot counts
    limits = {"short": int(db.get_setting("shorts_per_day")), "long": int(db.get_setting("longs_per_day"))}
    total_limit = int(db.get_setting("max_daily_videos"))
    needed: dict[int, list] = {}
    counts: dict[tuple, dict] = {}
    totals: dict[str, dict] = {}
    for o in occurrences(now, horizon):
        s = o["slot"]
        cid = slot_channel(s)
        if s["paused"] or cid not in channels:
            continue
        if db.one("SELECT id FROM videos WHERE slot_id=? AND slot_date=?", (s["id"], o["date"])):
            continue
        c = counts.setdefault((cid, o["date"]), daily_counts(o["date"], cid))
        tot = totals.setdefault(o["date"], daily_counts(o["date"]))
        if c[s["kind"]] >= limits[s["kind"]] or tot["total"] >= total_limit:
            continue
        c[s["kind"]] += 1
        tot["total"] += 1
        needed.setdefault(cid, []).append(o)
    ids = []
    for cid, occ in needed.items():
        ch = channels[cid]
        if suggest is None and db.get_setting("growth_auto_genre"):
            ids += _plan_by_growth(ch, occ)
            continue
        topics = (suggest or (lambda n, ch=ch: research.suggest_topics(ch, n)))(len(occ))
        for o, topic in zip(occ, topics):
            try:
                vid = pipeline.create_video(cid, o["slot"]["kind"], topic, scheduled_for=iso(o["at"]),
                                            slot_id=o["slot"]["id"], slot_date=o["date"])
            except Exception as e:      # duplicate topic / unique slot race -> skip safely
                logs.warn("scheduler", f"slot {o['slot']['label']} {o['date']}: {e}")
                continue
            pipeline.enqueue(vid)
            ids.append(vid)
        if len(topics) < len(occ):
            logs.warn("scheduler", f"only {len(topics)} fresh topics for {len(occ)} slots of '{ch['name']}'")
    if ids:
        logs.notify("INFO", "Videos planned", f"{len(ids)} videos queued for upcoming slots")
    return ids


def _plan_by_growth(ch: dict, occ: list[dict], pick=None, suggest=None) -> list[int]:
    """Each slot gets the genre that is earning this channel the most views (with some testing of others), and a
    verified, demand-ranked Wikipedia topic from that genre's bank."""
    from . import growth, pipeline
    from .agents import research
    from .agents import topics as topic_bank
    pick = pick or growth.pick_genre
    suggest = suggest or (lambda g: research.suggest_topics(ch, 1, genre=g))
    ids = []
    for o in occ:
        kind = o["slot"]["kind"]
        g = pick(ch["id"], kind)
        found = suggest(g)
        if not found:
            logs.warn("scheduler", f"no fresh {g} topic for slot {o['slot']['label']} {o['date']}")
            continue
        try:
            vid = pipeline.create_video(ch["id"], kind, found[0], scheduled_for=iso(o["at"]), slot_id=o["slot"]["id"],
                                        slot_date=o["date"], genre=g)
        except Exception as e:
            logs.warn("scheduler", f"slot {o['slot']['label']} {o['date']}: {e}")
            continue
        topic_bank.take(g, ch["id"], found[0])
        logs.info("growth", f"slot {o['slot']['label']}: genre '{g}' chosen from channel performance", vid)
        pipeline.enqueue(vid)
        ids.append(vid)
    return ids


# ------------------------------------------------------------------ dispatcher
def _ready_to_upload(v: dict, now: dt.datetime) -> bool:
    if v.get("next_retry_at") and parse(v["next_retry_at"]) > now:
        return False
    if not v.get("scheduled_for"):
        return False                         # unscheduled videos are uploaded manually
    when = parse(v["scheduled_for"])
    public = (v.get("target_privacy") or db.get_setting("default_privacy") or "public") == "public"
    if public and db.get_setting("scheduled_publishing") and when - now > dt.timedelta(minutes=15):
        return True                          # upload early as private + publishAt; YouTube publishes at the slot
    return when <= now                       # otherwise upload at the slot time with the final privacy


def dispatch(now: dt.datetime | None = None) -> list[int]:
    """Upload due videos. Returns ids attempted."""
    from . import pipeline
    from .youtube import oauth
    if db.get_setting("emergency_stop") or db.get_setting("automation_paused") or not db.get_setting("auto_upload"):
        return []
    if db.get_setting("upload_mode") == "manual":
        return []
    linked = set(oauth.connected_profiles())
    if not linked:
        return []
    now = now or now_utc()
    if db.get_setting("slot_auto_publish"):
        # slot videos that were held for review before this option existed: release them so they go up at their slot
        db.execute("UPDATE videos SET status='READY', updated_at=datetime('now') WHERE status='WAITING_FOR_APPROVAL' "
                   "AND (slot_id IS NOT NULL OR scheduled_for IS NOT NULL) AND youtube_video_id IS NULL")
    attempted = []
    for v in db.query("SELECT * FROM videos WHERE status='READY' AND youtube_video_id IS NULL ORDER BY scheduled_for"):
        if v["channel_id"] not in linked or not _ready_to_upload(v, now):
            continue                         # each video goes to its own profile's account only
        attempted.append(v["id"])
        try:
            pipeline.do_upload(v["id"])
        except Exception:
            pass                             # do_upload records retry/failure state
    return attempted


def dispatch_immediate(now: dt.datetime | None = None) -> list[int]:
    """'Upload immediately' videos: the user chose this per video, so they go up as soon as they are READY
    (independent of the slot auto-upload switch). Retries respect the backoff recorded by do_upload."""
    from . import pipeline
    from .youtube import oauth
    if pipeline.immediate_block_reason():
        return []
    linked = set(oauth.connected_profiles())
    now = now or now_utc()
    attempted = []
    for v in db.query("SELECT id, channel_id, next_retry_at FROM videos WHERE status='READY' AND upload_immediately=1 "
                      "AND youtube_video_id IS NULL ORDER BY id"):
        if v["channel_id"] not in linked or (v.get("next_retry_at") and parse(v["next_retry_at"]) > now):
            continue
        attempted.append(v["id"])
        pipeline.upload_immediate(v["id"])
    return attempted


def upload_backoff(attempt: int) -> dt.timedelta:
    return dt.timedelta(minutes=UPLOAD_BACKOFF_MIN[min(attempt - 1, len(UPLOAD_BACKOFF_MIN) - 1)])


def next_quota_reset(now: dt.datetime | None = None) -> dt.datetime:
    pt = ZoneInfo("America/Los_Angeles")
    n = (now or now_utc()).astimezone(pt)
    return dt.datetime.combine(n.date() + dt.timedelta(days=1), dt.time(0, 5), tzinfo=pt).astimezone(dt.timezone.utc)


# ------------------------------------------------------------------ rescheduling
def reschedule(video_id: int, when_utc: str) -> dict:
    v = db.get_video(video_id)
    if not v:
        raise ScheduleError("video not found")
    if v["status"] in ("UPLOADING", "PUBLISHED", "SCHEDULED"):
        raise ScheduleError(f"cannot reschedule a video that is {v['status']} (change it in YouTube Studio)")
    t = parse(when_utc)
    if t < now_utc() - dt.timedelta(minutes=1):
        raise ScheduleError("cannot schedule in the past")
    if not db.get_setting("allow_same_time"):
        clash = db.one("SELECT id, title, topic FROM videos WHERE scheduled_for=? AND id!=? AND status NOT IN ('FAILED')",
                       (iso(t), video_id))
        if clash:
            raise ScheduleError(f"video #{clash['id']} is already scheduled at that time")
    db.update_video(video_id, scheduled_for=iso(t), rescheduled=1)
    logs.info("scheduler", f"rescheduled to {iso(t)}", video_id)
    return db.get_video(video_id)


# ------------------------------------------------------------------ dashboard data
DONE_STATES = ("READY", "WAITING_FOR_APPROVAL", "SCHEDULED", "UPLOADING", "PUBLISHED")


def today_summary(now: dt.datetime | None = None) -> dict:
    now = now or now_utc()
    day = today_local(now)
    occ = occurrences(local_to_utc(day, "00:00"), local_to_utc(day + dt.timedelta(days=1), "00:00"))
    per_channel: dict[tuple, int] = {}
    for o in occ:
        if not o["slot"]["paused"]:
            k = (slot_channel(o["slot"]), o["slot"]["kind"])
            per_channel[k] = per_channel.get(k, 0) + 1
    slots_today = {kind: sum(n for (_, k), n in per_channel.items() if k == kind) for kind in ("short", "long")}
    out: dict[str, Any] = {"date": day.isoformat(), "timezone": str(tz())}
    for kind, key in (("short", "shorts_per_day"), ("long", "longs_per_day")):
        # daily limits apply per channel profile
        target = sum(min(int(db.get_setting(key)), n) for (_, k), n in per_channel.items() if k == kind)
        rows = db.query("SELECT status FROM videos WHERE slot_date=? AND kind=?", (day.isoformat(), kind))
        out[kind] = {"target": target, "slots": slots_today[kind],
                     "generated": sum(1 for r in rows if r["status"] in DONE_STATES),
                     "uploaded": sum(1 for r in rows if r["status"] in ("PUBLISHED", "SCHEDULED")),
                     "in_production": sum(1 for r in rows if r["status"] in ("DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK"))}
    out["pending_uploads"] = db.one("SELECT COUNT(*) n FROM videos WHERE status IN ('READY','WAITING_FOR_APPROVAL') "
                                    "AND youtube_video_id IS NULL")["n"]
    out["waiting_approval"] = db.one("SELECT COUNT(*) n FROM videos WHERE status='WAITING_FOR_APPROVAL'")["n"]
    out["failed_today"] = db.one("SELECT COUNT(*) n FROM videos WHERE status='FAILED' AND date(updated_at)>=date('now','-1 day')")["n"]
    nxt = db.one("SELECT id, title, topic, kind, scheduled_for, status FROM videos WHERE scheduled_for>? AND "
                 "status NOT IN ('PUBLISHED','SCHEDULED','FAILED','PAUSED') ORDER BY scheduled_for LIMIT 1", (iso(now),))
    upcoming = [o for o in occurrences(now, now + dt.timedelta(days=7)) if not o["slot"]["paused"]]
    next_slot = upcoming[0] if upcoming else None
    out["next_upload"] = nxt
    out["next_slot"] = {"label": next_slot["slot"]["label"], "kind": next_slot["slot"]["kind"],
                        "at": iso(next_slot["at"])} if next_slot else None
    total_target = out["short"]["target"] + out["long"]["target"]
    total_done = out["short"]["uploaded"] + out["long"]["uploaded"]
    out["progress"] = round(100 * total_done / total_target) if total_target else 0
    return out


def calendar(start: dt.date, end: dt.date) -> list[dict]:
    s_utc, e_utc = local_to_utc(start, "00:00"), local_to_utc(end + dt.timedelta(days=1), "00:00")
    items = []
    for v in db.query("SELECT id, kind, topic, title, status, scheduled_for, published_at, youtube_url, slot_id, slot_date, "
                      "rescheduled, thumbnail_path FROM videos WHERE (scheduled_for>=? AND scheduled_for<?) OR "
                      "(scheduled_for IS NULL AND published_at>=? AND published_at<?)",
                      (iso(s_utc), iso(e_utc), iso(s_utc), iso(e_utc))):
        at = v["scheduled_for"] or v["published_at"]
        items.append({**v, "at": at, "local_date": parse(at).astimezone(tz()).date().isoformat(),
                      "local_time": parse(at).astimezone(tz()).strftime("%H:%M"), "virtual": False})
    taken = {(v["slot_id"], v["slot_date"]) for v in items if v["slot_id"]}
    taken |= {(r["slot_id"], r["slot_date"]) for r in db.query("SELECT slot_id, slot_date FROM videos WHERE slot_id IS NOT NULL")}
    for o in occurrences(max(s_utc, now_utc()), e_utc):
        if (o["slot"]["id"], o["date"]) in taken:
            continue
        items.append({"id": None, "kind": o["slot"]["kind"], "title": o["slot"]["label"], "topic": None,
                      "status": "PAUSED" if o["slot"]["paused"] else "UPCOMING", "at": iso(o["at"]),
                      "local_date": o["date"], "local_time": o["slot"]["time_local"], "virtual": True,
                      "slot_id": o["slot"]["id"]})
    return sorted(items, key=lambda i: i["at"])


# ------------------------------------------------------------------ Windows autostart
def _startup_file() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AutoTube AI.vbs"


_SYSTEMD_UNIT = Path("/etc/systemd/system/autotube.service")


def autostart_enabled() -> bool:
    if sys.platform != "win32":      # Linux server: the systemd service starts AutoTube on boot
        return _SYSTEMD_UNIT.exists()
    return _startup_file().exists()


def set_autostart(enabled: bool) -> bool:
    if sys.platform != "win32":      # managed by deploy/install_server.sh (systemd), not from the dashboard
        return autostart_enabled()
    f = _startup_file()
    if enabled:
        bat = config.ROOT / "START_AUTOTUBE.bat"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('Set sh = CreateObject("WScript.Shell")\r\n'
                     f'sh.CurrentDirectory = "{config.ROOT}"\r\n'
                     f'sh.Run """{bat}"" silent", 0, False\r\n', encoding="utf-8")
    else:
        f.unlink(missing_ok=True)
    return autostart_enabled()
