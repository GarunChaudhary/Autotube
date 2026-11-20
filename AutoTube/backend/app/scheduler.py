"""APScheduler jobs that drive the persistent queue.

All state (slots, videos, schedule times, retries) lives in SQLite, so the job definitions themselves are
stateless and recreated on start - nothing is lost when Windows restarts. Missed work is caught up on start.
NOTE: jobs only run while this PC is on and AutoTube is running.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, logs, pipeline, scheduling

sched = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})


def job_plan():
    try:
        ids = scheduling.plan()
        if ids:
            logs.info("scheduler", f"planned videos {ids}")
    except Exception as e:
        logs.error("scheduler", f"planner failed: {e}")


def job_dispatch():
    try:
        scheduling.dispatch()
    except Exception as e:
        logs.error("scheduler", f"dispatcher failed: {e}")
    try:
        scheduling.dispatch_immediate()
    except Exception as e:
        logs.error("scheduler", f"immediate dispatcher failed: {e}")


def job_retries():
    """Re-queue production retries whose backoff has elapsed."""
    if db.get_setting("emergency_stop"):
        return
    pipeline.ensure_workers()
    now = scheduling.iso(scheduling.now_utc())
    for r in db.query("SELECT id FROM videos WHERE status='DRAFT' AND (next_retry_at IS NULL OR next_retry_at<=?)", (now,)):
        pipeline.enqueue(r["id"])


def job_token_health():
    try:
        from .youtube import oauth
        oauth.health_check()
        from .youtube import api
        for pid in oauth.connected_profiles():
            api.channel_details(pid)                   # keeps the saved channel name / picture current
    except Exception as e:
        logs.warn("oauth", f"health check error: {type(e).__name__}")


def job_verify_publication():
    """Confirm with YouTube that scheduled videos went public, and keep displayed privacy truthful."""
    try:
        from .youtube import oauth
        if not oauth.any_connected():
            return
        ids = [r["id"] for r in db.query(
            "SELECT id FROM videos WHERE youtube_video_id IS NOT NULL AND (status='SCHEDULED' OR yt_checked_at IS NULL "
            "OR yt_checked_at < strftime('%Y-%m-%dT%H:%M:%SZ','now','-6 hours'))")]
        if ids:
            pipeline.refresh_youtube_status(ids)
    except Exception as e:
        logs.warn("youtube", f"status verification failed: {e}")
    try:
        from . import growth
        growth.post_engagement_comments()
    except Exception as e:
        logs.warn("growth", f"engagement comments failed: {e}")


def job_stats():
    try:
        from .youtube import api
        n = api.refresh_stats()
        if n:
            logs.info("analytics", f"refreshed stats for {n} videos")
    except Exception as e:
        logs.warn("analytics", f"stats refresh failed: {e}")


def job_topic_bank():
    """Keep ready-made, AI-judged topics for every genre so 'Suggest topics' and blank-topic uploads are instant.
    One genre per run, and only while no video is being produced (the AI judge is CPU heavy)."""
    if db.get_setting("emergency_stop") or pipeline.status()["current"]["video_id"] is not None:
        return
    try:
        from .agents import genres, topics
        for ch in db.query("SELECT id FROM channels ORDER BY id"):
            for g in genres.GENRES:
                if len(topics.banked(g, ch["id"])) < topics.BANK_SIZE // 2:
                    n = topics.refill(g, ch["id"])
                    logs.info("topics", f"topic bank {g}: +{n} for channel {ch['id']}")
                    return
    except Exception as e:
        logs.warn("topics", f"topic bank refill failed: {e}")


def job_report():
    pipeline.daily_report()


def start() -> None:
    if sched.running:
        return
    tz = scheduling.tz()
    sched.add_job(job_dispatch, "interval", minutes=1, id="upload_dispatcher", replace_existing=True)
    sched.add_job(job_plan, "interval", minutes=1, id="production_planner", replace_existing=True)
    sched.add_job(job_plan, "date", id="planner_startup_catchup", replace_existing=True,
                  run_date=scheduling.now_utc() + __import__("datetime").timedelta(seconds=30))
    sched.add_job(job_retries, "interval", minutes=2, id="retry_queue", replace_existing=True)
    sched.add_job(job_token_health, "interval", minutes=45, id="token_health", replace_existing=True)
    sched.add_job(job_verify_publication, "interval", minutes=10, id="publication_check", replace_existing=True)
    sched.add_job(job_stats, "interval", hours=6, id="stats", replace_existing=True)
    sched.add_job(job_topic_bank, "interval", minutes=10, id="topic_bank", replace_existing=True,
                  next_run_time=scheduling.now_utc() + __import__("datetime").timedelta(minutes=3))
    sched.add_job(job_report, CronTrigger(hour=23, minute=30, timezone=tz), id="daily_report", replace_existing=True)
    sched.start()
    logs.info("scheduler", f"scheduler started (timezone {tz})")


def jobs() -> list[dict]:
    return [{"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in sched.get_jobs()]


def shutdown() -> None:
    if sched.running:
        sched.shutdown(wait=False)
