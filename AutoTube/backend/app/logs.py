"""Logging to rotating file + SQLite (visible in the dashboard), and notifications."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config, db

_logger = logging.getLogger("autotube")
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(config.LOG_DIR / "autotube.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(sh)


def log(level: str, source: str, message: str, video_id: int | None = None) -> None:
    level = level.upper()
    _logger.log(getattr(logging, level, logging.INFO), "[%s]%s %s", source, f"[video {video_id}]" if video_id else "", message)
    try:
        db.execute("INSERT INTO logs(level, source, message, video_id) VALUES (?,?,?,?)",
                   (level, source, message[:4000], video_id))
    except Exception:  # never let logging crash the pipeline
        _logger.exception("failed to write log row")


def info(source: str, msg: str, video_id: int | None = None) -> None:
    log("INFO", source, msg, video_id)


def warn(source: str, msg: str, video_id: int | None = None) -> None:
    log("WARNING", source, msg, video_id)


def error(source: str, msg: str, video_id: int | None = None) -> None:
    log("ERROR", source, msg, video_id)


def notify(level: str, title: str, body: str = "") -> None:
    """Error/info notification shown in the dashboard bell."""
    db.execute("INSERT INTO notifications(level,title,body) VALUES (?,?,?)", (level, title, body[:2000]))
    log(level if level in ("INFO", "WARNING", "ERROR") else "INFO", "notify", f"{title}: {body}")
