"""Video production pipeline + background job worker.

Research -> Script -> Fact Check -> Storyboard -> Visuals -> Voiceover -> Editing (captions+render)
-> Thumbnail -> SEO -> Quality Check -> Upload

Each stage persists its output, so a retry resumes from the failed stage instead of starting over.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import queue
import re
import threading
import traceback
from pathlib import Path
from typing import Any

from . import config, db, logs
from .agents import factcheck, genres, research, script as script_agent, seo
from .agents.text_utils import copies_source, jaccard, normalize_key, word_count
from .media import ffmpeg, music, render, subtitles, thumbnail, tts, visuals

STAGES = ["research", "script", "factcheck", "storyboard", "visuals", "voiceover", "editing",
          "thumbnail", "seo", "quality", "upload", "done"]

# Priority queue: slot videos (earliest slot first) are produced before unscheduled / "upload immediately" ones,
# so a slot video started an hour ahead is never stuck behind a backlog of manual jobs.
_q: "queue.PriorityQueue[tuple[str, int, int]]" = queue.PriorityQueue()
_seq = itertools.count()
_queued: set[int] = set()
_current: dict[str, dict] = {}          # worker name -> {"video_id", "stage"}
_workers: list[threading.Thread] = []


class Stopped(Exception):
    pass


def _work_dir(video_id: int) -> Path:
    p = config.OUTPUT_DIR / f"{video_id:05d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state(v: dict) -> dict:
    """Scratch state stored in storyboard_json between stages."""
    sb = v.get("storyboard_json")
    return sb if isinstance(sb, dict) else {}


# ------------------------------------------------------------------ stages
def st_research(v, ch):
    r = research.research(v["topic"], ch["niche"], v["id"])
    db.update_video(v["id"], research_json=r)


def st_script(v, ch):
    r = v["research_json"]
    s = script_agent.write_short(v["topic"], r, ch, v["id"]) if v["kind"] == "short" else \
        script_agent.write_long(v["topic"], r, ch, v["id"])
    db.update_video(v["id"], script_json=s)


def st_factcheck(v, ch):
    fc = factcheck.check(v["script_json"], v["research_json"], v["id"])
    words = sum(word_count(s["text"]) for s in fc["script"]["segments"])
    min_words = 70 if v["kind"] == "short" else int(script_agent.long_minutes(ch) * 145 * 0.55)
    if len(fc["script"]["segments"]) < 3 or words < min_words:
        raise RuntimeError(f"fact-check left only {words} words; regenerating")
    if v["kind"] == "short":
        script_agent.attach_loop_line(fc["script"])
    db.update_video(v["id"], factcheck_json={k: fc[k] for k in ("score", "method", "results", "rewritten", "removed")},
                    script_json=fc["script"])


def st_storyboard(v, ch):
    """Scene plan: one scene per narration beat, split into shots for pacing; marks chapter starts."""
    segs = v["script_json"]["segments"]
    portrait = v["kind"] == "short"
    # retention pacing: a new picture every ~1.6 s on Shorts, ~3.2 s on long-form. Assets are sourced per
    # asset_len; extra cuts re-frame the same asset with a different move (see _shots), so sourcing load is unchanged.
    shot_len = 1.6 if portrait else 3.2
    asset_len = 2.8 if portrait else 5.0
    plan, prev_ch = [], None
    for i, s in enumerate(segs):
        est = word_count(s["text"]) / 2.6               # ~156 wpm narration
        chap = s.get("caption_title") if not portrait else None
        plan.append({"i": i, "query": s.get("visual") or v["topic"], "label": s.get("caption_title", ""),
                     "n": max(1, math.ceil(est / asset_len)),
                     "chapter_start": bool(chap) and chap != prev_ch and i > 0})
        if chap:
            prev_ch = chap
    db.update_video(v["id"], storyboard_json={"plan": plan, "shot_len": shot_len})


def st_visuals(v, ch):
    """Source real, relevant, licensed media for every scene. Never uses placeholder art."""
    st = _state(v)
    portrait = v["kind"] == "short"
    providers = db.get_setting("visual_provider_order") or list(visuals.SUBJECT_PROVIDERS + visuals.STOCK_PROVIDERS)
    used: set[str] = set()
    pool = visuals.subject_candidates(v["topic"], "", providers, 40, v["id"])
    pool_i = 0
    assets, stats = [], {"unique": 0, "by_provider": {}}

    film_uses: dict[str, int] = {}

    def take(cands):
        for c in cands:
            # a long real film can serve up to 3 shots (each cut from a different moment of it)
            if c["id"] in used and not (c.get("type") == "video" and (c.get("dur") or 0) >= 60
                                        and 0 < film_uses.get(c["id"], 0) < 3):
                continue
            used.add(c["id"])
            film_uses[c["id"]] = film_uses.get(c["id"], 0) + 1
            a = visuals.materialize(c, v["id"])
            if a:
                if film_uses[c["id"]] == 1:
                    stats["unique"] += 1
                    stats["by_provider"][a["provider"]] = stats["by_provider"].get(a["provider"], 0) + 1
                stats["real_footage"] = stats.get("real_footage", 0) + (a["type"] == "video")
                return a
            film_uses[c["id"]] = 99     # broken download: never retry
        return None

    for p in st["plan"]:
        want = p["n"] + (0 if portrait else 1)
        subj = visuals.subject_candidates(v["topic"], p["query"], providers, want, v["id"])
        stock = visuals.stock_candidates(p["query"], portrait, providers, want, v["id"])
        got: list[dict] = []
        # interleave: subject imagery anchors the story, stock b-roll adds motion
        for k in range(want):
            first, second = (subj, stock) if k % 2 == 0 else (stock, subj)
            a = take(first) or take(second)
            while not a and pool_i < len(pool):
                a = take([pool[pool_i]])
                pool_i += 1
            if a:
                got.append(a)
        assets.append(got)
    # scenes left empty reuse the nearest real asset (re-framed by different motion) - never placeholders
    flat = [a for g in assets for a in g]
    if not flat:
        raise RuntimeError("no relevant licensed visuals found for this topic (add free Pexels/Pixabay keys or choose "
                           "a better-documented topic)")
    for i, g in enumerate(assets):
        if not g:
            near = next((assets[j] for d in range(1, len(assets)) for j in (i - d, i + d)
                         if 0 <= j < len(assets) and assets[j] and not assets[j][0].get("reused")), [flat[0]])
            assets[i] = [dict(near[-1], reused=True)]
    total_shots = sum(p["n"] for p in st["plan"])
    stats["empty_scenes"] = sum(1 for g in assets if g and g[0].get("reused"))
    stats["coverage"] = round(min(1.0, stats["unique"] / max(1, total_shots)), 2)
    st["assets"] = assets
    st["visual_stats"] = stats
    logs.info("visuals", f"{stats['unique']} unique licensed assets for {total_shots} shots {stats['by_provider']}, "
                         f"{stats['empty_scenes']} scenes reuse footage", v["id"])
    db.update_video(v["id"], storyboard_json=st)


def st_voiceover(v, ch):
    st = _state(v)
    g = genres.get(v.get("genre"))
    voice = g["voice"] if g else tts.voice_for(v["kind"])
    short = v["kind"] == "short"
    segs = v["script_json"]["segments"]
    voices = None
    if v.get("character_ids"):                   # AI characters speak with their own voices (podcast: hosts alternate)
        from . import characters
        cast = characters.video_cast(v)
        voices = characters.segment_voices(v.get("character_mode"), cast, len(segs)) or None
        if voices:
            voice = voices[0]
    podcast = v.get("character_mode") == "podcast" and voices
    # retention pacing: tight pauses; Shorts end right on the loop line so the replay flows into the hook
    tl = tts.synthesize(segs, _work_dir(v["id"]), voice=voice,
                        speed=1.08 if short else 1.0, video_id=v["id"],
                        sent_gap=0.12 if short else 0.18,
                        seg_gap=(0.3 if short else 0.45) if podcast else (0.2 if short else 0.35),
                        tail=0.12 if short else None, voices=voices)
    st["timeline"] = tl
    db.update_video(v["id"], storyboard_json=st, duration_sec=tl["duration"])


def _shots(st: dict) -> list[dict]:
    shots = []
    tl = st["timeline"]
    for seg, assets, p in zip(tl["segments"], st["assets"], st["plan"]):
        dur = seg["end"] - seg["start"]
        n = max(1, round(dur / st["shot_len"]))
        n = min(n, max(2, len(assets) * 2))          # at most two framings per asset (never a long static hold)
        for k in range(n):
            a = assets[k % len(assets)]
            shots.append({"start": seg["start"] + dur * k / n, "end": seg["start"] + dur * (k + 1) / n, "asset": a,
                          "segment_start": k == 0, "chapter_start": k == 0 and p.get("chapter_start", False),
                          "seg_i": p["i"], "take": k // len(assets)})   # take>0 = same asset, new framing
    shots[0]["start"] = 0.0
    shots[-1]["end"] = tl["duration"]
    return shots


def _short_credit(c: str) -> str:
    c = re.sub(r"\s*\(https?://[^)]*\)", "", c)
    return c if c.lower().startswith(("video", "image")) else "Image: " + c


def subscribe_prompt(v: dict, duration: float) -> tuple[float, float, str] | None:
    """Silent on-screen prompt. Shorts: the last ~2.5 s (spoken CTAs kill the replay loop). Long: once, early."""
    if not db.get_setting("subscribe_prompt") or duration < 12:
        return None
    g = genres.get(v.get("genre"))
    what = f"{g['label'].upper()} STORIES" if g else "STORIES LIKE THIS"
    if v["kind"] == "short":
        return (max(0.0, duration - 2.6), max(0.1, duration - 0.15), f"FOLLOW FOR MORE {what}")
    t0 = min(90.0, duration * 0.25)
    return (t0, t0 + 5.0, f"SUBSCRIBE FOR MORE {what}")


def st_editing(v, ch):
    st = _state(v)
    work = _work_dir(v["id"])
    portrait = v["kind"] == "short"
    tl = st["timeline"]
    shots = _shots(st)
    seed = f"v{v['id']}"
    accent = subtitles.ACCENTS[v["id"] % len(subtitles.ACCENTS)]
    headline = st.get("headline") or seo.thumbnail_text(v["topic"], v["script_json"].get("hook", ""), v["id"])
    st["headline"] = headline
    hook_end = tl["segments"][0]["end"] if tl["segments"] else 3.0
    segs = v["script_json"]["segments"]
    chapters, n = [], 1
    for s in shots:
        if s["chapter_start"]:
            n += 1
            chapters.append((s["start"], segs[s["seg_i"]].get("caption_title", ""), n))
    credits = [(s["start"] + 0.3, s["end"], _short_credit(s["asset"]["credit"])) for s in shots
               if s["asset"].get("provider") in visuals.SUBJECT_PROVIDERS and s["asset"].get("credit")]
    # both formats open on the curiosity headline from frame 1 (no slow title card on long-form)
    overlays = {"hook_title": headline, "hook_end": hook_end,
                "title": None, "chapters": chapters, "credits": credits,
                "duration": tl["duration"], "subscribe": subscribe_prompt(v, tl["duration"])}
    caps = subtitles.chunk_captions(tl, 3 if portrait else 6)
    srt = subtitles.write_srt(caps, work / "captions.srt")
    ass = subtitles.write_ass(caps, work / "captions.ass", portrait, overlays, accent=accent)
    g = genres.get(v.get("genre"))
    mood = g["mood"] if g else music.mood_for(f"{ch.get('niche', '')} {v['topic']} {v['script_json'].get('hook', '')}")
    mus, credit, msrc = music.pick_music(tl["duration"], work, f"music-{v['id']}", mood)
    st["music_credit"], st["music_source"], st["music_mood"] = credit, msrc, mood
    # sound-design cues
    events: list[tuple[float, str, float]] = []
    if portrait:
        events += [(max(0.0, hook_end - 1.9), "riser", 0.35), (max(0.0, hook_end - 0.05), "impact", 0.55)]
    else:
        events += [(0.15, "impact", 0.5)]
    for s in shots[1:]:
        if s["chapter_start"]:
            events.append((s["start"] - 0.1, "impact", 0.45))
        elif s["segment_start"]:
            events.append((s["start"] - 0.28, "whoosh", 0.32 if portrait else 0.22))
        elif portrait:
            events.append((s["start"], "tick", 0.12))          # subtle accent on every fast cut
    # tension riser into the final reveal (Shorts payoff beat / long-form last chapter)
    seg_starts = [s["start"] for s in shots if s["segment_start"]]
    if len(seg_starts) >= 3:
        reveal = seg_starts[-1] if portrait else (chapters[-1][0] if chapters else seg_starts[-1])
        events.append((max(0.0, reveal - 1.8), "riser", 0.28))
        if portrait:                                            # long-form chapter starts already carry an impact
            events.append((reveal - 0.05, "impact", 0.35))
    # two separate production pipelines: vertical Short vs landscape long-form (each validated on output)
    renderer = render.render_short if portrait else render.render_long
    out = renderer(shots, Path(tl["path"]), mus, ass, work, tl["duration"], v["id"], seed=seed, sfx_events=events)
    # chapter timestamps for the description (YouTube: first at 0:00, >=3 chapters, >=10s apart)
    if not portrait and chapters:
        times = [("0:00", "Introduction")]
        for t0, title, _ in chapters:
            if title and t0 >= 10:
                m, sec = divmod(int(t0), 60)
                times.append((f"{m}:{sec:02}", title))
        if len(times) >= 3:
            sj = v["script_json"]
            sj["chapter_times"] = times
            db.update_video(v["id"], script_json=sj)
    st["shots"] = len(shots)
    db.update_video(v["id"], storyboard_json=st, video_path=str(out), subtitle_path=str(srt))


def st_thumbnail(v, ch):
    """Thumbnail from the strongest real subject image (largest), else a frame from the edited video."""
    st = _state(v)
    work = _work_dir(v["id"])
    flat = [a for g in st["assets"] for a in g if not a.get("reused")]
    imgs = sorted([a for a in flat if a["type"] == "image" and a.get("w")], key=lambda a: -(a["w"] * a["h"]))
    if imgs:
        bg = Path(imgs[0]["path"])
    else:
        bg = work / "thumb_bg.jpg"
        ffmpeg.run(["-ss", f"{min(2.0, (v.get('duration_sec') or 4) / 2):.2f}", "-i", v["video_path"],
                    "-frames:v", "1", "-q:v", "2", str(bg)])
    text = st.get("headline") or seo.thumbnail_text(v["topic"], v["script_json"].get("hook", ""), v["id"])
    t = thumbnail.make_thumbnail(bg, text, work / "thumbnail.jpg", f"thumb-{v['id']}")
    db.update_video(v["id"], thumbnail_path=str(t))


def st_seo(v, ch):
    st = _state(v)
    credits = sorted({a["credit"] for g in st["assets"] for a in g if a.get("credit")})
    if st.get("music_credit"):
        credits.append(st["music_credit"])
    elif st.get("music_source") == "original":
        credits.append("Music and sound design: original, created for this video")
    meta = seo.optimize(v["topic"], v["kind"], v["script_json"], v["research_json"], ch, credits, v["id"])
    db.update_video(v["id"], seo_json=meta, title=meta["title"], description=meta["description"],
                    tags=json.dumps(meta["tags"]))


def st_quality(v, ch):
    from .media import formats
    portrait = v["kind"] == "short"
    sp = formats.spec(v["kind"])
    check = render.verify(Path(v["video_path"]), portrait, sp.hard_min, sp.hard_max)
    fmt = formats.validate(v["video_path"], v["kind"])
    if not fmt["ok"]:
        check["ok"] = False
        check["problems"] = check.get("problems", []) + fmt["errors"]
    s = v["script_json"]
    words = sum(word_count(x["text"]) for x in s["segments"])
    lm = script_agent.long_minutes(ch)
    target = (90, 170) if portrait else (int(lm * 145 * 0.6), int(lm * 145 * 1.6))
    others = db.query("SELECT script_json FROM videos WHERE channel_id=? AND id!=? AND script_json IS NOT NULL "
                      "ORDER BY id DESC LIMIT 50", (ch["id"], v["id"]))
    text = " ".join(x["text"] for x in s["segments"])
    sim = max([jaccard(text, " ".join(x["text"] for x in json.loads(o["script_json"])["segments"])) for o in others] or [0])
    fc = (v.get("factcheck_json") or {}).get("score", 0)
    parts = {
        "fact_check": round(35 * fc / 100),
        "ai_written": 15 if s.get("provider") != "template" else 0,
        "hook": 10 if word_count(s["segments"][0]["text"]) <= 22 else 4,
        "length": 15 if target[0] <= words <= target[1] else 6,
        "originality": 15 if sim < 0.35 else (6 if sim < 0.5 else 0),
        "render": 10 if check["ok"] else 0,
    }
    score = sum(parts.values())
    # hard blockers: any of these sends the video to human review regardless of total score
    blockers = []
    if words < target[0]:
        blockers.append(f"script too short ({words} < {target[0]} words)")
    if fc < 60:
        blockers.append(f"fact-check score {fc} < 60")
    if s.get("provider") == "template":
        blockers.append("written by offline template (no AI available)")
    if sim >= 0.5:
        blockers.append(f"too similar to a previous video ({sim:.2f})")
    blockers += fmt["warnings"]                       # e.g. Short outside 30-60 s, long outside 6-12 min
    # visuals: every scene must be backed by real licensed media, with enough variety
    vs = _state(v).get("visual_stats", {})
    nscenes = max(1, len(s["segments"]))
    if vs.get("unique", 0) < max(3, int(0.35 * (_state(v).get("shots") or nscenes))):
        blockers.append(f"too few unique relevant visuals ({vs.get('unique', 0)} for {_state(v).get('shots')} shots)")
    if vs.get("empty_scenes", 0) > 0.25 * nscenes:
        blockers.append(f"{vs.get('empty_scenes')} of {nscenes} scenes had no matching visual and reuse footage")
    # originality: narration must not lift sentences from the sources
    src = (v.get("research_json") or {}).get("all_text", "")
    copied = sum(1 for x in s["segments"] if copies_source(x["text"], src))
    if copied > 0.2 * nscenes:
        blockers.append(f"{copied} segments copy 10+ consecutive words from the source text")
    q = {"score": score, "parts": parts, "blockers": blockers, "render": check, "format": fmt, "words": words,
         "max_similarity": round(sim, 2), "visuals": vs, "copied_segments": copied,
         "music": _state(v).get("music_source"), "voice": (_state(v).get("timeline") or {}).get("engine")}
    if not check["ok"]:
        db.update_video(v["id"], quality_json=q)
        raise RuntimeError(f"render verification failed: {check['problems']}")
    db.update_video(v["id"], quality_json=q, duration_sec=check["duration"])
    status = post_qc_status({**v, "quality_json": q})
    db.update_video(v["id"], status=status)
    if status == "READY" and blockers and (v.get("slot_id") or v.get("scheduled_for")):
        logs.notify("WARNING", f"Video #{v['id']} will auto-upload at its slot despite quality warnings",
                    "; ".join(blockers)[:900])
    if status == "WAITING_FOR_APPROVAL" and v.get("scheduled_for") and not v.get("upload_immediately"):
        from . import scheduling
        at = scheduling.parse(v["scheduled_for"]).astimezone(scheduling.tz()).strftime("%d %b %H:%M")
        logs.notify("WARNING", f"Video #{v['id']} is ready - approve it before {at}",
                    f"'{v.get('title') or v['topic']}' is waiting for your approval. Approve it on the Upload queue "
                    f"page and it goes public on YouTube at {at}. Without approval it is not published."
                    + (f" Quality warnings: {'; '.join(blockers)[:600]}" if blockers else ""))
    logs.info("quality", f"score {score}, blockers {len(blockers)} -> {status}", v["id"])


def post_qc_status(v: dict) -> str:
    """Where a finished video goes: blockers always wait for a human; approval mode waits; else READY."""
    q = v.get("quality_json") or {}
    if q.get("approved_by_user"):
        return "READY"
    if v.get("character_ids"):               # AI character videos: no approval step, unless a quality check failed
        minimum = int(db.get_setting("min_quality_score") or 70)
        return "WAITING_FOR_APPROVAL" if q.get("blockers") or q.get("score", 0) < minimum else "READY"
    if (v.get("slot_id") or v.get("scheduled_for")) and db.get_setting("slot_auto_publish"):
        return "READY"                       # scheduled videos publish at their time without a human review
    minimum = int(db.get_setting("min_quality_score") or 70)
    if q.get("blockers") or q.get("score", 0) < minimum:
        return "WAITING_FOR_APPROVAL"
    if db.get_setting("upload_mode") == "approval" and not v.get("skip_review"):
        return "WAITING_FOR_APPROVAL"
    return "READY"


# ------------------------------------------------------------------ upload immediately
def immediate_block_reason(channel_id: int | None = None) -> str | None:
    """Why an 'upload immediately' video of this channel profile cannot go up right now (None = it can).
    Without a profile: whether uploads are possible at all (any linked account)."""
    from .youtube import oauth
    if db.get_setting("emergency_stop"):
        return "emergency stop is active"
    if db.get_setting("automation_paused"):
        return "automation is paused"
    if channel_id is None:
        return None if oauth.any_connected() else "no YouTube account is connected"
    if not oauth.is_connected(channel_id):
        ch = db.one("SELECT name FROM channels WHERE id=?", (channel_id,))
        return f"YouTube is not connected for the profile '{ch['name'] if ch else channel_id}'"
    return None


def upload_immediate(video_id: int) -> dict | None:
    """Upload a READY 'upload immediately' video now. Failures are recorded by do_upload (retried by the dispatcher)."""
    v = db.get_video(video_id)
    if not v or not v.get("upload_immediately") or v["status"] != "READY" or v.get("youtube_video_id"):
        return None
    reason = immediate_block_reason(v["channel_id"])
    if reason:
        db.update_video(video_id, error=f"upload immediately is waiting: {reason}")
        logs.warn("upload", f"immediate upload waiting: {reason}", video_id)
        return None
    try:
        return do_upload(video_id)
    except Exception as e:
        if "is not READY" not in str(e):          # lost the atomic claim to a concurrent caller: not an error
            logs.warn("upload", f"immediate upload failed: {e}", video_id)
        return None


def upload_immediate_async(video_id: int) -> None:
    """Uploads run in their own thread so a large upload never blocks the production workers."""
    threading.Thread(target=upload_immediate, args=(video_id,), name=f"autotube-up-{video_id}", daemon=True).start()


def do_upload(video_id: int) -> dict:
    """Upload one video. Atomic READY->UPLOADING claim prevents duplicate publishing even with concurrent callers."""
    from . import scheduling
    from .youtube import api as yt
    claimed = db.execute_rowcount("UPDATE videos SET status='UPLOADING', updated_at=datetime('now') "
                                  "WHERE id=? AND status='READY' AND youtube_video_id IS NULL", (video_id,))
    if claimed != 1:
        raise RuntimeError("video is not READY (already uploading/uploaded, or not approved)")
    v = db.get_video(video_id)
    try:
        res = yt.upload(v)
    except Exception as e:
        n = (v.get("upload_attempts") or 0) + 1
        limit = int(db.get_setting("upload_retry_count") or 3)
        msg = str(e)
        db.execute("INSERT INTO uploads(video_id, status, error) VALUES (?,?,?)", (video_id, "failed", msg[:1000]))
        if "Emergency stop" in msg:
            db.update_video(video_id, status="READY", error=f"upload: {msg}")
        elif n < limit:
            when = scheduling.next_quota_reset() if "limit reached" in msg or "quota" in msg.lower() \
                else scheduling.now_utc() + scheduling.upload_backoff(n)
            db.update_video(video_id, status="READY", upload_attempts=n, next_retry_at=scheduling.iso(when),
                            error=f"upload attempt {n}/{limit} failed: {msg}")
            logs.warn("upload", f"attempt {n}/{limit} failed; retry after {scheduling.iso(when)}: {msg}", video_id)
        else:
            db.update_video(video_id, status="FAILED", upload_attempts=n, error=f"upload failed {n} times: {msg}")
            logs.notify("ERROR", f"Upload failed: video #{video_id}", msg)
        raise
    published = "SCHEDULED" if res.get("publish_at") else "PUBLISHED"
    db.execute("INSERT INTO uploads(video_id, youtube_video_id, status, privacy, publish_at, response_json) VALUES (?,?,?,?,?,?)",
               (video_id, res["youtube_video_id"], "confirmed", res["privacy"], res.get("publish_at"), json.dumps(res)))
    now = scheduling.iso(scheduling.now_utc())
    db.update_video(video_id, youtube_video_id=res["youtube_video_id"], youtube_url=f"https://youtu.be/{res['youtube_video_id']}",
                    privacy=res["privacy"], yt_privacy=res["privacy"], yt_publish_at=res.get("publish_at"),
                    yt_upload_status=res.get("upload_status"), yt_checked_at=now, status=published, published_at=now,
                    thumbnail_status=res.get("thumbnail_status"), thumbnail_error=res.get("thumbnail_error"),
                    next_retry_at=None, error="; ".join(res.get("notes") or []) or None)
    when = f", goes public at {res['publish_at']}" if res.get("publish_at") else ""
    logs.notify("INFO", "Upload confirmed by YouTube",
                f"'{v['title']}' -> https://youtu.be/{res['youtube_video_id']} ({res['privacy']}{when}); "
                f"thumbnail: {res.get('thumbnail_status') or 'none'}")
    return res


def publish_now(video_id: int) -> dict:
    """Private/scheduled -> public on YouTube via videos.update (no re-upload). Requires the user's click."""
    from . import scheduling
    from .youtube import api as yt
    v = db.get_video(video_id)
    if not v or not v.get("youtube_video_id"):
        raise RuntimeError("video has not been uploaded")
    res = yt.publish_now(v)
    now = scheduling.iso(scheduling.now_utc())
    db.update_video(video_id, yt_privacy=res["privacy"], privacy=res["privacy"], yt_publish_at=None, yt_checked_at=now,
                    status="PUBLISHED", error=None)
    db.execute("INSERT INTO uploads(video_id, youtube_video_id, status, privacy, response_json) VALUES (?,?,?,?,?)",
               (video_id, v["youtube_video_id"], "privacy_updated", res["privacy"], json.dumps(res)))
    logs.notify("INFO", "Video is now public (confirmed by YouTube)", f"'{v['title']}' -> {v['youtube_url']}")
    return res


