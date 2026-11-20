"""Research agent: picks fresh topics for a niche and gathers grounded source material from Wikipedia
(free, no key, CC BY-SA - we credit the article in the description)."""
from __future__ import annotations

import random
import re
import unicodedata
from typing import Any

import requests

from .. import db, llm, logs
from .text_utils import jaccard, normalize_key

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "AutoTubeAI/1.0 (personal local research tool; low volume)"}


def _wiki(params: dict) -> dict:
    """Wikipedia API call; backs off politely on 429/5xx (rate limit) instead of failing the topic."""
    import time
    params = {"format": "json", "formatversion": 2, **params}
    for attempt in range(4):
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=30)
        if r.status_code in (429, 503) and attempt < 3:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait) if wait.isdigit() else 2 ** (attempt + 1)))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def wiki_search(q: str, limit: int = 10) -> list[str]:
    data = _wiki({"action": "query", "list": "search", "srsearch": q, "srlimit": limit, "srnamespace": 0})
    return [h["title"] for h in data.get("query", {}).get("search", [])]


def wiki_article(title: str, max_chars: int = 9000) -> dict[str, Any] | None:
    data = _wiki({"action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain",
                  "titles": title, "redirects": 1, "inprop": "url"})
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    p = pages[0]
    text = p.get("extract", "")
    # drop trailing reference-ish sections
    for marker in ("\nSee also", "\nReferences", "\nFurther reading", "\nExternal links", "\nNotes"):
        i = text.find(marker)
        if i > 0:
            text = text[:i]
    return {"title": p["title"], "url": p.get("fullurl", f"https://en.wikipedia.org/wiki/{p['title'].replace(' ', '_')}"),
            "text": text[:max_chars].strip()}


class NoSourceError(RuntimeError):
    """The topic has no usable source - retrying the same topic can never succeed."""


# words that describe the ANGLE of a story, not its subject (ignored when checking an idea against an article)
GENERIC = set("""mystery mysteries mysterious legend legends story stories tale tales haunted haunting haunt ghost ghosts
phantom strange unexplained true real famous incident case curse cursed secret secrets dark eerie creepy bizarre weird
unsolved phenomenon untold forgotten shocking terrifying scary horror funny hilarious absurd amazing incredible
surprising unbelievable inside behind truth hidden lost great history historical estate
disappearance disappearances disappeared vanishing vanished""".split())


def _cw(text: str) -> set[str]:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = (re.sub(r"our$", "or", w) if len(w) > 4 else w for w in re.findall(r"[a-z0-9]+", t))   # colour = color
    return {w for w in words if len(w) > 2 and w not in {"the", "and", "for", "from", "with",
                                                                             "that", "this", "who", "how", "why", "what"}}


def _strip_angle(topic: str) -> str:
    """'Dyatlov Pass incident: The Mystery Nobody Solved' -> 'Dyatlov Pass incident'."""
    return re.split(r"\s*[:|–—]\s*|\s+-\s+", topic, maxsplit=1)[0].strip()


def verify_topic(topic: str) -> str | None:
    """Return the Wikipedia article title that really covers this topic, or None if it has no source
    (e.g. an AI-invented legend). Both checks must pass: the article title is mostly words of the idea, and the
    idea's subject words appear in the article itself - so 'Phantom Horse of <real place>' is not accepted
    just because an article about the place exists."""
    words = _cw(topic)
    core = words - GENERIC
    stems = lambda ws: {w[:6] for w in ws}           # noqa: E731  disappearance ~ disappeared
    ordered = sorted(core, key=lambda w: unicodedata.normalize("NFKD", topic).lower().find(w))
    queries = list(dict.fromkeys(q for q in [topic, _strip_angle(topic), " ".join(ordered), " ".join(ordered[:3])] if q))
    for q in queries:
        for hit in wiki_search(q, 3):
            hw = _cw(hit)
            if not hw or len(hw & words) / len(hw) < 0.6:
                continue                     # the article is about something else
            if core and len(hw & core) / len(core) < 0.5 and not (hw - GENERIC) >= core:
                continue                     # title covers too little of the idea ('BT Tower' for 'Tower of London')
            art = wiki_article(hit, 6000)
            if not art or len(art["text"]) < 800:
                continue
            # subject words the title doesn't cover (the 'horse' in 'Phantom Horse of <place>') must be in the article
            extras = stems(core - hw)
            body = stems(_cw(hit + " " + art["text"]))
            if extras and len(extras & body) / len(extras) < 0.6:
                continue
            return art["title"]
    return None


def _used_topics(channel_id: int) -> list[str]:
    return [r["topic"] for r in db.query("SELECT topic FROM videos WHERE channel_id=?", (channel_id,))]


