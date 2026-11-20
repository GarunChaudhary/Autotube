"""SQLite access layer + migration runner."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_lock = threading.RLock()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _backup_before_migration(name: str) -> None:
    import datetime as _dt
    if not config.DB_PATH.exists() or config.DB_PATH.stat().st_size == 0:
        return
    dest = config.BACKUP_DIR / f"pre-migration-{name}-{_dt.datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def migrate() -> list[str]:
    """Apply pending migrations/NNN_*.sql, each atomically, after backing up the database."""
    applied: list[str] = []
    with _lock:
        conn = connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))")
            conn.commit()
            done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
            pending = [f for f in sorted(config.MIGRATIONS_DIR.glob("*.sql")) if f.name not in done]
            has_data = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='videos'").fetchone()[0] > 0
            for f in pending:
                if has_data:
                    _backup_before_migration(f.stem)
                sql = f.read_text(encoding="utf-8")
                safe_name = f.name.replace("'", "''")
                try:
                    conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_migrations(name) VALUES ('{safe_name}');\nCOMMIT;")
                except Exception:
                    conn.execute("ROLLBACK") if conn.in_transaction else None
                    raise
                applied.append(f.name)
                has_data = True
        finally:
            conn.close()
    return applied


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def execute_rowcount(sql: str, params: tuple | list = ()) -> int:
    """Run an UPDATE/DELETE and return affected rows (used for atomic state claims)."""
    with tx() as c:
        return c.execute(sql, params).rowcount


# ---- settings (key/value JSON) -------------------------------------------
DEFAULT_SETTINGS: dict[str, Any] = {
    # --- automation switches
    "automation_enabled": False,       # automatic video GENERATION for scheduled slots
    "auto_upload": False,              # automatic UPLOADING of approved/ready videos at their slot
    "automation_paused": False,        # soft pause: no new generation or uploads; running jobs finish
    "emergency_stop": False,           # hard stop: halts pipeline between stages and all uploads
    "upload_mode": "approval",         # automatic | approval | manual
    "public_publishing_approved": False,  # user must explicitly approve public publishing
    # --- daily production
    "timezone": "Asia/Kolkata",
    "shorts_per_day": 3,               # 0-10
    "longs_per_day": 1,                # 0-5
    "max_daily_videos": 4,             # daily total limit (spending/volume control)
    "generate_ahead_hours": 18,        # legacy (no longer used; see production_lead_minutes)
    # --- growth (learns from this channel's own YouTube numbers)
    "growth_auto_genre": True,         # planner picks each slot's genre from what earns the most views
    "growth_genres": ["horror", "true_crime", "mystery", "history", "science"],   # genres the planner may use
    "engagement_comment": True,        # post one question comment on each new public video
    "subscribe_prompt": True,          # silent on-screen follow/subscribe prompt (Shorts: last seconds)
    "slot_auto_publish": False,        # True: slot videos upload at their slot without waiting for approval
    "production_lead_minutes": 180,    # start producing a slot's video this many minutes (3 h) before the slot,
                                       # leaving time for retries and for your approval before it publishes
    "max_concurrent_renders": 1,       # parallel production workers (CPU heavy)
    "allow_same_time": False,          # allow two slots / videos at the same minute
    "default_privacy": "public",       # public | unlisted | private
    "scheduled_publishing": True,      # public + future slot -> upload early as private with publishAt
    "allow_private_uploads": False,    # if off, a video can never be left private by accident
    "allow_unlisted_uploads": False,
    # --- reliability
    "max_retries": 3,                  # production retries per video
    "upload_retry_count": 3,           # upload retries (exponential backoff)
    "llm_provider_order": ["ollama", "gemini", "template"],
    "visual_provider_order": ["pexels", "pixabay", "wikimedia", "openverse", "nasa"],
    "voice_short": "am_michael",          # Kokoro voices: am_michael, am_fenrir, af_heart, bm_george, bm_fable...
    "voice_long": "bm_george",
    "min_quality_score": 70,
}


def get_setting(key: str) -> Any:
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(row["value"])


def set_setting(key: str, value: Any) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for r in query("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out


def update_video(video_id: int, **fields: Any) -> None:
    if not fields:
        return
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v, ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE videos SET {cols}, updated_at=datetime('now') WHERE id=?", (*fields.values(), video_id))


def get_video(video_id: int) -> dict[str, Any] | None:
    v = one("SELECT * FROM videos WHERE id=?", (video_id,))
    if v:
        for k in ("research_json", "script_json", "factcheck_json", "storyboard_json", "seo_json", "quality_json"):
            if v.get(k):
                try:
                    v[k] = json.loads(v[k])
                except json.JSONDecodeError:
                    pass
    return v