def retry_thumbnail(video_id: int) -> dict:
    from .youtube import api as yt
    v = db.get_video(video_id)
    res = yt.retry_thumbnail(v)
    db.update_video(video_id, thumbnail_status=res["status"], thumbnail_error=res["error"])
    return res


def refresh_youtube_status(video_ids: list[int] | None = None) -> dict:
    """Read the real privacy/publish state from YouTube; promote SCHEDULED -> PUBLISHED once YouTube shows public."""
    from . import scheduling
    from .youtube import api as yt
    rows = db.query("SELECT id, channel_id, youtube_video_id, status, yt_publish_at, title FROM videos "
                    "WHERE youtube_video_id IS NOT NULL"
                    + (f" AND id IN ({','.join('?' * len(video_ids))})" if video_ids else ""), tuple(video_ids or ()))
    if not rows:
        return {"checked": 0}
    statuses: dict[str, dict] = {}
    unchecked: set[int] = set()            # profiles whose account is not connected: nothing to compare against
    for cid in sorted({r["channel_id"] for r in rows}):
        try:
            statuses.update(yt.fetch_statuses([r["youtube_video_id"] for r in rows if r["channel_id"] == cid], cid))
        except yt.YouTubeError as e:
            unchecked.add(cid)
            logs.warn("youtube", f"status check skipped for profile {cid}: {e}")
    if unchecked and len(unchecked) == len({r["channel_id"] for r in rows}):
        raise yt.YouTubeError("YouTube is not connected for these videos' channel profile. Open the YouTube page.")
    rows = [r for r in rows if r["channel_id"] not in unchecked]
    now_dt = scheduling.now_utc()
    now = scheduling.iso(now_dt)
    changed = 0
    for r in rows:
        st = statuses.get(r["youtube_video_id"])
        if st is None:
            db.update_video(r["id"], yt_checked_at=now, yt_privacy="missing",
                            error="YouTube no longer returns this video (deleted or removed?)")
            continue
        fields = {"yt_privacy": st.get("privacyStatus"), "privacy": st.get("privacyStatus"),
                  "yt_publish_at": st.get("publishAt"), "yt_upload_status": st.get("uploadStatus"), "yt_checked_at": now}
        if r["status"] == "SCHEDULED" and st.get("privacyStatus") == "public":
            fields.update(status="PUBLISHED", error=None)
            changed += 1
            logs.notify("INFO", "Scheduled video went public (verified)", r["title"] or str(r["id"]))
        elif r["status"] == "SCHEDULED" and r["yt_publish_at"] and \
                scheduling.parse(r["yt_publish_at"]) < now_dt - dt.timedelta(minutes=30) and st.get("privacyStatus") != "public":
            fields["error"] = "scheduled publish time passed but YouTube still shows it as " + str(st.get("privacyStatus"))
            logs.notify("WARNING", f"Video #{r['id']} did not go public on schedule", fields["error"])
        db.update_video(r["id"], **fields)
    return {"checked": len(rows), "published": changed}


