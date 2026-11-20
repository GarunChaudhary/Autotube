"""SEO agent: title, description (what the video shows + hashtags), tags, hashtags."""
from __future__ import annotations

import re
from typing import Any

from .. import llm, logs
from . import genres
from .text_utils import content_words

SYSTEM = ("You are a YouTube growth strategist. Titles open a curiosity gap the video honestly pays off (never "
          "clickbait it doesn't deliver), front-load the most surprising element, are under 60 characters, and never "
          "start with the topic name or 'The Story of'. Title and description may ONLY state things the NARRATION "
          "actually says - never add places, events, numbers or claims that are not in it. Descriptions open with a "
          "one-line hook, then say plainly what the video shows; helpful and natural, not keyword-stuffed, no links, "
          "no 'stay tuned', no 'like and subscribe'.")


def thumbnail_text(topic: str, hook: str, video_id: int | None = None) -> str:
    try:
        data, _ = llm.generate_json(
            f'TOPIC: {topic}\nHOOK: {hook}\nWrite thumbnail / opening headline text: 2-4 punchy words that create a '
            f'curiosity gap (a contradiction, a stake or an unanswered question), honest to the story, no emojis, '
            f'do not just repeat the title. Return JSON: {{"text": "..."}}', SYSTEM, 0.9, video_id, role="writer")
        txt = re.sub(r"[^\w\s?!'-]", "", str(data.get("text", ""))).strip()
        if 1 <= len(txt.split()) <= 6:
            return txt
    except Exception:
        pass
    return " ".join(topic.split()[:5])


CURIOSITY = {"why", "how", "never", "nobody", "still", "secret", "vanished", "impossible", "truth", "really",
             "mystery", "unsolved", "strange", "hidden", "lost", "only", "first", "last", "but", "without"}
WEAK = ("the story of", "the history of", "all about", "you won't believe", "shocking!!!", "explained")


def title_score(title: str, topic: str) -> float:
    """Heuristic click-worthiness: length sweet spot, curiosity words, specifics; penalise weak/clickbait patterns."""
    t = title.strip()
    low = t.lower()
    s = 0.0
    n = len(t)
    s += 3 if 30 <= n <= 60 else (1 if 20 <= n <= 70 else -3)
    s += min(3, sum(1 for w in re.findall(r"[a-z']+", low) if w in CURIOSITY))
    s += 1.5 if re.search(r"\d", t) else 0
    s -= 3 if low.startswith(topic.lower()[:15]) else 0
    s -= 4 if any(w in low for w in WEAK) else 0
    s -= 3 if t.isupper() or t.count("!") > 1 else 0
    s -= 2 if "?" in t and t.count("?") > 1 else 0
    return s


def numbers_supported(text: str, narration: str) -> bool:
    """Every number in a title must appear in the narration (no invented '39 years', '100 people')."""
    have = set(re.findall(r"\d+", narration))
    return all(n in have for n in re.findall(r"\d+", text))


def pick_title(options: list[str], topic: str, narration: str | None = None) -> str:
    """Model's order breaks ties: the first option it wrote gets a small bonus."""
    opts = [o.strip().strip('"')[:95] for o in options if o and o.strip()]
    if narration is not None:
        opts = [o for o in opts if numbers_supported(o, narration)]
    if not opts:
        return ""
    return max(enumerate(opts), key=lambda io: title_score(io[1], topic) + (0.5 if io[0] == 0 else 0))[1]


