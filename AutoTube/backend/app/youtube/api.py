"""YouTube Data API v3 + YouTube Analytics API v2.

Rules:
* Nothing is reported as uploaded / scheduled / public / thumbnail-set until YouTube's response confirms it.
* Immediate publishing = privacyStatus public. Scheduled publishing = private + publishAt (official mechanism).
* Existing uploads are changed with videos.update - a video is never uploaded twice.
* Every file is format-validated (Short vs Long spec) immediately before upload.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import time
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .. import config, db, logs, quota
from . import oauth

RETRIABLE_STATUS = {500, 502, 503, 504}
COST_UPDATE = int(__import__("os").getenv("YOUTUBE_COST_UPDATE", "50"))
WRITABLE_STATUS = ("privacyStatus", "embeddable", "license", "publicStatsViewable", "publishAt",
                   "selfDeclaredMadeForKids", "containsSyntheticMedia")
VERIFY_URL = "https://www.youtube.com/verify"


class YouTubeError(RuntimeError):
    pass


def _yt(channel_id: int | None = None):
    """API client for one channel profile's linked YouTube account."""
    creds = oauth.load(channel_id)
    if not creds:
        title = (db.one("SELECT name FROM channels WHERE id=?", (channel_id,)) or {}).get("name") if channel_id else None
        raise YouTubeError(f"YouTube is not connected for {'profile ' + repr(title) if title else 'this channel profile'} "
                           "(or needs reconnecting). Open the YouTube page.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def google_error(e: HttpError) -> dict:
    """Exact reason + message from a Google API error."""
    reason, message = "", ""
    try:
        err = json.loads(e.content.decode())["error"]
        message = err.get("message", "")
        reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
    except Exception:
        message = str(e)[:300]
    return {"http": e.resp.status, "reason": reason, "message": message,
            "text": f"HTTP {e.resp.status} {reason}: {message}".strip()}


def channel_info(channel_id: int | None = None) -> dict[str, Any] | None:
    c = oauth.connection(channel_id)
    if c.get("status") == "disconnected":
        return None
    return {"connected": c.get("status") == "connected", "status": c.get("status"),
            "channel": {"id": c.get("channel_id"), "title": c.get("channel_title"),
                        "thumbnail": c.get("channel_thumbnail")} if c.get("channel_id") else None}


# ------------------------------------------------------------------ privacy planning
def allowed_privacies() -> list[str]:
    out = ["public"]
    if db.get_setting("allow_unlisted_uploads"):
        out.append("unlisted")
    if db.get_setting("allow_private_uploads"):
        out.append("private")
    return out


def plan_privacy(video: dict, now: dt.datetime | None = None) -> dict[str, Any]:
    """privacyStatus / publishAt for an upload.

    * immediate: privacyStatus = requested (public by default)
    * scheduled (public, slot > 15 min ahead, scheduled publishing on): private + publishAt=slot
    * private/unlisted only if enabled in settings - otherwise public, so nothing stays private by accident
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    want = (video.get("target_privacy") or db.get_setting("default_privacy") or "public").lower()
    note = None
    if want not in allowed_privacies():
        note = f"{want} uploads are disabled in settings; publishing as public"
        want = "public"
    status: dict[str, Any] = {"privacyStatus": want}
    sched = video.get("scheduled_for")
    scheduled = False
    if want == "public" and sched and db.get_setting("scheduled_publishing"):
        when = dt.datetime.fromisoformat(sched.replace("Z", "+00:00"))
        if when - now > dt.timedelta(minutes=15):
            status = {"privacyStatus": "private", "publishAt": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
            scheduled = True
    return {"status": status, "requested": want, "scheduled": scheduled, "note": note}


# ------------------------------------------------------------------ low-level calls (mocked in tests)
def _insert(yt, body: dict, video_path: str, notify: bool) -> dict:
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=notify)
    response, attempt = None, 0
    while response is None:
        try:
            _, response = req.next_chunk()
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS and attempt < 6:
                attempt += 1
                time.sleep(min(60, 2 ** attempt + random.random()))
                continue
            raise
        except (ConnectionError, TimeoutError, OSError):
            if attempt < 6:
                attempt += 1
                time.sleep(min(60, 2 ** attempt + random.random()))
                continue
            raise
    return response


def _status(yt, vid: str) -> dict | None:
    res = yt.videos().list(part="status", id=vid).execute()
    quota.record("youtube", "videos.list", config.YOUTUBE_COST_LIST)
    items = res.get("items", [])
    return items[0]["status"] if items else None


# ------------------------------------------------------------------ thumbnails
def validate_thumbnail(path: str | Path) -> list[str]:
    """YouTube: JPG/PNG (also GIF/BMP), <= 2 MB, recommended 1280x720 16:9, min width 640."""
    from PIL import Image
    p = Path(path)
    if not p.exists():
        return ["thumbnail file missing"]
    problems = []
    if p.stat().st_size > 2 * 1024 * 1024:
        problems.append(f"file is {p.stat().st_size / 1e6:.1f} MB (max 2 MB)")
    try:
        with Image.open(p) as im:
            if im.format not in ("JPEG", "PNG"):
                problems.append(f"format {im.format} (use JPEG or PNG)")
            if im.width < 640:
                problems.append(f"width {im.width}px (min 640px)")
            if abs(im.width / im.height - 16 / 9) > 0.02:
                problems.append(f"aspect {im.width}x{im.height} is not 16:9")
    except Exception as e:
        problems.append(f"unreadable image: {e}")
    return problems


# A freshly inserted video is often not yet visible to thumbnails.set (HTTP 404 videoNotFound) for a while
# after videos.insert returns. Retry on that (and on transient 5xx / network errors) with growing waits.
THUMB_RETRY_DELAYS = (5, 10, 20, 30, 45, 60)


def set_thumbnail(yt, vid: str, path: str) -> dict:
    """thumbnails.set with exact error capture. Never raises - returns {status, error}."""
    problems = validate_thumbnail(path)
    if problems:
        return {"status": "invalid", "error": "; ".join(problems)}
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    attempt = 0
    while True:
        try:
            res = yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(path), mimetype=mime)).execute()
            quota.record("youtube", "thumbnails.set", config.YOUTUBE_COST_THUMBNAIL)
            if not (res or {}).get("items"):
                return {"status": "failed", "error": "YouTube returned no thumbnail data"}
            return {"status": "set", "error": None}
        except HttpError as e:
            quota.record("youtube", "thumbnails.set(failed)", config.YOUTUBE_COST_THUMBNAIL)
            g = google_error(e)
            transient = g["http"] == 404 or g["http"] in RETRIABLE_STATUS
            if transient and attempt < len(THUMB_RETRY_DELAYS):
                time.sleep(THUMB_RETRY_DELAYS[attempt])
                attempt += 1
                continue
            unavailable = g["http"] == 403
            return {"status": "unavailable" if unavailable else "failed", "error": g["text"]}
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt < len(THUMB_RETRY_DELAYS):
                time.sleep(THUMB_RETRY_DELAYS[attempt])
                attempt += 1
                continue
            return {"status": "failed", "error": f"network error: {e}"}


def retry_thumbnail(video: dict) -> dict:
    if not video.get("youtube_video_id"):
        raise YouTubeError("video has not been uploaded")
    return set_thumbnail(_yt(video.get("channel_id")), video["youtube_video_id"], video.get("thumbnail_path") or "")


# ------------------------------------------------------------------ upload
def upload(video: dict) -> dict[str, Any]:
    from ..media import formats
    if db.get_setting("emergency_stop"):
        raise YouTubeError("Emergency stop is active - uploads blocked")
    if video.get("youtube_video_id"):
        raise YouTubeError(f"Already uploaded as {video['youtube_video_id']} (duplicate prevented)")
    if not video.get("video_path") or not Path(video["video_path"]).exists():
        raise YouTubeError("rendered video file is missing")
    fmt = formats.validate(video["video_path"], video["kind"])
    if not fmt["ok"]:
        raise YouTubeError(f"rejected before upload - not a valid {video['kind']}: {'; '.join(fmt['errors'])}")
    need = config.YOUTUBE_COST_INSERT + config.YOUTUBE_COST_THUMBNAIL + config.YOUTUBE_COST_LIST
    if not quota.allow("youtube", need):
        raise YouTubeError(f"Not enough YouTube quota left today ({quota.used('youtube')}/{quota.LIMITS['youtube']})")

    plan = plan_privacy(video)
    status: dict[str, Any] = {**plan["status"], "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": True, "embeddable": True}
    tags = json.loads(video["tags"]) if isinstance(video.get("tags"), str) else (video.get("tags") or [])
    desc = video["description"][:5000]
    if video["kind"] == "short" and "#shorts" not in (video["title"] + desc).lower():
        desc = (desc + "\n\n#Shorts")[:5000]
    body = {"snippet": {"title": video["title"][:100], "description": desc, "tags": tags,
                        "categoryId": "27", "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status}
    yt = _yt(video.get("channel_id"))
    try:
        response = _insert(yt, body, video["video_path"], notify=plan["requested"] == "public")
    except HttpError as e:
        quota.record("youtube", "videos.insert(failed)", config.YOUTUBE_COST_INSERT)
        g = google_error(e)
        if g["reason"] in ("quotaExceeded", "uploadLimitExceeded"):
            raise YouTubeError(f"YouTube limit reached ({g['reason']}); will retry after the daily reset") from e
        raise YouTubeError(f"upload failed: {g['text']}") from e
    quota.record("youtube", "videos.insert", config.YOUTUBE_COST_INSERT)
    vid = response.get("id")
    if not vid:
        raise YouTubeError("upload returned no video id")

    thumb = {"status": None, "error": "no thumbnail rendered"}
    if video.get("thumbnail_path"):
        thumb = set_thumbnail(yt, vid, video["thumbnail_path"])
        if thumb["status"] != "set":
            logs.warn("youtube", f"custom thumbnail {thumb['status']}: {thumb['error']}", video["id"])

    st = _status(yt, vid)
    if not st:
        raise YouTubeError(f"upload {vid} could not be confirmed via videos.list")
    notes = [n for n in (plan["note"],) if n]
    confirmed = st.get("privacyStatus")
    mismatch = None
    if plan["scheduled"] and not st.get("publishAt"):
        mismatch = "YouTube did not accept the scheduled publish time"
    elif not plan["scheduled"] and confirmed != plan["requested"]:
        mismatch = (f"requested {plan['requested']} but YouTube set it {confirmed}. Unverified Google API projects "
                    "can only upload private videos until the project passes YouTube's API compliance audit")
    if mismatch:
        notes.append(mismatch)
        logs.notify("WARNING", f"Video #{video['id']} is {confirmed} on YouTube", mismatch)
    return {"youtube_video_id": vid, "privacy": confirmed, "publish_at": st.get("publishAt"),
            "upload_status": st.get("uploadStatus"), "requested_privacy": plan["requested"],
            "scheduled": plan["scheduled"], "thumbnail_status": thumb["status"], "thumbnail_error": thumb["error"],
            "privacy_mismatch": bool(mismatch), "notes": notes, "confirmed": True}


# ------------------------------------------------------------------ existing videos
def publish_now(video: dict) -> dict:
    """Make an already-uploaded video public via videos.update (never re-uploads). Confirmed by videos.list."""
    vid = video.get("youtube_video_id")
    if not vid:
        raise YouTubeError("this video has not been uploaded yet; use Upload")
    if not oauth.can_manage_videos(video.get("channel_id")):
        raise YouTubeError("Publishing an existing video needs the 'Manage your YouTube videos' permission. "
                           "Open the YouTube page and click Reconnect, then allow every permission.")
    if not quota.allow("youtube", COST_UPDATE + 2 * config.YOUTUBE_COST_LIST):
        raise YouTubeError("Not enough YouTube quota left today")
    yt = _yt(video.get("channel_id"))
    current = _status(yt, vid)
    if current is None:
        raise YouTubeError(f"YouTube has no video {vid} on this channel (deleted?)")
    if current.get("privacyStatus") == "public":
        return {"youtube_video_id": vid, "privacy": "public", "changed": False, "status": current}
    new_status = {k: current[k] for k in WRITABLE_STATUS if k in current and k != "publishAt"}
    new_status["privacyStatus"] = "public"
    try:
        yt.videos().update(part="status", body={"id": vid, "status": new_status}).execute()
        quota.record("youtube", "videos.update", COST_UPDATE)
    except HttpError as e:
        quota.record("youtube", "videos.update(failed)", COST_UPDATE)
        g = google_error(e)
        raise YouTubeError(f"YouTube refused the privacy change: {g['text']}") from e
    after = _status(yt, vid) or {}
    for wait in (2, 4, 8):  # YouTube can take a few seconds to reflect the change
        if after.get("privacyStatus") == "public":
            break
        time.sleep(wait)
        after = _status(yt, vid) or {}
    if after.get("privacyStatus") != "public":
        raise YouTubeError(f"YouTube accepted the request but the video is still {after.get('privacyStatus')}. "
                           "Videos uploaded by an unverified Google API project are locked private until the project "
                           "passes YouTube's API compliance audit.")
    return {"youtube_video_id": vid, "privacy": "public", "changed": True, "status": after}


def fetch_statuses(ids: list[str], channel_id: int | None = None) -> dict[str, dict]:
    """Confirmed status for many videos of one profile's account (1 quota unit per 50)."""
    out: dict[str, dict] = {}
    if not ids:
        return out
    yt = _yt(channel_id)
    for i in range(0, len(ids), 50):
        if not quota.allow("youtube", config.YOUTUBE_COST_LIST):
            break
        res = yt.videos().list(part="status", id=",".join(ids[i:i + 50])).execute()
        quota.record("youtube", "videos.list(status)", config.YOUTUBE_COST_LIST)
        for it in res.get("items", []):
            out[it["id"]] = it.get("status", {})
    return out


def refresh_stats() -> int:
    """View/like/comment counts for every uploaded video, per linked account."""
    n = 0
    for cid in oauth.connected_profiles():
        n += _refresh_stats(cid)
    return n


def _refresh_stats(channel_id: int) -> int:
    ids = [r["youtube_video_id"] for r in db.query(
        "SELECT youtube_video_id FROM videos WHERE youtube_video_id IS NOT NULL AND channel_id=?", (channel_id,))]
    if not ids or not oauth.load(channel_id):
        return 0
    yt = _yt(channel_id)
    n = 0
    for i in range(0, len(ids), 50):
        if not quota.allow("youtube", config.YOUTUBE_COST_LIST):
            break
        res = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        quota.record("youtube", "videos.list(stats)", config.YOUTUBE_COST_LIST)
        for it in res.get("items", []):
            s = it.get("statistics", {})
            db.execute("INSERT INTO analytics_snapshots(youtube_video_id, views, likes, comments) VALUES (?,?,?,?)",
                       (it["id"], int(s.get("viewCount", 0)), int(s.get("likeCount", 0)), int(s.get("commentCount", 0))))
            n += 1
    return n


def channel_analytics(days: int = 28, channel_id: int | None = None) -> dict[str, Any]:
    creds = oauth.load(channel_id)
    if not creds:
        raise YouTubeError("not connected")
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    res = ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                             metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,likes",
                             dimensions="day", sort="day").execute()
    cols = [h["name"] for h in res.get("columnHeaders", [])]
    return {"columns": cols, "rows": res.get("rows", [])}