RUNNERS = {"research": st_research, "script": st_script, "factcheck": st_factcheck, "storyboard": st_storyboard,
           "visuals": st_visuals, "voiceover": st_voiceover, "editing": st_editing, "thumbnail": st_thumbnail,
           "seo": st_seo, "quality": st_quality}
STAGE_STATUS = {"research": "GENERATING", "script": "GENERATING", "factcheck": "GENERATING", "storyboard": "GENERATING",
                "visuals": "GENERATING", "voiceover": "GENERATING", "editing": "RENDERING", "thumbnail": "RENDERING",
                "seo": "QUALITY_CHECK", "quality": "QUALITY_CHECK"}
PRODUCTION_STATES = ("DRAFT", "GENERATING", "RENDERING", "QUALITY_CHECK")


def run_video(video_id: int, worker: str = "w0") -> None:
    v = db.get_video(video_id)
    if not v or v["status"] not in PRODUCTION_STATES:
        return
    ch = genres.channel_with_genre(db.one("SELECT * FROM channels WHERE id=?", (v["channel_id"],)), v.get("genre"))
    try:                                     # learn from this channel's best performers (titles / hooks)
        from . import growth
        ch = {**ch, "best_titles": growth.top_examples(v["channel_id"], v["kind"], "title"),
              "best_hooks": growth.top_examples(v["channel_id"], v["kind"], "hook")}
    except Exception as e:
        logs.warn("growth", f"performance examples unavailable: {e}", video_id)
    if v.get("character_ids"):                   # AI character host(s): the script is written in their voice
        from . import characters
        cast = characters.video_cast(v)
        if cast:
            ch = {**ch, "tone": (ch.get("tone") or "") + characters.persona_text(v.get("character_mode"), cast)}
    stage0 =v["stage"] if v["stage"] in STAGES else "done"
    if stage0 in ("upload", "done"):
        db.update_video(video_id, status=post_qc_status(v))
        return
    try:
        for stage in STAGES[STAGES.index(stage0):]:
            if stage in ("upload", "done"):
                break
            if db.get_setting("emergency_stop"):
                raise Stopped()
            _current[worker] = {"video_id": video_id, "stage": stage}
            db.update_video(video_id, stage=stage, status=STAGE_STATUS[stage], next_retry_at=None)
            logs.info("pipeline", f"stage {stage}", video_id)
            v = db.get_video(video_id)
            RUNNERS[stage](v, ch)
        db.update_video(video_id, stage="done")
        v = db.get_video(video_id)
        if v.get("upload_immediately"):
            if v["status"] == "READY":
                upload_immediate_async(video_id)
            elif v["status"] == "WAITING_FOR_APPROVAL":
                logs.notify("WARNING", f"Video #{video_id} needs your review before it uploads",
                            "It was set to upload immediately, but quality checks or approval mode require a human "
                            "review. Approve it and it uploads right away.")
    except Stopped:
        db.update_video(video_id, status="PAUSED")
        logs.warn("pipeline", "paused by emergency stop", video_id)
    except Exception as e:
        attempts = (v.get("attempts") or 0) + 1
        max_r = int(db.get_setting("max_retries") or 3)
        if isinstance(e, research.NoSourceError):
            attempts = max_r                 # same topic can never be researched: fail now, don't retry for 15 min
        stage = db.get_video(video_id)["stage"]
        if "regenerating" in str(e):
            db.update_video(video_id, stage="script")   # rewrite the script on next attempt
        if attempts < max_r:
            from . import scheduling
            when = scheduling.now_utc() + dt.timedelta(minutes=5 * attempts)
            db.update_video(video_id, status="DRAFT", attempts=attempts, next_retry_at=scheduling.iso(when),
                            error=f"[{stage}] attempt {attempts}/{max_r}: {e}")
        else:
            db.update_video(video_id, status="FAILED", attempts=attempts, error=f"[{stage}] {e}")
            logs.notify("ERROR", f"Video #{video_id} failed", f"[{stage}] {e}")
        logs.error("pipeline", f"stage {stage} failed (attempt {attempts}/{max_r}): {e}\n{traceback.format_exc()[-1500:]}",
                   video_id)
    finally:
        _current.pop(worker, None)


