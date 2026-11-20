"""Viral Trends: what is blowing up on YouTube in a region (default USA) and why, and making ORIGINAL videos on
those subjects.

Copyright-safe by design: nothing is downloaded from or clipped out of other creators' videos. A trending video only
supplies the real-world SUBJECT (verified against Wikipedia, like every AutoTube topic) and its title/hook PATTERN;
the new video is written, voiced and illustrated by AutoTube from licensed sources.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

from . import db, llm, logs

CATEGORIES = {"": "All categories", "24": "Entertainment", "27": "Education", "28": "Science & Technology",
              "22": "People & Blogs", "25": "News & Politics", "17": "Sports", "1": "Film & Animation",
              "26": "Howto & Style", "15": "Pets & Animals", "19": "Travel & Events", "10": "Music", "20": "Gaming"}
NO_CHART = {"27", "19"}          # no most-popular chart in the US for these (Shorts search still works)
CACHE_TTL = 3600
SEARCH_DAYS = 30

# Short names people type -> the Wikipedia article (plain "GTA 6" would otherwise match the unrelated article "GTA")
ALIASES = {"gta 6": "Grand Theft Auto VI", "gta6": "Grand Theft Auto VI", "gta vi": "Grand Theft Auto VI",
           "gta 5": "Grand Theft Auto V", "gta5": "Grand Theft Auto V", "gta v": "Grand Theft Auto V",
           "gta": "Grand Theft Auto", "rockstar": "Rockstar Games"}

# one-click presets on the Viral Trends page: what to search on YouTube + ready, Wikipedia-verified story topics
PRESETS = {
    "gta6": {"label": "🎮 GTA 6", "query": "GTA 6", "category": "20",
             "topics": ["Grand Theft Auto VI", "Rockstar Games", "Grand Theft Auto V", "Grand Theft Auto",
                        "Take-Two Interactive", "Rockstar North"]},
}


def resolve_alias(topic: str) -> str:
    return ALIASES.get(re.sub(r"\s+", " ", (topic or "").strip().lower()), (topic or "").strip())
SHORTS_SEARCH_COST = 100          # search.list
_cache: dict[tuple, tuple[float, dict]] = {}

CURIOSITY = {"why", "how", "never", "nobody", "secret", "truth", "real", "really", "impossible", "insane", "crazy",
             "finally", "first", "last", "only", "vs", "challenge", "hidden", "mystery", "shocking", "won't", "can't"}


def _client():
    from .youtube import api, oauth
    linked = oauth.connected_profiles()
    if not linked:
        raise RuntimeError("Connect a YouTube account first (YouTube → Accounts). Trends are read with its permission.")
    return api._yt(linked[0])


def patterns(title: str, duration: int) -> list[str]:
    """Why the packaging works - plain-language tags of the title / format pattern."""
    t = title.strip()
    low = t.lower()
    out = []
    if re.search(r"\d", t):
        out.append("number in title")
    if "?" in t:
        out.append("question")
    if any(w in re.findall(r"[a-z']+", low) for w in CURIOSITY):
        out.append("curiosity words")
    if re.search(r"\b[A-Z]{3,}\b", t):
        out.append("CAPS emphasis")
    if re.search(r"[\U0001F300-\U0001FAFF]", t):
        out.append("emoji")
    if any(w in low for w in ("i ", "i'", "my ", "we ")):
        out.append("personal story")
    if any(w in low for w in (" vs ", "versus", " or ")):
        out.append("comparison")
    out.append("short title" if len(t) <= 45 else "long title")
    if 0 < duration <= 180:
        out.append("Short")
    elif duration >= 1200:
        out.append("20+ min")
    return out


def _rows(items: list[dict], now: dt.datetime) -> list[dict[str, Any]]:
    from .youtube.api import iso_duration
    out = []
    for it in items:
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        dur = iso_duration(it.get("contentDetails", {}).get("duration"))
        pub = dt.datetime.fromisoformat(sn.get("publishedAt", now.isoformat()).replace("Z", "+00:00"))
        hours = max(1.0, (now - pub).total_seconds() / 3600)
        views, likes, comments = (int(st.get(k, 0)) for k in ("viewCount", "likeCount", "commentCount"))
        th = sn.get("thumbnails", {})
        kind = "short" if 0 < dur <= 180 else "long"
        out.append({"youtube_video_id": it["id"], "title": sn.get("title", ""), "channel": sn.get("channelTitle"),
                    "category": CATEGORIES.get(sn.get("categoryId", ""), sn.get("categoryId")),
                    "description": (sn.get("description") or "")[:600], "tags": (sn.get("tags") or [])[:12],
                    "published_at": sn.get("publishedAt"), "duration_sec": dur, "kind": kind,
                    "views": views, "likes": likes, "comments": comments,
                    "views_per_hour": round(views / hours), "engagement": round((likes + comments) / views, 4) if views else 0,
                    "thumbnail": (th.get("medium") or th.get("default") or {}).get("url"),
                    "url": f"https://youtube.com/shorts/{it['id']}" if kind == "short" else f"https://youtu.be/{it['id']}",
                    "patterns": patterns(sn.get("title", ""), dur)})
    return out


def trending(region: str = "US", category: str = "", mode: str = "trending", refresh: bool = False,
             q: str = "", duration: str = "any") -> dict[str, Any]:
    """mode 'trending': YouTube's official most-popular chart (1 quota unit per 50 videos).
    mode 'shorts': most-viewed Shorts published in the last 7 days (search: ~100 quota units, cached 1 h).
    mode 'search': most-viewed videos about `q` (e.g. "GTA 6") from the last 30 days (search: ~100 units, cached 1 h)."""
    from . import quota
    region = (region or "US").upper()[:2]
    q = (q or "").strip()[:100]
    if mode == "search" and not q:
        raise RuntimeError("Type what to search for (e.g. GTA 6).")
    duration = duration if duration in ("short", "long") else "any"
    key = (region, category, mode, q.lower(), duration if mode == "search" else "any")
    hit = _cache.get(key)
    if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    yt = _client()
    now = dt.datetime.now(dt.timezone.utc)
    items: list[dict] = []
    if mode in ("shorts", "search"):
        if not quota.allow("youtube", SHORTS_SEARCH_COST + 1):
            raise RuntimeError("Not enough YouTube quota left today for a search (needs ~100 units).")
        days = 7 if mode == "shorts" else SEARCH_DAYS
        args = dict(part="id", type="video", regionCode=region, order="viewCount", relevanceLanguage="en", maxResults=50,
                    publishedAfter=(now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if mode == "shorts" or duration == "short":
            args["videoDuration"] = "short"            # YouTube: under 4 minutes
        elif duration == "long":
            args["videoDuration"] = "medium"           # YouTube: 4-20 minutes (typical long-form)
        if q:
            args["q"] = q
        if category:
            args["videoCategoryId"] = category
        res = yt.search().list(**args).execute()
        quota.record("youtube", "search.list(trends)", SHORTS_SEARCH_COST)
        ids = [r["id"]["videoId"] for r in res.get("items", []) if r.get("id", {}).get("videoId")]
        if ids:
            items = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute().get("items", [])
            quota.record("youtube", "videos.list(trends)", 1)
    else:
        token = None
        for _ in range(4):                                  # up to 200 videos
            args = dict(part="snippet,statistics,contentDetails", chart="mostPopular", regionCode=region, maxResults=50)
            if category:
                args["videoCategoryId"] = category
            if token:
                args["pageToken"] = token
            try:
                res = yt.videos().list(**args).execute()
            except Exception as e:                          # some categories have no chart in some regions
                if not items:
                    raise RuntimeError(f"YouTube has no trending chart for that category here ({str(e)[:120]})") from e
                break
            quota.record("youtube", "videos.list(trending)", 1)
            items += res.get("items", [])
            token = res.get("nextPageToken")
            if not token:
                break
    rows = _rows(items, now)
    out = {"region": region, "category": category, "mode": mode, "q": q, "duration": duration, "videos": rows,
           "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "categories": CATEGORIES,
           "chart_categories": {k: v for k, v in CATEGORIES.items() if k not in NO_CHART}, "presets": PRESETS}
    _cache[key] = (time.time(), out)
    return out


# ------------------------------------------------------------------ trend -> original topic
SYSTEM = ("You identify the real-world subject of a YouTube video from its title, tags and description. You never "
          "invent: if the video is not about a specific real person, event, place, phenomenon, animal, invention or "
          "historical/scientific subject that has a Wikipedia article, you say so.")


def topic_candidates(v: dict, video_id: int | None = None) -> list[str]:
    prompt = f"""TITLE: {v.get('title')}