def is_duplicate(channel_id: int, topic: str, threshold: float = 0.6) -> bool:
    key = normalize_key(topic)
    if db.one("SELECT id FROM videos WHERE channel_id=? AND topic_key=? AND status!='failed'", (channel_id, key)):
        return True
    return any(jaccard(topic, t) >= threshold for t in _used_topics(channel_id))


def suggest_topics(channel: dict, count: int = 5, video_id: int | None = None, genre: str | None = None) -> list[str]:
    """Return `count` fresh, specific topic ideas for the channel niche (duplicates filtered).
    With a genre, ideas are true stories that fit that genre (e.g. horror -> real eerie events)."""
    from . import genres, topics
    g = genres.get(genre)
    if g:
        # real articles from Wikipedia lists/categories for this genre, ranked by 30-day pageviews (audience demand)
        try:
            found = [t["topic"] for t in topics.ready(genre, channel["id"], count)]
            if len(found) >= count:
                return found
        except Exception as e:
            found = []
            logs.warn("research", f"genre topic finder failed ({e}); falling back to AI ideas", video_id)
        return found + [t for t in _ai_topics(channel, count - len(found), video_id, g) if t not in found]
    return _ai_topics(channel, count, video_id, None)


def _ai_topics(channel: dict, count: int, video_id: int | None, g: dict | None) -> list[str]:
    """LLM topic ideas, each verified against Wikipedia (invented stories are dropped)."""
    if count <= 0:
        return []
    used = _used_topics(channel["id"])
    ideas: list[str] = []
    try:
        genre_line = (f"Genre: {g['label']} - every idea must be one of: {g['topic_hint']}. Prefer subjects with high "
                      f"viewer curiosity and search demand; fit the channel niche where possible.\n") if g else ""
        prompt = (
            f"Channel niche: {channel['niche']}\nAudience: {channel['audience'] or 'general curious viewers'}\n"
            f"{genre_line}"
            f"Already covered (do NOT repeat or rephrase): {used[-40:]}\n\n"
            f"Suggest {count * 2} specific, surprising, factual video topics. Each must be a concrete subject "
            f"that has a Wikipedia article (a real event, person, place, phenomenon, invention, species...). "
            'Return JSON: {"topics": ["...", "..."]}'
        )
        data, _ = llm.generate_json(prompt, "You are a meticulous YouTube research producer.", 0.9, video_id)
        ideas = [str(t).strip() for t in data.get("topics", []) if str(t).strip()]
    except Exception as e:
        logs.warn("research", f"LLM topic ideation unavailable ({e}); using Wikipedia search", video_id)
    if len(ideas) < count:
        try:
            pool = wiki_search(g["topic_hint"].split(",")[0] if g else channel["niche"], 30)
            random.shuffle(pool)
            ideas += pool
        except Exception as e:
            logs.error("research", f"Wikipedia search failed: {e}", video_id)
    # small models invent plausible-sounding stories: keep only ideas backed by a real Wikipedia article.
    # Candidates are verified in parallel (each check is several Wikipedia requests).
    cands: list[str] = []
    for t in ideas:
        if not is_duplicate(channel["id"], t) and all(jaccard(t, c) < 0.6 for c in cands):
            cands.append(t)
        if len(cands) >= count * 3:
            break

    def check(t: str) -> str | None:
        try:
            real = verify_topic(t)
        except Exception as e:
            logs.warn("research", f"could not verify idea '{t}': {e}", video_id)
            return None
        if not real:
            logs.info("research", f"dropped idea with no Wikipedia source: '{t}'", video_id)
        return real

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        verified = list(ex.map(check, cands))
    fresh: list[str] = []
    for real in verified:
        if real and not is_duplicate(channel["id"], real) and all(jaccard(real, f) < 0.6 and real != f for f in fresh):
            fresh.append(real)
        if len(fresh) >= count:
            break
    return fresh


def research(topic: str, niche: str, video_id: int | None = None) -> dict[str, Any]:
    """Ground the video in the ONE Wikipedia article that is really about the topic (verified, see verify_topic).
    No loose fallbacks: a search for '<topic> <niche>' used to pull in unrelated articles (e.g. a railway for an
    invented ghost story), which produced scripts about the wrong subject."""
    title = verify_topic(topic)
    if not title:
        raise NoSourceError(f"No Wikipedia source exists for '{topic}' - it may not be a real, documented story. "
                            "Pick a different topic (use 'Suggest topics' for verified ones).")
    art = wiki_article(title, 16000)
    if not art or len(art["text"]) < 800:
        raise NoSourceError(f"The Wikipedia article '{title}' is too short to build a video from.")
    logs.info("research", f"source: {art['title']} ({len(art['text'])} chars)", video_id)
    return {"topic": topic, "sources": [art], "primary_text": art["text"], "all_text": art["text"]}
