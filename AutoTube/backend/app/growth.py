"""Growth engine: learns from this channel's OWN YouTube numbers (no fake engagement, no guesses).

* performance()      latest views/likes per uploaded video, normalised to views/day since it went live
* genre_report()     which genres earn the most views per video, per format
* pick_genre()       the planner's genre choice: mostly the proven winners, some exploration of untested genres
* top_examples()     best-performing titles / hooks, fed back to the writers as style references
* hour_report()      views by local go-live hour -> slot-time recommendation once there is enough data
* engagement comments: one channel comment per new public video (a question that invites replies)
* monetization()     YouTube Partner Program progress from the real channel numbers
"""
from __future__ import annotations

import datetime as dt
import json
import random
from typing import Any

from . import db, logs

MIN_TESTS = 2              # videos a genre needs before its average is trusted
EXPLORE = 0.25             # share of slots that test a less-proven genre
MIN_HOUR_VIDEOS = 8        # videos (48h+ old) needed before recommending posting times


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def performance(channel_id: int, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now()
    rows = db.query("""SELECT v.id, v.kind, v.genre, v.title, v.script_json, v.youtube_video_id,
                              COALESCE(v.yt_publish_at, v.published_at) AS live_at, a.views, a.likes, a.comments
                       FROM videos v JOIN analytics_snapshots a ON a.youtube_video_id=v.youtube_video_id
                       WHERE v.channel_id=? AND a.id IN (SELECT MAX(id) FROM analytics_snapshots GROUP BY youtube_video_id)""",
                    (channel_id,))
    out = []
    for r in rows:
        live = _parse(r["live_at"])
        if not live or live > now:
            continue
        age_d = max(0.5, (now - live).total_seconds() / 86400)
        try:
            sj = json.loads(r["script_json"]) if isinstance(r.get("script_json"), str) else (r.get("script_json") or {})
        except ValueError:
            sj = {}
        out.append({"id": r["id"], "kind": r["kind"], "genre": r["genre"], "title": r["title"],
                    "hook": sj.get("hook") if isinstance(sj, dict) else None, "youtube_video_id": r["youtube_video_id"],
                    "live_at": live, "age_days": round(age_d, 1), "views": r["views"] or 0, "likes": r["likes"] or 0,
                    "comments": r["comments"] or 0, "views_per_day": round((r["views"] or 0) / age_d, 1),
                    "like_rate": round((r["likes"] or 0) / r["views"], 4) if r["views"] else 0.0})
    return out


def genre_report(channel_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    perf = [p for p in performance(channel_id) if p["genre"] and (kind is None or p["kind"] == kind)]
    made = {r["genre"]: r["n"] for r in db.query(
        "SELECT genre, COUNT(*) n FROM videos WHERE channel_id=? AND genre IS NOT NULL" + (" AND kind=?" if kind else "")
        + " GROUP BY genre", (channel_id, kind) if kind else (channel_id,))}
    by: dict[str, list] = {}
    for p in perf:
        by.setdefault(p["genre"], []).append(p)
    out = []
    for g in set(by) | set(made):
        ps = by.get(g, [])
        out.append({"genre": g, "videos_made": made.get(g, 0), "videos_with_stats": len(ps),
                    "avg_views": round(sum(p["views"] for p in ps) / len(ps)) if ps else 0,
                    "avg_views_per_day": round(sum(p["views_per_day"] for p in ps) / len(ps), 1) if ps else 0.0,
                    "avg_like_rate": round(sum(p["like_rate"] for p in ps) / len(ps), 4) if ps else 0.0})
    return sorted(out, key=lambda r: -r["avg_views_per_day"])


def candidate_genres() -> list[str]:
    from .agents import genres
    want = db.get_setting("growth_genres") or []
    ok = [g for g in want if genres.get(g)]
    return ok or list(genres.GENRES)


def pick_genre(channel_id: int, kind: str, rnd: random.Random | None = None) -> str:
    """Exploit the genres that earn the most views per day; keep testing the rest so the data stays honest."""
    rnd = rnd or random.Random()
    cands = candidate_genres()
    stats = {r["genre"]: r for r in genre_report(channel_id, kind) if r["genre"] in cands}
    untested = [g for g in cands if stats.get(g, {}).get("videos_with_stats", 0) < MIN_TESTS]
    proven = sorted((g for g in cands if g not in untested), key=lambda g: -stats[g]["avg_views_per_day"])
    if untested and (not proven or rnd.random() < EXPLORE or len(proven) < 2):
        # least-made first so every genre gets a fair test
        return min(untested, key=lambda g: (stats.get(g, {}).get("videos_made", 0), rnd.random()))
    if not proven:
        return rnd.choice(cands)
    # weighted toward the best performers (top genre ~ half the slots)
    weights = [max(0.1, stats[g]["avg_views_per_day"]) for g in proven]
    return rnd.choices(proven, weights=weights)[0]


def top_examples(channel_id: int, kind: str, field: str = "title", n: int = 3) -> list[str]:
    """Best titles/hooks of this channel (by views per day). Only when there is a real difference to learn from."""
    perf = [p for p in performance(channel_id) if p["kind"] == kind and p.get(field) and p["views"] > 0]
    if len(perf) < 4:
        return []
    perf.sort(key=lambda p: -p["views_per_day"])
    return [str(p[field])[:120] for p in perf[:n]]


def hour_report(channel_id: int) -> dict[str, Any]:
    from . import scheduling
    tz = scheduling.tz()
    old = [p for p in performance(channel_id) if p["age_days"] >= 2]
    buckets: dict[int, list] = {}
    for p in old:
        buckets.setdefault(p["live_at"].astimezone(tz).hour, []).append(p["views_per_day"])
    hours = [{"hour": f"{h:02d}:00", "videos": len(v), "avg_views_per_day": round(sum(v) / len(v), 1)}
             for h, v in sorted(buckets.items())]
    rec = None
    if len(old) >= MIN_HOUR_VIDEOS and len([h for h in hours if h["videos"] >= 2]) >= 3:
        best = sorted((h for h in hours if h["videos"] >= 2), key=lambda h: -h["avg_views_per_day"])[:3]
        rec = [h["hour"] for h in best]
    return {"hours": hours, "videos_counted": len(old), "recommended": rec, "needed": MIN_HOUR_VIDEOS,
            "timezone": str(tz)}


# ------------------------------------------------------------------ engagement comment
DEFAULT_QUESTIONS = {
    "short": "Which detail surprised you most? Tell me below.",
    "long": "What do you think really happened? I read every comment.",
}


def comment_text(v: dict) -> str:
    seo = v.get("seo_json") or {}
    q = str(seo.get("comment") or "").strip() or DEFAULT_QUESTIONS.get(v["kind"], DEFAULT_QUESTIONS["short"])
    best = [p for p in performance(v["channel_id"]) if p["youtube_video_id"] != v.get("youtube_video_id")]
    if best:
        b = max(best, key=lambda p: p["views_per_day"])
        q += f"\n\nIf you liked this one, watch next: {b['title']} https://youtu.be/{b['youtube_video_id']}"
    return q[:1500]


def post_engagement_comments(limit: int = 5) -> int:
    """One channel comment per newly public video (posted once; YouTube's API cannot pin it - pin it in Studio)."""
    if not db.get_setting("engagement_comment") or db.get_setting("emergency_stop"):
        return 0
    from .youtube import api as yt
    since = (_now() - dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.query("SELECT * FROM videos WHERE youtube_video_id IS NOT NULL AND status='PUBLISHED' AND yt_privacy='public' "
                    "AND engagement_comment_id IS NULL AND COALESCE(engagement_comment_attempts,0)<3 "
                    "AND COALESCE(yt_publish_at, published_at)>=? ORDER BY id LIMIT ?", (since, limit))
    n = 0
    for r in rows:
        v = db.get_video(r["id"])
        try:
            cid = yt.post_comment(v, comment_text(v))
            db.update_video(v["id"], engagement_comment_id=cid)
            logs.info("growth", "engagement comment posted (pin it in YouTube Studio for best effect)", v["id"])
            n += 1
        except Exception as e:
            db.update_video(v["id"], engagement_comment_attempts=(v.get("engagement_comment_attempts") or 0) + 1)
            logs.warn("growth", f"engagement comment failed: {e}", v["id"])
    return n


# ------------------------------------------------------------------ monetization
YPP = {
    "fan_funding": {"label": "Early access (memberships, Super Thanks)", "subs": 500, "uploads_90d": 3,
                    "watch_hours": 3000, "shorts_views_90d": 3_000_000},
    "ads": {"label": "Full monetization (ad revenue)", "subs": 1000, "watch_hours": 4000, "shorts_views_90d": 10_000_000},
}


def monetization(channel_id: int) -> dict[str, Any]:
    from .youtube import api as yt
    s = yt.monetization_stats(channel_id)
    uploads_90d = db.one("SELECT COUNT(*) n FROM videos WHERE channel_id=? AND youtube_video_id IS NOT NULL AND "
                         "COALESCE(yt_publish_at, published_at)>=?",
                         (channel_id, (_now() - dt.timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")))["n"]
    tiers = {}
    for k, t in YPP.items():
        wh = round(s["long_watch_hours_365d"] or 0) if s["long_watch_hours_365d"] is not None else None
        tiers[k] = {"label": t["label"],
                    "subs": {"have": s["subscribers"], "need": t["subs"]},
                    "watch_hours": {"have": wh, "need": t["watch_hours"]},
                    "shorts_views_90d": {"have": s["shorts_views_90d"], "need": t["shorts_views_90d"]},
                    **({"uploads_90d": {"have": uploads_90d, "need": t["uploads_90d"]}} if "uploads_90d" in t else {})}
        subs_ok = (s["subscribers"] or 0) >= t["subs"]
        views_ok = (wh or 0) >= t["watch_hours"] or (s["shorts_views_90d"] or 0) >= t["shorts_views_90d"]
        up_ok = uploads_90d >= t.get("uploads_90d", 0)
        tiers[k]["eligible"] = bool(subs_ok and views_ok and up_ok)
    return {**s, "uploads_90d": uploads_90d, "tiers": tiers}