CHANNEL: {v.get('channel')}
TAGS: {', '.join(v.get('tags') or [])}
DESCRIPTION: {(v.get('description') or '')[:500]}

List up to 4 specific real-world subjects this video is about or that its audience would want a documentary-style
story on, each as the exact name a Wikipedia article would use (e.g. "Titan submersible implosion", "Mariana Trench",
"Great Emu War"). Skip generic words, songs, games, influencers' personal drama and anything not encyclopedic.
Return JSON: {{"subjects": ["...", "..."]}}"""
    try:
        data, _ = llm.generate_json(prompt, SYSTEM, 0.2, video_id, role="writer")
        subs = [str(s).strip() for s in data.get("subjects", []) if str(s).strip()]
    except Exception as e:
        logs.warn("trends", f"subject extraction failed: {e}")
        subs = []
    # tags are often exact subject names - but not the creator's own channel / show name
    owner = {w for w in re.findall(r"[a-z0-9]+", (v.get("channel") or "").lower()) if len(w) > 2}
    tags = [t for t in (v.get("tags") or []) if len(t.split()) >= 2
            and not (owner and owner <= set(re.findall(r"[a-z0-9]+", t.lower())))
            and not any(w in t.lower() for w in ("show", "episode", "official", "trailer", "podcast", "full video"))]
    # known short names first ("GTA 6" -> "Grand Theft Auto VI")
    text = f"{v.get('title', '')} {' '.join(v.get('tags') or [])}".lower()
    known = [full for short, full in ALIASES.items() if re.search(rf"(?<![a-z0-9]){re.escape(short)}(?![a-z0-9])", text)]
    return list(dict.fromkeys(known + subs + tags))[:8]


VAGUE = {"part", "unboxing", "challenge", "prank", "vlog", "reaction", "tutorial", "compilation", "funny", "comedy",
         "music", "song", "dance", "meme", "shorts", "video", "trailer", "game", "gaming", "family", "love", "life"}


def good_subject(title: str) -> bool:
    """A specific, story-worthy subject: not one vague word ('Part', 'Unboxing') and not a generic how-to topic."""
    words = re.findall(r"[A-Za-z0-9]+", title)
    if not words or title.strip().lower() in VAGUE:
        return False
    if len(words) == 1:
        return bool(re.search(r"\d", title)) or (title[:1].isupper() and title.lower() not in VAGUE and len(title) > 3
                                                  and not title.endswith("ing"))
    return True


def idea(v: dict) -> dict[str, Any]:
    """Verified topic for an original video inspired by this trend (None when the trend has no factual subject)."""
    from .agents import research
    tried = []
    for cand in topic_candidates(v):
        tried.append(cand)
        try:
            title = research.verify_topic(cand)
        except Exception as e:
            logs.warn("trends", f"verify '{cand}' failed: {e}")
            continue
        if title and good_subject(title):
            return {"topic": title, "candidate": cand, "tried": tried}
    return {"topic": None, "tried": tried,
            "reason": "This trend has no verifiable real-world subject (music, gaming and personal vlogs usually don't). "
                      "Pick a trend about a real event, place, person, science or history - or type your own topic."}


def fallback_topic(channel_id: int, kind: str) -> tuple[str | None, str | None]:
    """When a trend has no usable subject: a verified, in-demand story from the genre that performs best on this
    channel (topic bank - usually instant)."""
    from . import growth
    from .agents import research
    ch = db.one("SELECT * FROM channels WHERE id=?", (channel_id,))
    g = growth.pick_genre(channel_id, kind)
    found = research.suggest_topics(ch, 1, genre=g) if ch else []
    return (found[0], g) if found else (None, None)


def start(channel_id: int, topic: str, kinds: list[str], mode: str, when_local: str | None = None,
          source: dict | None = None, skip_review: bool = False) -> dict[str, Any]:
    """'Make video' button: checks the choices right away, then finds the topic (trend subject, else the best genre's
    top verified story) and starts production in the background, so the click never waits on the AI."""
    import threading
    _check(channel_id, kinds, mode, when_local)

    def work():
        try:
            t, g = (topic or "").strip(), None
            if not t and source:
                t = idea(source).get("topic") or ""
            if not t:
                t, g = fallback_topic(channel_id, kinds[0])
                if t:
                    logs.info("trends", f"trend had no clear subject; using top {g} story '{t}'")
            if not t:
                raise ValueError("no verified topic available right now - type a topic and try again")
            try:
                make(channel_id, t, kinds, mode, when_local, g, source, skip_review)
            except ValueError as e:
                if "Duplicate topic" not in str(e) or topic:
                    raise
                t, g = fallback_topic(channel_id, kinds[0])        # already made a video on that subject
                if not t:
                    raise
                make(channel_id, t, kinds, mode, when_local, g, source, skip_review)
        except Exception as e:
            logs.notify("ERROR", "Viral video could not start", str(e)[:300])
            logs.error("trends", f"make failed: {e}")

    threading.Thread(target=work, name="autotube-trend-make", daemon=True).start()
    return {"started": True}


def _check(channel_id: int, kinds: list[str], mode: str, when_local: str | None) -> None:
    from . import scheduling
    if mode not in ("now", "schedule", "make"):
        raise ValueError("mode must be now, schedule or make")
    if db.get_setting("emergency_stop"):
        raise ValueError("Emergency stop is active")
    if not db.one("SELECT id FROM channels WHERE id=?", (channel_id,)):
        raise ValueError("channel profile not found")
    if not [k for k in kinds if k in ("short", "long")]:
        raise ValueError("choose Short and/or Long")
    if mode == "schedule":
        if not when_local:
            raise ValueError("choose a date and time to schedule")
        when = dt.datetime.fromisoformat(when_local).replace(tzinfo=scheduling.tz()).astimezone(dt.timezone.utc)
        if when < scheduling.now_utc() + dt.timedelta(minutes=30):
            raise ValueError("schedule at least 30 minutes ahead so the video can be produced")


def make(channel_id: int, topic: str, kinds: list[str], mode: str, when_local: str | None = None,
         genre: str | None = None, source: dict | None = None, skip_review: bool = False) -> list[dict]:
    """Create original Short/long videos on the topic.
    mode 'now': upload as soon as ready | 'schedule': upload at when_local | 'make': only make it (kept in the library
    with its thumbnail, title, description and hashtags; upload or schedule it later)."""
    if mode not in ("now", "schedule", "make"):
        raise ValueError("mode must be now, schedule or make")
    from . import pipeline, scheduling
    from .agents import genres, research
    if db.get_setting("emergency_stop"):
        raise ValueError("Emergency stop is active")
    if not db.one("SELECT id FROM channels WHERE id=?", (channel_id,)):
        raise ValueError("channel profile not found")
    if genre and not genres.get(genre):
        raise ValueError(f"unknown genre '{genre}'")
    kinds = [k for k in dict.fromkeys(kinds) if k in ("short", "long")]
    if not kinds:
        raise ValueError("choose Short and/or Long")
    verified = research.verify_topic(resolve_alias(topic))
    if not verified:
        raise ValueError(f"No Wikipedia source found for '{topic}', so it can't be fact-checked. Pick another topic.")
    when = None
    if mode == "schedule":
        if not when_local:
            raise ValueError("choose a date and time to schedule")
        local = dt.datetime.fromisoformat(when_local).replace(tzinfo=scheduling.tz())
        when = local.astimezone(dt.timezone.utc)
        if when < scheduling.now_utc() + dt.timedelta(minutes=30):
            raise ValueError("schedule at least 30 minutes ahead so the video can be produced")
    out = []
    for i, kind in enumerate(kinds):
        # a Short and a long on the same story are a pair, not duplicates; when scheduled the long goes up 2 h later
        vid = pipeline.create_video(channel_id, kind, verified, genre=genre, upload_immediately=(mode == "now"),
                                    skip_review=skip_review, companion=i > 0)
        if when:
            scheduling.reschedule(vid, scheduling.iso(when + dt.timedelta(hours=2 * i)))
        # every video made from the Viral Trends page is tagged in the log, so the page can list its progress
        logs.info("trends", f"original {kind} on '{verified}'" + (
            f", inspired by trending '{source.get('title', '')[:80]}' ({source.get('youtube_video_id')})" if source else
            " (Viral Trends: Create a video)"), vid)
        pipeline.enqueue(vid)
        out.append(db.get_video(vid))
    return out
