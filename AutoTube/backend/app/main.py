"""AutoTube AI - FastAPI backend. Serves the API under /api and the built dashboard at /."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import characters, config, db, llm, logs, pipeline, quota, remote_auth, scheduler, scheduling
from .agents import genres, research
from .youtube import api as yt
from .youtube import oauth


@asynccontextmanager
async def lifespan(app: FastAPI):
    applied = db.migrate()
    if applied:
        logs.info("db", f"applied migrations {applied} (backup saved in data/backups)")
    oauth._migrate_legacy_file()
    rec = pipeline.start_worker()
    logs.info("app", f"recovered queue: {rec}")
    scheduler.start()
    logs.info("app", f"AutoTube started on {config.BASE_URL}")
    yield
    scheduler.shutdown()


app = FastAPI(title="AutoTube AI", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(remote_auth.middleware)      # every page and API call needs a logged-in session


@app.middleware("http")
async def no_stale_pages(request, call_next):
    """Dashboard pages and API data are always re-checked, so a rebuilt dashboard shows up on the next load (old
    browsers otherwise keep showing a cached menu). Hashed /_next/ assets keep normal caching."""
    resp = await call_next(request)
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("text/html") or request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if remote_auth.valid(request.cookies.get(remote_auth.COOKIE)):
        return RedirectResponse(remote_auth._safe_next(next), status_code=303)
    return remote_auth.login_page(next)


@app.post("/api/login")
async def login_submit(request: Request):
    return await remote_auth.login(request)


@app.api_route("/api/logout", methods=["GET", "POST"])
def logout():
    return remote_auth.logout()


@app.get("/api/ping")
def ping():
    """Liveness probe for the start scripts (no login, reveals nothing)."""
    return {"ok": True}

VIDEO_COLS = ("id, channel_id, kind, topic, title, status, stage, attempts, upload_attempts, next_retry_at, duration_sec, "
              "youtube_video_id, youtube_url, privacy, target_privacy, scheduled_for, slot_id, slot_date, published_at, "
              "rescheduled, approved_at, error, created_at, updated_at, quality_json, thumbnail_path, "
              "yt_privacy, yt_publish_at, yt_upload_status, yt_checked_at, thumbnail_status, thumbnail_error, "
              "genre, upload_immediately, skip_review")


def _by_kind() -> dict:
    """Separate Shorts / Long counters for the dashboard."""
    out = {}
    for kind in ("short", "long"):
        rows = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM videos WHERE kind=? GROUP BY status", (kind,))}
        out[kind] = {
            "generated": sum(rows.get(s, 0) for s in ("READY", "WAITING_FOR_APPROVAL", "UPLOADING", "SCHEDULED", "PUBLISHED")),
            "in_production": sum(rows.get(s, 0) for s in ("DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK")),
            "published": db.one("SELECT COUNT(*) n FROM videos WHERE kind=? AND status='PUBLISHED' AND yt_privacy='public'", (kind,))["n"],
            "uploaded_not_public": db.one("SELECT COUNT(*) n FROM videos WHERE kind=? AND status='PUBLISHED' AND "
                                          "COALESCE(yt_privacy,'')!='public'", (kind,))["n"],
            "scheduled": rows.get("SCHEDULED", 0),
            "failed": rows.get("FAILED", 0),
        }
    return out


# ------------------------------------------------------------------ system
@app.get("/api/health")
def health():
    voices = [p.stem for p in config.VOICES_DIR.glob("*.onnx")]
    du = shutil.disk_usage(config.DATA_DIR)
    return {
        "ffmpeg": bool(config.FFMPEG), "ffmpeg_path": config.FFMPEG,
        "ollama": llm.ollama_status(), "ollama_model": config.OLLAMA_MODEL, "ollama_writer": config.OLLAMA_MODEL_WRITER,
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "pexels_configured": bool(config.PEXELS_API_KEY), "pixabay_configured": bool(config.PIXABAY_API_KEY),
        "wikimedia_contact": bool(__import__("os").getenv("WIKIMEDIA_CONTACT", "").strip()),
        "kokoro": (config.VOICES_DIR / "kokoro" / "kokoro-v1.0.onnx").exists(), "piper_voices": voices,
        "youtube_client_configured": oauth.client_configured(),
        "youtube_connected": oauth.any_connected(), "youtube_status": oauth.overall_status(),
        "youtube_accounts": [{"profile_id": c["profile_id"], "profile_name": c["profile_name"], "status": c["status"],
                              "channel_title": c.get("channel_title")} for c in oauth.connections()],
        "disk_free_gb": round(du.free / 1e9, 1),
        "emergency_stop": db.get_setting("emergency_stop"),
        "automation_paused": db.get_setting("automation_paused"),
        "automation_enabled": db.get_setting("automation_enabled"),
        "autostart": scheduling.autostart_enabled(),
    }


@app.get("/api/dashboard")
def dashboard():
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM videos GROUP BY status")}
    recent = db.query(f"SELECT {VIDEO_COLS} FROM videos ORDER BY id DESC LIMIT 8")
    uploads = db.query("SELECT v.id, v.title, v.kind, v.youtube_url, v.privacy, v.yt_privacy, v.published_at, v.status, "
                       "v.thumbnail_status FROM videos v "
                       "WHERE v.youtube_video_id IS NOT NULL ORDER BY v.published_at DESC LIMIT 6")
    thumbs_blocked = db.one("SELECT COUNT(*) n FROM videos WHERE thumbnail_status='unavailable'")["n"]
    return {"counts": counts, "by_kind": _by_kind(), "thumbnails_unavailable": thumbs_blocked,
            "can_manage_videos": all(c["can_manage_videos"] for c in oauth.connections() if c["connected"]),
            "recent": recent, "recent_uploads": uploads, "pipeline": pipeline.status(),
            "quota": quota.summary(), "jobs": scheduler.jobs(), "settings": db.all_settings(),
            "today": scheduling.today_summary(), "connection": oauth.connection(), "accounts": oauth.connections(),
            "unread": db.one("SELECT COUNT(*) n FROM notifications WHERE read=0")["n"],
            "channels": db.query("SELECT id, name, niche FROM channels")}


class SettingsIn(BaseModel):
    values: dict[str, Any]


PROTECTED = {"public_publishing_approved", "emergency_stop", "automation_paused", "oauth_client_id"}
RANGES = {"shorts_per_day": (0, 10), "longs_per_day": (0, 5), "max_daily_videos": (0, 15),
          "max_concurrent_renders": (1, 4), "upload_retry_count": (1, 10), "max_retries": (1, 6),
          "min_quality_score": (0, 100), "generate_ahead_hours": (1, 48),
          "production_lead_minutes": (15, 720)}
ENUMS = {"upload_mode": ("automatic", "approval", "manual"), "default_privacy": ("private", "unlisted", "public")}


@app.get("/api/settings")
def get_settings():
    s = db.all_settings()
    s.pop("oauth_client_id", None)
    return s


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    for k, v in body.values.items():
        if k in PROTECTED:
            raise HTTPException(400, f"'{k}' must be changed via its dedicated control")
        if k not in db.DEFAULT_SETTINGS:
            raise HTTPException(400, f"unknown setting '{k}'")
        if k in RANGES:
            lo, hi = RANGES[k]
            if not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise HTTPException(400, f"{k} must be between {lo} and {hi}")
        if k in ENUMS and v not in ENUMS[k]:
            raise HTTPException(400, f"{k} must be one of {ENUMS[k]}")
        if k == "growth_genres" and (not isinstance(v, list) or not v or any(not genres.get(g) for g in v)):
            raise HTTPException(400, "growth_genres must be a non-empty list of known genres")
        if k == "timezone":
            try:
                ZoneInfo(v)
            except Exception:
                raise HTTPException(400, "unknown timezone")
    merged = {**db.all_settings(), **body.values}
    dp = merged.get("default_privacy")
    if (dp == "private" and not merged.get("allow_private_uploads")) or (dp == "unlisted" and not merged.get("allow_unlisted_uploads")):
        raise HTTPException(400, f"enable 'Allow {dp} uploads' before making {dp} the default")
    for k, v in body.values.items():
        db.set_setting(k, v)
    if "max_concurrent_renders" in body.values:
        pipeline.ensure_workers()
    logs.info("settings", f"updated {list(body.values)}")
    return get_settings()


class StopIn(BaseModel):
    active: bool


@app.post("/api/emergency-stop")
def emergency_stop(body: StopIn):
    db.set_setting("emergency_stop", body.active)
    if body.active:
        db.set_setting("automation_enabled", False)
        db.set_setting("auto_upload", False)
        logs.notify("WARNING", "EMERGENCY STOP activated", "Production halted between stages; all uploads blocked.")
    else:
        logs.notify("INFO", "Emergency stop released", "Automatic generation and uploading stay OFF until you re-enable them.")
        for r in db.query("SELECT id, stage FROM videos WHERE status='PAUSED'"):
            if r["stage"] != "done":
                pipeline.enqueue(r["id"])
    return {"emergency_stop": body.active}


class PauseIn(BaseModel):
    paused: bool


@app.post("/api/automation/pause")
def pause_automation(body: PauseIn):
    db.set_setting("automation_paused", body.paused)
    logs.notify("INFO", "Automation paused" if body.paused else "Automation resumed",
                "No new videos are planned or uploaded while paused; running jobs finish." if body.paused else "")
    return {"automation_paused": body.paused}


class StockKeysIn(BaseModel):
    pexels: str = ""
    pixabay: str = ""
    wikimedia_contact: str = ""


def _check_stock_key(provider: str, key: str) -> None:
    """Live test call so a typo is caught now, not mid-video."""
    import requests
    if provider == "pexels":
        r = requests.get("https://api.pexels.com/videos/search", params={"query": "ocean", "per_page": 1},
                         headers={"Authorization": key}, timeout=20)
    else:
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": "ocean", "per_page": 3}, timeout=20)
    if r.status_code in (400, 401, 403):
        raise HTTPException(400, f"{provider.title()} rejected this key - copy it again from your {provider} account")
    r.raise_for_status()


@app.post("/api/stock-keys")
def stock_keys(body: StockKeysIn):
    """Save free Pexels / Pixabay keys (real stock video footage). Each key is verified with a live call first."""
    from . import envfile
    saved = []
    for provider, key in (("pexels", body.pexels.strip()), ("pixabay", body.pixabay.strip())):
        if not key:
            continue
        try:
            _check_stock_key(provider, key)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Could not reach {provider} to test the key: {e}")
        name = f"{provider.upper()}_API_KEY"
        envfile.set_values({name: key})
        setattr(config, name, key)
        saved.append(provider)
    contact = body.wikimedia_contact.strip().replace(")", "").replace("(", "")[:120]
    if contact:
        if not ("@" in contact or contact.startswith(("http://", "https://"))):
            raise HTTPException(400, "Wikimedia contact must be an email address or a website link (https://...)")
        from .media import visuals
        envfile.set_values({"WIKIMEDIA_CONTACT": contact})
        visuals.UA["User-Agent"] = f"AutoTubeAI/1.0 (personal local video tool; low volume; {contact})"
        saved.append("Wikimedia archive film")
    if not saved:
        raise HTTPException(400, "Paste at least one key or a contact")
    logs.info("settings", f"stock video keys saved: {', '.join(saved)}")
    return {"saved": saved, "pexels_configured": bool(config.PEXELS_API_KEY),
            "pixabay_configured": bool(config.PIXABAY_API_KEY)}


class ApproveIn(BaseModel):
    approve: bool
    confirm_text: str = ""


@app.post("/api/public-publishing")
def public_publishing(body: ApproveIn):
    if body.approve and body.confirm_text.strip() != "I APPROVE PUBLIC PUBLISHING":
        raise HTTPException(400, "Type exactly: I APPROVE PUBLIC PUBLISHING")
    db.set_setting("public_publishing_approved", body.approve)
    logs.notify("WARNING" if body.approve else "INFO", "Public publishing " + ("APPROVED" if body.approve else "revoked"))
    return {"public_publishing_approved": body.approve}


class AutostartIn(BaseModel):
    enabled: bool


@app.get("/api/system/autostart")
def get_autostart():
    return {"enabled": scheduling.autostart_enabled()}


@app.post("/api/system/autostart")
def set_autostart(body: AutostartIn):
    return {"enabled": scheduling.set_autostart(body.enabled)}


# ------------------------------------------------------------------ channels
class ChannelIn(BaseModel):
    name: str
    niche: str
    audience: str = ""
    tone: str = "curious, warm, energetic"
    long_minutes: int = 6


@app.get("/api/channels")
def list_channels():
    return db.query("SELECT * FROM channels ORDER BY id")


@app.post("/api/channels")
def create_channel(c: ChannelIn):
    cid = db.execute("INSERT INTO channels(name, niche, audience, tone, long_minutes) VALUES (?,?,?,?,?)",
                     (c.name, c.niche, c.audience, c.tone, max(2, min(20, c.long_minutes))))
    return db.one("SELECT * FROM channels WHERE id=?", (cid,))


@app.put("/api/channels/{cid}")
def update_channel(cid: int, c: ChannelIn):
    db.execute("UPDATE channels SET name=?, niche=?, audience=?, tone=?, long_minutes=? WHERE id=?",
               (c.name, c.niche, c.audience, c.tone, max(2, min(20, c.long_minutes)), cid))
    return db.one("SELECT * FROM channels WHERE id=?", (cid,))


@app.post("/api/channels/{cid}/suggest-topics")
def suggest(cid: int, count: int = 5, genre: str | None = None):
    ch = db.one("SELECT * FROM channels WHERE id=?", (cid,))
    if not ch:
        raise HTTPException(404)
    if genre and not genres.get(genre):
        raise HTTPException(400, f"unknown genre '{genre}'")
    return {"topics": research.suggest_topics(ch, max(1, min(12, count)), genre=genre or None)}


@app.get("/api/genres")
def list_genres():
    return {"genres": genres.public_list()}


# ------------------------------------------------------------------ schedule slots
class SlotIn(BaseModel):
    label: str = ""
    kind: Literal["short", "long"] | None = None
    time_local: str | None = None
    days: str | None = None
    enabled: bool | None = None
    paused: bool | None = None
    channel_id: int | None = None


@app.get("/api/slots")
def get_slots():
    return {"slots": scheduling.list_slots(), "timezone": str(scheduling.tz())}


@app.post("/api/slots")
def add_slot(s: SlotIn):
    try:
        return scheduling.create_slot(s.label, s.kind or "short", s.time_local or "", s.days or ",".join(scheduling.DAYS),
                                      True if s.enabled is None else s.enabled, s.channel_id)
    except scheduling.ScheduleError as e:
        raise HTTPException(400, str(e))


@app.put("/api/slots/{sid}")
def edit_slot(sid: int, s: SlotIn):
    try:
        return scheduling.update_slot(sid, label=s.label or None, kind=s.kind, time_local=s.time_local, days=s.days,
                                      enabled=s.enabled, paused=s.paused, channel_id=s.channel_id)
    except scheduling.ScheduleError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/slots/{sid}")
def remove_slot(sid: int):
    scheduling.delete_slot(sid)
    return {"ok": True}


# ------------------------------------------------------------------ AI characters
class CategoryIn(BaseModel):
    name: str | None = None
    icon: str | None = None
    description: str | None = None


class CharacterIn(BaseModel):
    name: str | None = None
    category_id: int | None = None
    channel_id: int | None = None
    gender: str | None = None
    age_range: str | None = None
    style: str | None = None
    appearance: str | None = None
    personality: str | None = None
    voice: str | None = None
    original_confirmed: bool | None = None
    enabled: bool | None = None
    status: str | None = None


def _char_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except characters.CharacterError as e:
        raise HTTPException(400, str(e))


@app.get("/api/characters")
def get_characters():
    return {"characters": characters.list_characters(), "categories": characters.list_categories(),
            "voices": characters.VOICES, "voice_genders": {v: characters.voice_gender(v) for v in characters.VOICES},
            "styles": characters.STYLES, "genders": list(characters.GENDERS),
            "age_ranges": list(characters.AGE_RANGES), "engine": characters.engine_status(),
            "modes": [{"id": k, "label": lbl, "characters": n, "available": ok}
                      for k, (lbl, n, ok) in characters.MODES.items()],
            "videos": db.query("SELECT id, kind, topic, title, status, character_ids, character_mode, scheduled_for, "
                               "youtube_url, created_at FROM videos WHERE character_ids IS NOT NULL "
                               "ORDER BY id DESC LIMIT 30")}


@app.post("/api/characters")
def add_character(body: CharacterIn):
    return _char_call(characters.create_character, **body.model_dump(exclude_unset=True))


@app.put("/api/characters/{char_id}")
def edit_character(char_id: int, body: CharacterIn):
    return _char_call(characters.update_character, char_id, body.model_dump(exclude_unset=True))


@app.delete("/api/characters/{char_id}")
def remove_character(char_id: int):
    _char_call(characters.delete_character, char_id)
    return {"ok": True}


@app.get("/api/characters/voice-preview")
def character_voice_preview(voice: str, name: str = ""):
    try:
        path = characters.voice_preview(voice, name)
    except characters.CharacterError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(503, f"voice engine unavailable: {e}")
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/character-categories")
def add_character_category(body: CategoryIn):
    return _char_call(characters.create_category, **body.model_dump(exclude_unset=True))


@app.put("/api/character-categories/{cat_id}")
def edit_character_category(cat_id: int, body: CategoryIn):
    return _char_call(characters.update_category, cat_id, **body.model_dump(exclude_unset=True))


@app.delete("/api/character-categories/{cat_id}")
def remove_character_category(cat_id: int):
    _char_call(characters.delete_category, cat_id)
    return {"ok": True}


@app.post("/api/scheduler/plan-now")
def plan_now():
    if db.get_setting("emergency_stop"):
        raise HTTPException(409, "Emergency stop is active")
    return {"created": scheduling.plan(force=True)}


@app.get("/api/today")
def today():
    return scheduling.today_summary()


@app.get("/api/calendar")
def get_calendar(start: str, end: str):
    try:
        s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "dates must be YYYY-MM-DD")
    if (e - s).days > 62:
        raise HTTPException(400, "range too large")
    return {"items": scheduling.calendar(s, e), "timezone": str(scheduling.tz())}


# ------------------------------------------------------------------ videos & queue
class VideoIn(BaseModel):
    channel_id: int
    kind: Literal["short", "long"]
    topic: str = ""
    scheduled_for: str | None = None
    genre: str | None = None
    upload_immediately: bool = False     # upload as soon as production + quality checks pass
    skip_review: bool = False            # immediate only: skip approval-mode review when ALL quality checks pass
    character_ids: list[int] = []        # AI character host(s), in speaking order
    character_mode: str | None = None    # narrator | podcast


@app.post("/api/videos")
def create_video(body: VideoIn):
    if db.get_setting("emergency_stop"):
        raise HTTPException(409, "Emergency stop is active")
    if body.character_ids or body.character_mode:
        try:
            characters.validate_cast(body.character_mode or "", body.character_ids)
        except characters.CharacterError as e:
            raise HTTPException(400, str(e))
    genre = body.genre or None
    if genre and not genres.get(genre):
        raise HTTPException(400, f"unknown genre '{genre}'")
    if body.upload_immediately and body.scheduled_for:
        raise HTTPException(400, "choose either 'upload immediately' or a scheduled time, not both")
    topic = body.topic.strip()
    if not topic:
        ch = db.one("SELECT * FROM channels WHERE id=?", (body.channel_id,))
        if not ch:
            raise HTTPException(404, "channel not found")
        ideas = research.suggest_topics(ch, 1, genre=genre)
        if not ideas:
            raise HTTPException(500, "could not find a fresh topic")
        topic = ideas[0]
    else:
        try:
            if research.verify_topic(topic) is None:
                raise HTTPException(400, f"No Wikipedia source found for '{topic}', so it can't be fact-checked. Use the "
                                         "name of a real event, person or place, or click 'Suggest topics'.")
        except HTTPException:
            raise
        except Exception as e:                      # Wikipedia unreachable: let the research stage retry later
            logs.warn("research", f"could not verify topic '{topic}' now: {e}")
    try:
        vid = pipeline.create_video(body.channel_id, body.kind, topic, genre=genre,
                                    upload_immediately=body.upload_immediately, skip_review=body.skip_review,
                                    character_ids=body.character_ids or None,
                                    character_mode=body.character_mode if body.character_ids else None)
        if genre:
            from .agents import topics as topic_bank
            topic_bank.take(genre, body.channel_id, topic)
        if body.scheduled_for:
            scheduling.reschedule(vid, body.scheduled_for)
    except (ValueError, scheduling.ScheduleError) as e:
        raise HTTPException(409, str(e))
    pipeline.enqueue(vid)
    out = db.get_video(vid)
    if body.upload_immediately:
        out["upload_warning"] = pipeline.immediate_block_reason(body.channel_id)
    return out


@app.get("/api/immediate")
def immediate_videos():
    """Videos created with 'upload immediately', newest first, with what (if anything) blocks the upload."""
    return {"videos": db.query(f"SELECT {VIDEO_COLS} FROM videos WHERE upload_immediately=1 ORDER BY id DESC LIMIT 40"),
            "blocked": pipeline.immediate_block_reason(), "pipeline": pipeline.status(),
            "upload_mode": db.get_setting("upload_mode")}


@app.get("/api/videos")
def list_videos(status: str | None = None, limit: int = 300):
    if status:
        return db.query(f"SELECT {VIDEO_COLS} FROM videos WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
    return db.query(f"SELECT {VIDEO_COLS} FROM videos ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/queue")
def upload_queue():
    """Persistent upload queue: everything not yet published, ordered by slot time, plus recent results."""
    pending = db.query(f"SELECT {VIDEO_COLS} FROM videos WHERE status NOT IN ('PUBLISHED','SCHEDULED') "
                       "ORDER BY CASE WHEN scheduled_for IS NULL THEN 1 ELSE 0 END, scheduled_for, id")
    done = db.query(f"SELECT {VIDEO_COLS} FROM videos WHERE status IN ('PUBLISHED','SCHEDULED') "
                    "ORDER BY published_at DESC LIMIT 30")
    return {"pending": pending, "done": done, "timezone": str(scheduling.tz()), "mode": db.get_setting("upload_mode")}


@app.get("/api/videos/{vid}")
def get_video(vid: int):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    v["uploads"] = db.query("SELECT * FROM uploads WHERE video_id=? ORDER BY id DESC", (vid,))
    v["logs"] = db.query("SELECT level, source, message, created_at FROM logs WHERE video_id=? ORDER BY id DESC LIMIT 100", (vid,))
    return v


class RetryIn(BaseModel):
    from_stage: str | None = None


@app.post("/api/videos/{vid}/retry")
def retry(vid: int, body: RetryIn):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    if v["status"] in ("UPLOADING", "PUBLISHED", "SCHEDULED"):
        raise HTTPException(409, f"video is {v['status']}")
    if body.from_stage:
        if body.from_stage not in pipeline.STAGES:
            raise HTTPException(400, "bad stage")
        db.update_video(vid, stage=body.from_stage, attempts=0, error=None, status="DRAFT", next_retry_at=None)
        pipeline.enqueue(vid)
        return {"ok": True, "action": "regenerate"}
    if v["stage"] in ("done", "upload") and v.get("video_path"):
        # upload retry (e.g. after an interrupted or failed upload): back to READY, attempts reset
        db.update_video(vid, status="READY" if (v.get("quality_json") or {}).get("approved_by_user") or
                        pipeline.post_qc_status(v) == "READY" else "WAITING_FOR_APPROVAL",
                        upload_attempts=0, next_retry_at=None, error=None)
        return {"ok": True, "action": "upload_retry"}
    db.update_video(vid, attempts=0, error=None, status="DRAFT", next_retry_at=None)
    pipeline.enqueue(vid)
    return {"ok": True, "action": "production_retry"}


@app.post("/api/videos/{vid}/approve")
def approve(vid: int):
    v = db.get_video(vid)
    if not v or v["status"] != "WAITING_FOR_APPROVAL":
        raise HTTPException(400, "video is not waiting for approval")
    q = v.get("quality_json") if isinstance(v.get("quality_json"), dict) else {}
    q["approved_by_user"] = True
    db.update_video(vid, status="READY", quality_json=q,
                    approved_at=scheduling.iso(scheduling.now_utc()))
    logs.info("review", "approved by user", vid)
    if v.get("upload_immediately"):
        pipeline.upload_immediate_async(vid)
        reason = pipeline.immediate_block_reason(v["channel_id"])
        return {"ok": True, "uploading": reason is None, "upload_warning": reason}
    return {"ok": True}


@app.post("/api/videos/{vid}/reject")
def reject(vid: int):
    v = db.get_video(vid)
    if not v or v["status"] not in ("WAITING_FOR_APPROVAL", "READY"):
        raise HTTPException(400, "only unpublished finished videos can be rejected")
    db.update_video(vid, status="PAUSED", error="rejected by user")
    return {"ok": True}


@app.post("/api/videos/{vid}/upload")
def upload_now(vid: int):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    if v["status"] == "WAITING_FOR_APPROVAL":
        raise HTTPException(400, "approve the video after reviewing it first")
    if v["status"] != "READY":
        raise HTTPException(400, f"video must be READY (current: {v['status']})")
    try:
        return pipeline.do_upload(vid)
    except Exception as e:
        raise HTTPException(502, str(e))


class PauseVideoIn(BaseModel):
    paused: bool


@app.post("/api/videos/{vid}/pause")
def pause_video(vid: int, body: PauseVideoIn):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    if body.paused:
        if v["status"] in ("UPLOADING", "PUBLISHED", "SCHEDULED"):
            raise HTTPException(409, f"video is {v['status']}")
        db.update_video(vid, status="PAUSED")
    else:
        if v["status"] != "PAUSED":
            raise HTTPException(409, "video is not paused")
        if v["stage"] in ("done", "upload"):
            db.update_video(vid, status=pipeline.post_qc_status(v), error=None)
        else:
            db.update_video(vid, status="DRAFT", error=None)
            pipeline.enqueue(vid)
    return db.get_video(vid)


class ScheduleIn(BaseModel):
    scheduled_for: str | None


@app.put("/api/videos/{vid}/schedule")
def schedule_video(vid: int, body: ScheduleIn):
    try:
        if body.scheduled_for is None:
            db.update_video(vid, scheduled_for=None)
            return db.get_video(vid)
        return scheduling.reschedule(vid, body.scheduled_for)
    except scheduling.ScheduleError as e:
        raise HTTPException(400, str(e))


class PrivacyIn(BaseModel):
    target_privacy: Literal["private", "unlisted", "public"]


@app.put("/api/videos/{vid}/privacy")
def video_privacy(vid: int, body: PrivacyIn):
    v = db.get_video(vid)
    if not v or v["youtube_video_id"]:
        raise HTTPException(400, "privacy of an uploaded video is changed on YouTube (use Publish now)")
    if body.target_privacy not in yt.allowed_privacies():
        raise HTTPException(400, f"{body.target_privacy} uploads are disabled in Scheduler > Publishing settings")
    db.update_video(vid, target_privacy=body.target_privacy)
    return {"ok": True}


@app.post("/api/videos/{vid}/publish-now")
def publish_now(vid: int):
    """Explicit user action: make an already-uploaded video public with videos.update (no re-upload)."""
    v = db.get_video(vid)
    if not v or not v.get("youtube_video_id"):
        raise HTTPException(400, "this video has not been uploaded yet")
    if db.get_setting("emergency_stop"):
        raise HTTPException(409, "Emergency stop is active")
    try:
        return pipeline.publish_now(vid)
    except Exception as e:
        logs.error("youtube", f"publish now failed: {e}", vid)
        raise HTTPException(502, str(e))


@app.post("/api/videos/{vid}/thumbnail")
def thumbnail_retry(vid: int):
    v = db.get_video(vid)
    if not v or not v.get("youtube_video_id"):
        raise HTTPException(400, "upload the video first")
    try:
        r = pipeline.retry_thumbnail(vid)
    except Exception as e:
        raise HTTPException(502, str(e))
    if r["status"] != "set":
        raise HTTPException(502, f"Custom thumbnail unavailable: {r['error']}")
    return r


@app.post("/api/youtube/refresh-status")
def refresh_status(video_id: int | None = None):
    """Read the actual privacy / publish state of uploaded videos from YouTube."""
    try:
        return pipeline.refresh_youtube_status([video_id] if video_id else None)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    if v["status"] in ("GENERATING", "RENDERING", "QUALITY_CHECK", "UPLOADING"):
        raise HTTPException(409, "video is being processed")
    work = config.OUTPUT_DIR / f"{vid:05d}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    db.execute("DELETE FROM uploads WHERE video_id=?", (vid,))
    db.execute("DELETE FROM videos WHERE id=?", (vid,))
    return {"ok": True}


@app.get("/api/videos/{vid}/file/{kind}")
def video_file(vid: int, kind: Literal["video", "thumbnail", "srt"]):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    p = {"video": v.get("video_path"), "thumbnail": v.get("thumbnail_path"), "srt": v.get("subtitle_path")}[kind]
    if not p or not Path(p).exists():
        raise HTTPException(404, "file not generated yet")
    if not Path(p).resolve().is_relative_to(config.OUTPUT_DIR):
        raise HTTPException(403)
    return FileResponse(p)


@app.get("/api/pipeline/status")
def pipe_status():
    return pipeline.status()


# ------------------------------------------------------------------ youtube connection
@app.get("/api/youtube/status")
def yt_status(refresh: bool = False):
    cid = oauth.client_id()
    for pid in oauth.connected_profiles():            # live name / handle / counts (cached 1 h, 1 quota unit)
        yt.channel_details(pid, refresh=refresh)
    return {"setup_required": not oauth.client_configured(),
            "client_configured": oauth.client_configured(),
            "client_id_masked": (cid[:10] + "…" + cid.split("-")[-1][-30:]) if cid else "",
            "connection": oauth.connection(), "connected": oauth.any_connected(),
            "can_manage_videos": oauth.can_manage_videos(),
            "accounts": [{**a, "details": yt.channel_details(a["profile_id"]) if a["connected"] else None}
                         for a in oauth.connections()],
            "redirect_uri": oauth.redirect_uri(), "env_file": str(config.ENV_FILE),
            "public_publishing_approved": db.get_setting("public_publishing_approved")}


class ClientIn(BaseModel):
    client_id: str
    client_secret: str | None = None


@app.put("/api/youtube/setup")
def yt_setup(body: ClientIn):
    """One-time setup wizard: writes GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET to the local .env file."""
    try:
        oauth.save_client(body.client_id, body.client_secret)
    except oauth.OAuthError as e:
        raise HTTPException(400, str(e))
    return yt_status()


app.put("/api/youtube/config")(yt_setup)     # backward-compatible alias


@app.post("/api/youtube/client-secret")
async def yt_client_json(file: UploadFile = File(...)):
    """Advanced, optional: read ID + secret from Google's JSON into .env (not used by the normal UI)."""
    raw = await file.read()
    try:
        oauth.import_client_json(raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    return yt_status()


@app.get("/api/youtube/connect")
def yt_connect(channel: int | None = None):
    """Google sign-in for one channel profile (?channel=<profile id>; default: the first profile)."""
    if not oauth.client_configured():
        raise HTTPException(400, "Google OAuth app credentials are not set up yet. Complete the one-time setup on the YouTube page.")
    try:
        return RedirectResponse(oauth.authorization_url(channel, force_consent=True))
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/youtube/oauth/callback")
def yt_callback(request: Request, state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/youtube/?error={error}")
    try:
        oauth.handle_callback(str(request.url), state)
    except Exception as e:
        logs.error("oauth", f"callback failed: {type(e).__name__}: {str(e)[:300]}")
        from urllib.parse import quote
        return RedirectResponse(f"/youtube/?error={quote(str(e)[:300])}")
    return RedirectResponse("/youtube/?connected=1")


def _profile(channel: int | None) -> int | None:
    if channel is not None and not db.one("SELECT id FROM channels WHERE id=?", (channel,)):
        raise HTTPException(404, "channel profile not found")
    return channel


@app.post("/api/youtube/test")
def yt_test(channel: int | None = None):
    """Checks one profile's saved authorization works right now (refreshes the access token)."""
    creds = oauth.load(_profile(channel))
    if not creds:
        raise HTTPException(400, oauth.connection(channel).get("last_error") or "not connected")
    return {"ok": True, "connection": oauth.connection(channel)}


@app.post("/api/youtube/disconnect")
def yt_disconnect(channel: int | None = None):
    oauth.disconnect(_profile(channel))
    return {"ok": True}


# ------------------------------------------------------------------ analytics / logs
@app.get("/api/analytics")
def analytics(channel: int | None = None):
    cid = _profile(channel) or oauth.default_channel_id()
    latest = db.query("""SELECT v.id, v.title, v.kind, a.youtube_video_id, a.views, a.likes, a.comments, a.fetched_at
                         FROM analytics_snapshots a JOIN videos v ON v.youtube_video_id=a.youtube_video_id
                         WHERE a.id IN (SELECT MAX(id) FROM analytics_snapshots GROUP BY youtube_video_id)
                         AND v.channel_id=? ORDER BY a.views DESC""", (cid,))
    out: dict[str, Any] = {"videos": latest, "channel": None, "profile_id": cid,
                           "profiles": db.query("SELECT id, name, youtube_title FROM channels ORDER BY id")}
    try:
        out["channel"] = yt.channel_analytics(channel_id=cid)
    except Exception as e:
        out["channel_error"] = str(e)
    return out


@app.get("/api/my-youtube")
def my_youtube(channel: int | None = None, refresh: bool = False):
    """Every upload on the selected profile's YouTube channel with live stats (for the 'My YouTube' page)."""
    accounts = [{"id": c["profile_id"], "name": c["profile_name"], "youtube_title": c.get("channel_title"),
                 "connected": c["connected"]} for c in oauth.connections()]
    cid = _profile(channel) or next((a["id"] for a in accounts if a["connected"]), oauth.default_channel_id())
    out: dict[str, Any] = {"profile_id": cid, "accounts": accounts}
    if not oauth.is_connected(cid):
        out["error"] = "This profile has no connected YouTube account. Connect it on the Accounts page."
        return out
    try:
        out.update(yt.channel_library(cid, refresh=refresh))
    except Exception as e:
        out["error"] = str(e)[:400]
    return out


# ------------------------------------------------------------------ viral trends
class TrendIdeaIn(BaseModel):
    video: dict[str, Any]


class TrendMakeIn(BaseModel):
    channel_id: int
    topic: str = ""                        # empty: AutoTube picks it (trend subject, else best genre's top story)
    kinds: list[Literal["short", "long"]]
    mode: Literal["now", "schedule", "make"]
    when_local: str | None = None          # "YYYY-MM-DDTHH:MM" in the scheduler timezone (IST by default)
    genre: str | None = None
    source: dict[str, Any] | None = None
    skip_review: bool = False


@app.get("/api/trends/videos")
def trends_videos():
    """Videos made from the Viral Trends page, newest first (for its progress list)."""
    ids = [r["video_id"] for r in db.query("SELECT DISTINCT video_id FROM logs WHERE source='trends' AND video_id IS NOT NULL "
                                           "ORDER BY video_id DESC LIMIT 40")]
    vids = db.query(f"SELECT {VIDEO_COLS}, description, tags, seo_json FROM videos WHERE id IN "
                    f"({','.join('?' * len(ids))}) ORDER BY id DESC", tuple(ids)) if ids else []
    for v in vids:                       # upload kit: title, description, hashtags, tags (thumbnail via /file/thumbnail)
        try:
            seo = json.loads(v.pop("seo_json") or "{}")
            v["tags"] = json.loads(v["tags"]) if v.get("tags") else []
        except ValueError:
            seo = {}
        v["hashtags"] = seo.get("hashtags") or []
        v["title_options"] = seo.get("title_options") or []
    return {"videos": vids, "blocked": pipeline.immediate_block_reason(), "upload_mode": db.get_setting("upload_mode"),
            "auto_upload": db.get_setting("auto_upload")}


@app.get("/api/trends")
def trends_list(region: str = "US", category: str = "", mode: Literal["trending", "shorts", "search"] = "trending",
                refresh: bool = False, q: str = "", duration: Literal["any", "short", "long"] = "any"):
    from . import trends
    try:
        return trends.trending(region, category, mode, refresh, q, duration)
    except Exception as e:
        raise HTTPException(502, str(e)[:400])


@app.post("/api/trends/idea")
def trends_idea(body: TrendIdeaIn):
    from . import trends
    return trends.idea(body.video)


@app.post("/api/trends/make")
def trends_make(body: TrendMakeIn):
    """One click: returns at once; the topic is found and production starts in the background (see My videos)."""
    from . import trends
    try:
        out = trends.start(body.channel_id, body.topic.strip(), body.kinds, body.mode, body.when_local, body.source,
                           body.skip_review)
    except (ValueError, scheduling.ScheduleError) as e:
        raise HTTPException(409, str(e))
    out["upload_warning"] = pipeline.immediate_block_reason(body.channel_id) if body.mode == "now" else None
    return out


@app.get("/api/growth")
def growth_report(channel: int | None = None):
    """What is working on this channel (from its real YouTube numbers) + YouTube Partner Program progress."""
    from . import growth
    cid = _profile(channel) or oauth.default_channel_id()
    out: dict[str, Any] = {"profile_id": cid, "genres": {k: growth.genre_report(cid, k) for k in ("short", "long")},
                           "hours": growth.hour_report(cid),
                           "top_titles": {k: growth.top_examples(cid, k, "title", 5) for k in ("short", "long")},
                           "candidate_genres": growth.candidate_genres(),
                           "all_genres": genres.public_list(), "monetization": None}
    try:
        out["monetization"] = growth.monetization(cid)
    except Exception as e:
        out["monetization_error"] = str(e)[:300]
    return out


@app.post("/api/analytics/refresh")
def analytics_refresh():
    try:
        return {"updated": yt.refresh_stats()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/logs")
def get_logs(level: str | None = None, limit: int = 300):
    if level:
        return db.query("SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ?", (level.upper(), limit))
    return db.query("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/notifications")
def notifications():
    return db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT 100")


@app.post("/api/notifications/read")
def notifications_read():
    db.execute("UPDATE notifications SET read=1")
    return {"ok": True}


@app.get("/api/quota")
def get_quota():
    return {"summary": quota.summary(),
            "today": db.query("SELECT provider, operation, SUM(units) units, COUNT(*) calls FROM quota_usage "
                              "WHERE day>=date('now','-1 day') GROUP BY provider, operation")}


@app.get("/api/reports")
def reports():
    return [{**r, "report_json": json.loads(r["report_json"])} for r in
            db.query("SELECT * FROM daily_reports ORDER BY day DESC LIMIT 30")]


@app.post("/api/reports/generate")
def report_now():
    return pipeline.daily_report()


# ------------------------------------------------------------------ dashboard (static Next.js export)
if config.FRONTEND_OUT.exists():
    app.mount("/", StaticFiles(directory=config.FRONTEND_OUT, html=True), name="dashboard")
else:
    @app.get("/")
    def no_frontend():
        return HTMLResponse("<h2>AutoTube API is running.</h2><p>Dashboard not built yet: run scripts\\setup.ps1</p>"
                            "<p><a href='/docs'>API docs</a></p>")