def _tags_total_ok(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = re.sub(r"[<>\"]", "", t).strip()[:60]
        if not t or t.lower() in {x.lower() for x in out}:
            continue
        if total + len(t) + 2 > 480:          # YouTube limit is 500 chars incl. separators
            break
        out.append(t)
        total += len(t) + 2
    return out


def optimize(topic: str, kind: str, script: dict, research: dict, channel: dict,
             credits: list[str], video_id: int | None = None) -> dict[str, Any]:
    narration = " ".join(s["text"] for s in script["segments"])[:3000]
    g = genres.get(channel.get("genre"))
    genre_txt = (f"GENRE: {g['label']} - title style: {g['title_style']}. Use search phrases viewers of this genre "
                 f"actually type (e.g. {', '.join(g['tags'][:4])}) in the title, first description line and tags, "
                 "naturally.\n") if g else ""
    best = channel.get("best_titles") or []
    best_txt = ("THIS CHANNEL'S BEST-PERFORMING TITLES (learn the pattern, never copy):\n- " + "\n- ".join(best) + "\n"
                ) if best else ""
    try:
        prompt = f"""VIDEO TYPE: {'YouTube Short (vertical, under 60s)' if kind == 'short' else 'long-form video'}
TOPIC: {topic}
CHANNEL NICHE: {channel.get('niche')}
{genre_txt}{best_txt}NARRATION: {narration}
Return JSON: {{"titles": ["5 different curiosity-gap titles under 60 characters, each a different angle: a contradiction, a hidden detail, stakes, a number from the narration, an unanswered question"], "description": "a one-line hook, then 2-3 short paragraphs that describe what actually happens in this video, in the order the narration tells it, using only facts from the NARRATION (do not give away the final reveal)", "tags": ["10-20 relevant tags"], "hashtags": ["5 hashtags without #, specific to this story (names, places, subject)"], "comment": "one short friendly question (max 20 words) the channel posts as the first comment, inviting viewers to share their opinion or theory about THIS story"}}"""
        data, _ = llm.generate_json(prompt, SYSTEM, 0.6, video_id, role="writer")
        options = [str(t) for t in (data.get("titles") or [])] + ([str(data["title"])] if data.get("title") else [])
        title = pick_title(options, topic, narration) or topic
        comment = re.sub(r"(https?://|www\.)\S+", "", str(data.get("comment", ""))).strip()[:200]
        desc = str(data.get("description", "")).strip()
        tags = [str(t) for t in data.get("tags", [])]
        hashtags = [re.sub(r"[^\w]", "", str(h)) for h in data.get("hashtags", [])][:5]
    except Exception as e:
        logs.warn("seo", f"LLM SEO unavailable ({e}); using heuristic SEO", video_id)
        options, comment = [], ""
        title = f"What Really Happened: {topic}"[:95]
        desc = script["segments"][0]["text"] + "\n\n" + " ".join(s["text"] for s in script["segments"][1:3])
        words = sorted(content_words(narration), key=lambda w: -narration.lower().count(w))
        tags = [topic, channel.get("niche", "")] + words[:15]
        hashtags = [re.sub(r"\W", "", topic.title())[:30], re.sub(r"\W", "", (channel.get("niche") or "facts").title())[:30]]

    if g:
        # genre search terms first (most-searched discovery phrases), then the video-specific tags
        tags = [topic] + g["tags"][:5] + tags + g["tags"][5:]
        hashtags = [h for h in hashtags if h.lower() not in {x.lower() for x in g["hashtags"]}]
        # the first 3 hashtags show above the title: lead with the story, then genre discovery tags
        hashtags = hashtags[:1] + g["hashtags"] + hashtags[1:3]
    if kind == "short" and "#shorts" not in title.lower() and len(title) <= 88:
        title = f"{title} #Shorts"
    if kind == "short":
        hashtags = ["Shorts"] + [h for h in hashtags if h.lower() != "shorts"]
    chapters = ""
    if script.get("chapter_times"):
        chapters = "\n\nChapters:\n" + "\n".join(f"{t} {n}" for t, n in script["chapter_times"])
    # Description = what the video shows + hashtags only. CC-licensed media is credited on screen inside the video,
    # Pexels/Pixabay need no credit, and the AI voice is disclosed via YouTube's containsSyntheticMedia flag at
    # upload - so no source links, domains or credit lists are added here.
    desc = re.sub(r"(https?://|www\.)\S+", "", desc)
    desc = re.sub(r"[ \t]+\n", "\n", desc).strip()
    seen: set[str] = set()
    hashtags = [h for h in hashtags if h and not (h.lower() in seen or seen.add(h.lower()))][:8]
    description = f"{desc}{chapters}\n\n" + " ".join(f"#{h}" for h in hashtags)
    return {"title": title, "description": description[:4900], "tags": _tags_total_ok(tags + [topic]),
            "genre": channel.get("genre"), "title_options": options[:6], "comment": comment,
            "hashtags": hashtags}
