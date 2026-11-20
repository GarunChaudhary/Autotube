"""API quota / spending controls. Tracks usage per provider per (Pacific-time for YouTube) day."""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

from . import config, db

# YouTube quota resets at midnight Pacific time.
try:
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:  # tzdata missing -> approximate with fixed UTC-8
    _PT = dt.timezone(dt.timedelta(hours=-8))

LIMITS = {
    "youtube": config.YOUTUBE_DAILY_QUOTA,
    "gemini": int(os.getenv("GEMINI_DAILY_REQUEST_BUDGET", "40")),   # stay well under free-tier RPD
    "pexels": int(os.getenv("PEXELS_HOURLY_BUDGET", "150")) * 24,     # official: 200/h, 20k/month
    "pixabay": int(os.getenv("PIXABAY_DAILY_BUDGET", "500")),         # official: 100 req / 60 s
}


def today(provider: str) -> str:
    if provider == "youtube":
        return dt.datetime.now(_PT).date().isoformat()
    return dt.date.today().isoformat()


def used(provider: str) -> int:
    row = db.one("SELECT COALESCE(SUM(units),0) AS u FROM quota_usage WHERE provider=? AND day=?",
                 (provider, today(provider)))
    return int(row["u"]) if row else 0


def allow(provider: str, units: int) -> bool:
    limit = LIMITS.get(provider)
    return limit is None or used(provider) + units <= limit


def record(provider: str, operation: str, units: int) -> None:
    db.execute("INSERT INTO quota_usage(provider, day, operation, units) VALUES (?,?,?,?)",
               (provider, today(provider), operation, units))


def summary() -> dict:
    return {p: {"used": used(p), "limit": lim, "day": today(p)} for p, lim in LIMITS.items()}