# ------------------------------------------------------------------ growth
COST_COMMENT = 50


def post_comment(video: dict, text: str) -> str:
    """Top-level comment from the channel itself on its own public video. Returns the comment thread id.
    (The Data API has no 'pin' call - pin it in YouTube Studio.)"""
    if not oauth.can_manage_videos(video.get("channel_id")):
        raise YouTubeError("posting comments needs the 'Manage your YouTube videos' permission - reconnect YouTube")
    if not quota.allow("youtube", COST_COMMENT):
        raise YouTubeError("not enough YouTube quota left today")
    yt = _yt(video.get("channel_id"))
    try:
        res = yt.commentThreads().insert(part="snippet", body={"snippet": {
            "videoId": video["youtube_video_id"], "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
    except HttpError as e:
        quota.record("youtube", "commentThreads.insert(failed)", COST_COMMENT)
        raise YouTubeError(f"comment refused: {google_error(e)['text']}") from e
    quota.record("youtube", "commentThreads.insert", COST_COMMENT)
    return res.get("id", "")


def monetization_stats(channel_id: int | None = None) -> dict[str, Any]:
    """Real channel numbers for YouTube Partner Program progress. Watch hours / Shorts views come from YouTube
    Analytics and are close to (not identical with) what YPP counts (YPP uses public videos only)."""
    yt = _yt(channel_id)
    ch = yt.channels().list(part="statistics", mine=True).execute()
    quota.record("youtube", "channels.list(stats)", config.YOUTUBE_COST_LIST)
    st = (ch.get("items") or [{}])[0].get("statistics", {})
    out: dict[str, Any] = {"subscribers": int(st.get("subscriberCount", 0)), "total_views": int(st.get("viewCount", 0)),
                           "videos": int(st.get("videoCount", 0)), "long_watch_hours_365d": None,
                           "shorts_views_90d": None, "analytics_error": None}
    try:
        ya = build("youtubeAnalytics", "v2", credentials=oauth.load(channel_id), cache_discovery=False)
        end = dt.date.today()

        def by_type(days: int, metrics: str) -> dict[str, list]:
            r = ya.reports().query(ids="channel==MINE", startDate=(end - dt.timedelta(days=days)).isoformat(),
                                   endDate=end.isoformat(), metrics=metrics, dimensions="creatorContentType").execute()
            return {row[0]: row[1:] for row in r.get("rows", [])}

        year = by_type(365, "estimatedMinutesWatched")
        out["long_watch_hours_365d"] = round(sum(v[0] for k, v in year.items() if k != "SHORTS") / 60, 1)
        q = by_type(90, "views")
        out["shorts_views_90d"] = int(q.get("SHORTS", [0])[0])
    except Exception as e:
        out["analytics_error"] = str(e)[:300]
    return out


# ------------------------------------------------------------------ channel library (every upload on the channel)
_LIBRARY_CACHE: dict[int, tuple[float, dict]] = {}
LIBRARY_TTL = 600            # seconds


def iso_duration(s: str | None) -> int:
    """ISO-8601 duration (PT1H2M3S) -> seconds."""
    import re as _re
    m = _re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def channel_library(channel_id: int, refresh: bool = False) -> dict[str, Any]:
    """Every video on the linked YouTube channel (not only AutoTube's) with live views / likes / comments.
    Shorts vs long: AutoTube's own record when the video was made here, otherwise duration <= 3 min = Short."""
    hit = _LIBRARY_CACHE.get(channel_id)
    if hit and not refresh and time.time() - hit[0] < LIBRARY_TTL:
        return hit[1]
    yt = _yt(channel_id)
    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    quota.record("youtube", "channels.list(library)", config.YOUTUBE_COST_LIST)
    items = ch.get("items") or []
    if not items:
        raise YouTubeError("YouTube returned no channel for this account")
    c = items[0]
    uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    token = None
    while len(ids) < 2000:
        if not quota.allow("youtube", config.YOUTUBE_COST_LIST):
            break
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token).execute()
        quota.record("youtube", "playlistItems.list", config.YOUTUBE_COST_LIST)
        ids += [it["contentDetails"]["videoId"] for it in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    ours = {r["youtube_video_id"]: r for r in db.query(
        "SELECT id, kind, youtube_video_id FROM videos WHERE youtube_video_id IS NOT NULL")}
    videos = []
    for i in range(0, len(ids), 50):
        if not quota.allow("youtube", config.YOUTUBE_COST_LIST):
            break
        r = yt.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids[i:i + 50])).execute()
        quota.record("youtube", "videos.list(library)", config.YOUTUBE_COST_LIST)
        for it in r.get("items", []):
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            dur = iso_duration(it.get("contentDetails", {}).get("duration"))
            mine = ours.get(it["id"])
            kind = mine["kind"] if mine else ("short" if 0 < dur <= 180 else "long")
            thumbs = sn.get("thumbnails", {})
            videos.append({"youtube_video_id": it["id"], "title": sn.get("title", ""), "published_at": sn.get("publishedAt"),
                           "duration_sec": dur, "kind": kind, "kind_source": "autotube" if mine else "length",
                           "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                           "comments": int(st.get("commentCount", 0)),
                           "privacy": it.get("status", {}).get("privacyStatus"),
                           "thumbnail": (thumbs.get("medium") or thumbs.get("default") or {}).get("url"),
                           "url": (f"https://youtube.com/shorts/{it['id']}" if kind == "short"
                                   else f"https://youtu.be/{it['id']}"),
                           "autotube_id": mine["id"] if mine else None})

    def totals(vs: list[dict]) -> dict[str, int]:
        return {"uploads": len(vs), "views": sum(v["views"] for v in vs), "likes": sum(v["likes"] for v in vs),
                "comments": sum(v["comments"] for v in vs)}

    cs = c.get("statistics", {})
    sth = c.get("snippet", {}).get("thumbnails", {})
    out = {"channel": {"id": c["id"], "title": c.get("snippet", {}).get("title"),
                       "thumbnail": (sth.get("default") or {}).get("url"),
                       "subscribers": int(cs.get("subscriberCount", 0)), "views": int(cs.get("viewCount", 0)),
                       "video_count": int(cs.get("videoCount", 0)), "url": f"https://youtube.com/channel/{c['id']}"},
           "totals": {"all": totals(videos), "short": totals([v for v in videos if v["kind"] == "short"]),
                      "long": totals([v for v in videos if v["kind"] == "long"])},
           "videos": videos, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _LIBRARY_CACHE[channel_id] = (time.time(), out)
    return out


# ------------------------------------------------------------------ live channel details (Accounts page)
_DETAILS: dict[int, dict] = {}
DETAILS_TTL = 3600


def sync_channel_details(channel_id: int) -> dict[str, Any]:
    """Current name / handle / picture / counts of the linked channel, straight from YouTube. Also updates the saved
    name and picture (they were only captured at sign-in, so renames never showed up)."""
    yt = _yt(channel_id)
    r = yt.channels().list(part="snippet,statistics", mine=True).execute()
    quota.record("youtube", "channels.list(details)", config.YOUTUBE_COST_LIST)
    items = r.get("items") or []
    if not items:
        raise YouTubeError("YouTube returned no channel for this account")
    c = items[0]
    sn, st = c.get("snippet", {}), c.get("statistics", {})
    th = sn.get("thumbnails", {})
    d = {"id": c["id"], "title": sn.get("title"), "handle": sn.get("customUrl"), "description": (sn.get("description") or "")[:300],
         "thumbnail": (th.get("medium") or th.get("default") or {}).get("url"), "country": sn.get("country"),
         "created_at": sn.get("publishedAt"), "subscribers": int(st.get("subscriberCount", 0)),
         "subscribers_hidden": bool(st.get("hiddenSubscriberCount")), "views": int(st.get("viewCount", 0)),
         "videos": int(st.get("videoCount", 0)),
         "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    oauth._set_connection(channel_id, channel_title=d["title"], channel_thumbnail=d["thumbnail"])
    db.execute("UPDATE channels SET youtube_title=? WHERE id=?", (d["title"], channel_id))
    _DETAILS[channel_id] = {**d, "_t": time.time()}
    return d


def channel_details(channel_id: int, refresh: bool = False) -> dict[str, Any] | None:
    hit = _DETAILS.get(channel_id)
    if hit and not refresh and time.time() - hit["_t"] < DETAILS_TTL:
        return {k: v for k, v in hit.items() if k != "_t"}
    try:
        return sync_channel_details(channel_id)
    except Exception as e:
        logs.warn("youtube", f"channel details unavailable for profile {channel_id}: {e}")
        return {k: v for k, v in hit.items() if k != "_t"} if hit else None