# ------------------------------------------------------------------ workers
def _loop(name: str):
    while True:
        _, _, vid = _q.get()
        try:
            run_video(vid, name)
        except Exception as e:
            logs.error("worker", f"unexpected: {e}")
        finally:
            _queued.discard(vid)
            _q.task_done()


def ensure_workers() -> None:
    """Keep `max_concurrent_renders` production workers alive (changes apply without restart)."""
    want = max(1, min(4, int(db.get_setting("max_concurrent_renders") or 1)))
    alive = [w for w in _workers if w.is_alive()]
    _workers[:] = alive
    while len(_workers) < want:
        name = f"w{len(_workers)}"
        t = threading.Thread(target=_loop, args=(name,), name=f"autotube-{name}", daemon=True)
        t.start()
        _workers.append(t)


def recover() -> dict:
    """Crash recovery on startup: re-queue interrupted production; never blindly re-upload."""
    requeued, flagged = 0, 0
    for r in db.query("SELECT id FROM videos WHERE status IN ('DRAFT','GENERATING','RENDERING','QUALITY_CHECK')"):
        if r["id"] not in _queued:
            db.update_video(r["id"], status="DRAFT")
            enqueue(r["id"])
            requeued += 1
    for r in db.query("SELECT id FROM videos WHERE status='UPLOADING'"):
        db.update_video(r["id"], status="FAILED",
                        error="upload was interrupted by a shutdown. Check YouTube Studio: if the video is not there, "
                              "press 'Retry upload'. (Not retried automatically to avoid publishing it twice.)")
        logs.notify("WARNING", f"Video #{r['id']}: interrupted upload needs your check")
        flagged += 1
    return {"requeued": requeued, "uploads_flagged": flagged}


def start_worker() -> dict:
    ensure_workers()
    return recover()


def enqueue(video_id: int) -> None:
    if video_id in _queued:
        return
    _queued.add(video_id)
    v = db.get_video(video_id)
    if v and v["status"] not in PRODUCTION_STATES:
        db.update_video(video_id, status="DRAFT")
    prio = (v or {}).get("scheduled_for") or "~"     # ISO UTC sorts chronologically; "~" sorts after any date
    if v and v.get("upload_immediately") and not v.get("scheduled_for"):
        prio = "9"      # "make it now": after slot videos (dates "20..") so no slot is missed, before background jobs ("~")
    _q.put((prio, next(_seq), video_id))


def status() -> dict:
    cur = list(_current.values())
    return {"current": cur[0] if cur else {"video_id": None, "stage": None}, "workers": cur,
            "worker_count": len([w for w in _workers if w.is_alive()]),
            "queue_length": _q.qsize(), "queued_ids": sorted(_queued)}


# ------------------------------------------------------------------ creation
def create_video(channel_id: int, kind: str, topic: str, scheduled_for: str | None = None,
                 slot_id: int | None = None, slot_date: str | None = None, genre: str | None = None,
                 upload_immediately: bool = False, skip_review: bool = False, companion: bool = False,
                 character_ids: list[int] | None = None, character_mode: str | None = None) -> int:
    """companion=True: the second format (Short + long) of the same story created together - not a duplicate.
    character_ids / character_mode: AI character host(s) - validate with characters.validate_cast first."""
    ch = db.one("SELECT * FROM channels WHERE id=?", (channel_id,))
    if not ch:
        raise ValueError("channel not found")
    if genre and not genres.get(genre):
        raise ValueError(f"unknown genre '{genre}'")
    if not companion and research.is_duplicate(channel_id, topic):
        raise ValueError(f"Duplicate topic prevented: '{topic}' is too similar to an existing video")
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status, scheduled_for, slot_id, slot_date, "
                     "privacy, target_privacy, genre, upload_immediately, skip_review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (channel_id, kind, topic, normalize_key(topic), "DRAFT", scheduled_for, slot_id, slot_date,
                      "private", "public" if upload_immediately else (db.get_setting("default_privacy") or "private"),
                      genre or None, int(bool(upload_immediately)), int(bool(skip_review and upload_immediately))))
    if character_ids:
        db.update_video(vid, character_ids=json.dumps(list(character_ids)), character_mode=character_mode)
    logs.info("pipeline", f"created {kind} '{topic}'" + (f" [{genre}]" if genre else "")
              + (f" with AI characters {list(character_ids)} ({character_mode})" if character_ids else "")
              + (" - upload immediately" if upload_immediately else "")
              + (f" for slot {slot_date} {scheduled_for}" if slot_id else ""), vid)
    return vid


def daily_report() -> dict:
    from . import quota, scheduling
    day = scheduling.today_local().isoformat()
    rows = db.query("SELECT status, kind, COUNT(*) n FROM videos WHERE slot_date=? OR date(updated_at)=date('now') "
                    "GROUP BY status, kind", (day,))
    rep = {"day": day, "videos": rows, "quota": quota.summary(), "today": scheduling.today_summary(),
           "errors": db.one("SELECT COUNT(*) n FROM logs WHERE level='ERROR' AND date(created_at)=date('now')")["n"]}
    db.execute("INSERT INTO daily_reports(day, report_json) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET report_json=excluded.report_json",
               (day, json.dumps(rep)))
    logs.notify("INFO", f"Daily report {day}", json.dumps(rows))
    return rep
