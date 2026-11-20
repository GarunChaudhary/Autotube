"""Script writing agent. Produces a grounded, original narration split into segments, each with a visual cue.

Output schema:
{"hook": str, "segments": [{"text": str, "visual": str, "caption_title": str}], "cta": str, "provider": str}
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

from .. import llm, logs
from .genres import style_block
from .text_utils import clean_for_speech, sentences, word_count

SYSTEM = (
    "You are a top YouTube retention scriptwriter. You write ORIGINAL narration in your own words (never copy "
    "sentences from the source) engineered for watch time: the first sentence stops the scroll in under 2 seconds, "
    "an open loop makes viewers need the ending, every beat adds a new surprising turn, and sentences are short and "
    "punchy so the edit can cut fast. You ONLY state facts supported by the provided SOURCE text - the hook and "
    "teases must be honest and paid off. No filler, no 'in this video', no 'hey guys', no 'smash that like button', "
    "no emojis."
)

SHORT_WORDS = (95, 140)       # ~40-55 seconds of narration

# Retention devices. The open loop goes in the hook beat and the loop line in the last beat (both framing beats,
# so the fact-checker never strips them); the loop line leads back into the hook so the Short replays seamlessly.
OPEN_LOOPS = ["But that is not the strangest part.", "And the ending makes it even stranger.",
              "Wait until you hear how it ends.", "But nobody expected what came next."]


def _long_words(minutes: int) -> int:
    return int(minutes * 145)


def _visual_from(text: str, topic: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]{4,}", text) if w.lower() not in {"this", "that", "with", "were", "they", "their", "which", "when", "there", "about"}]
    return " ".join([topic] + words[:2])


def _norm_segments(raw: list, topic: str) -> list[dict[str, str]]:
    segs = []
    for s in raw:
        if isinstance(s, str):
            s = {"text": s}
        text = clean_for_speech(str(s.get("text") or s.get("narration") or ""))
        if not text:
            continue
        segs.append({
            "text": text,
            "visual": str(s.get("visual") or s.get("visual_query") or _visual_from(text, topic))[:80],
            "caption_title": str(s.get("caption_title") or s.get("on_screen_text") or "")[:40],
        })
    return segs


def dedupe_segments(segs: list[dict]) -> list[dict]:
    """Drop beats that repeat an earlier beat (small models often restate the same fact)."""
    from .text_utils import jaccard
    out: list[dict] = []
    for s in segs:
        if all(jaccard(s["text"], o["text"]) < 0.5 for o in out):
            out.append(s)
    return out


SHORT_EXAMPLE = """EXAMPLE OF THE STYLE (different topic, do not reuse its facts):
{"hook": "Something flattened 80 million trees in Siberia. Nobody saw it land.",
 "open_loop": "And what scientists found there still makes no sense.",
 "segments": [
  {"text": "June 1908. A fireball brighter than the sun tears across the sky over the Tunguska river.", "visual": "fireball sky", "caption_title": "1908"},
  {"text": "Then the blast hits. A thousand times stronger than Hiroshima. People sixty kilometres away are thrown off their feet.", "visual": "explosion shockwave", "caption_title": "The blast"},
  {"text": "For nineteen years, nobody goes to look. When scientists finally arrive, they expect a giant crater.", "visual": "siberian forest", "caption_title": "No crater"},
  {"text": "There is no crater. At the centre, the trees are still standing. Stripped bare. Everything around them lies flat.", "visual": "fallen trees forest", "caption_title": "Still standing"},
  {"text": "The fallen trunks form a giant butterfly shape, all pointing away from one empty spot.", "visual": "aerial forest", "caption_title": "Butterfly"}],
 "payoff": "The answer? A space rock that exploded in the air before it ever touched the ground.",
 "loop_line": "Which is why, to this day,"}
(The loop_line is an unfinished lead-in that flows straight back into the hook when the Short replays.)"""


def _join(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _loop_line(text: str) -> str:
    """A loop line must be short and must not end the thought (no full stop) so it runs into the hook."""
    t = clean_for_speech(text).rstrip(".!?;:, ")
    return t if 2 <= word_count(t) <= 9 else ""


def attach_loop_line(script: dict) -> dict:
    """Shorts: end on an unfinished lead-in (trailing comma, rising intonation) that flows into the hook on replay."""
    loop = script.get("loop_line") or ""
    segs = script.get("segments") or []
    if loop and segs and not script.get("loop_attached"):
        last = segs[-1]["text"].rstrip()
        segs[-1]["text"] = f"{last} {loop},"
        script["loop_attached"] = True
    return script


BANNED_OPENERS = ("did you know", "this is", "in this video", "have you ever", "today", "hey", "welcome", "meet ")


def hook_ok(hook: str, topic: str) -> bool:
    h = hook.strip().lower()
    n = word_count(hook)
    return 5 <= n <= 18 and not h.startswith(BANNED_OPENERS) and not h.startswith(topic.lower()[:12]) and "..." not in h


def best_hook(topic: str, src: str, current: str, video_id: int | None = None,
              examples: list[str] | None = None, fmt: str = "Short") -> str:
    """Write several alternative scroll-stopping openers from the SOURCE, have the model rank them, keep the
    strongest one that passes the rules (true, short, not a banned opener). Falls back to the current hook."""
    ex = ("\nTHIS CHANNEL'S BEST-PERFORMING HOOKS (match their energy, not their facts):\n- " + "\n- ".join(examples)
          ) if examples else ""
    prompt = f"""TOPIC: {topic}
SOURCE (the ONLY facts you may use):
\"\"\"{src[:3500]}\"\"\"
CURRENT HOOK: {current}{ex}

Write 4 NEW alternative opening lines for a YouTube {fmt} about this TOPIC, each 6-14 words, each a different angle:
1) the single most shocking TRUE fact, 2) a contradiction or impossibility, 3) the stakes (life, death, money, secrecy),
4) a vivid in-the-moment scene. Never start with the topic name, "Did you know", "This is", a question, or a bare date.
Then rank all 5 lines (the 4 new + CURRENT) by how strongly they stop a viewer scrolling in under 2 seconds while
staying 100% supported by the SOURCE.
Return JSON: {{"hooks": ["...", "...", "...", "..."], "ranking": ["best line verbatim", "second", "..."]}}"""
    try:
        data, _ = llm.generate_json(prompt, SYSTEM, 0.85, video_id, role="writer")
        from .factcheck import verify_claim
        pool: list[str] = []
        for h in (data.get("ranking") or []) + (data.get("hooks") or []):
            h = clean_for_speech(str(h))
            if h and h not in pool:
                pool.append(h)
        checked = 0
        for h in pool:
            if h == current:
                return current                      # the original ranked best: keep it (checked later as usual)
            if not hook_ok(h, topic):
                continue
            if checked >= 3:
                break
            checked += 1
            if verify_claim(h, src, video_id):      # a new hook must be strictly true before it can lead the video
                logs.info("script", f"hook upgraded: '{h}' (was '{current}')", video_id)
                return h
    except Exception as e:
        logs.warn("script", f"hook alternatives unavailable ({e}); keeping the first hook", video_id)
    return current


def write_short(topic: str, research: dict, channel: dict, video_id: int | None = None) -> dict[str, Any]:
    src = research["primary_text"][:5000]
    prompt = f"""TOPIC: {topic}
TONE: {channel.get('tone')}
AUDIENCE: {channel.get('audience') or 'curious general audience'}
{style_block(channel.get('genre'))}
SOURCE (the ONLY facts you may use):
\"\"\"{src}\"\"\"

{SHORT_EXAMPLE}

Now write a viral YouTube Short narration about the TOPIC, {SHORT_WORDS[0]}-{SHORT_WORDS[1]} words in total.
Rules:
- "hook": the scroll-stopper, 6-14 words, said in under 2 seconds. Lead with the most shocking TRUE fact, a
  contradiction, or high stakes. Never start with the topic name, a date alone, a question like "Did you know", or "This is".
- "open_loop": one short line that promises a twist or answer revealed only at the end.
- "segments": 5 to 7 beats. Each beat is 1-3 SHORT complete sentences (4-16 words each), present tense where natural.
  Never use "..." or fragments. Every beat adds a NEW fact or turn - escalate, never repeat.
- "payoff": the final reveal that pays off the hook and the open loop.
- "loop_line": 3-8 words, an unfinished lead-in WITHOUT a full stop that flows back into the hook sentence
  (so the Short loops). No call to action - "follow/subscribe" lines kill rewatches.
- "visual": a 2-4 word stock-footage search phrase; "caption_title": 1-3 words.
Return ONLY JSON in the same shape as the example."""
    try:
        data, provider = llm.generate_json(prompt, SYSTEM, 0.75, video_id, role="writer")
        for attempt in range(2):
            segs = _norm_segments(data.get("segments", []), topic)
            hook = clean_for_speech(str(data.get("hook", "")))
            tease = clean_for_speech(str(data.get("open_loop", ""))) or random.choice(OPEN_LOOPS)
            payoff = clean_for_speech(str(data.get("payoff", "")))
            loop = _loop_line(str(data.get("loop_line", "")))
            cta = ""
            if hook:
                if segs and segs[0]["text"].startswith(hook):
                    segs.pop(0)
                segs.insert(0, {"text": _join(hook, tease), "visual": _visual_from(hook, topic), "caption_title": ""})
            if payoff:
                segs.append({"text": payoff, "visual": _visual_from(payoff, topic), "caption_title": ""})
            segs = dedupe_segments(segs)
            total = sum(word_count(s["text"]) for s in segs)
            if total >= SHORT_WORDS[0] or attempt == 1:
                break
            logs.info("script", f"short draft only {total} words; asking for an expanded version", video_id)
            expand = (prompt + f"\n\nYOUR PREVIOUS DRAFT WAS TOO SHORT ({total} words):\n{json.dumps(data)}\n"
                      f"Rewrite it to {SHORT_WORDS[0] + 10}-{SHORT_WORDS[1]} words with 5-6 full beats and more concrete source details.")
            data, provider = llm.generate_json(expand, SYSTEM, 0.7, video_id, role="writer")
        if total < 70:
            raise ValueError(f"script too short ({total} words)")
        if hook and segs and segs[0]["text"].startswith(hook):
            better = best_hook(topic, src, hook, video_id, channel.get("best_hooks"))
            if better != hook:
                segs[0]["text"] = _join(better, segs[0]["text"][len(hook):])
                segs[0]["visual"] = _visual_from(better, topic)
                hook = better
        # loop_line is attached to the last beat AFTER fact-checking (pipeline.attach_loop_line) so a rewrite can't drop it
        return {"hook": hook or segs[0]["text"], "segments": segs, "cta": cta, "loop_line": loop,
                "provider": provider}
    except Exception as e:
        logs.warn("script", f"LLM short script failed ({e}); using grounded template writer", video_id)
        return template_script(topic, research, SHORT_WORDS[1], short=True)


LONG_MIN, LONG_MAX = 6, 12          # long-form target duration window (minutes)


def long_minutes(channel: dict) -> int:
    return max(LONG_MIN, min(LONG_MAX, int(channel.get("long_minutes") or 8)))


def write_long(topic: str, research: dict, channel: dict, video_id: int | None = None) -> dict[str, Any]:
    minutes = long_minutes(channel)
    target = _long_words(minutes)
    src = research["all_text"][:12000]
    try:
        genre_txt = style_block(channel.get("genre"))
        outline_prompt = f"""TOPIC: {topic}
TONE: {channel.get('tone')}{genre_txt}
SOURCE:
\"\"\"{src}\"\"\"
Plan a {minutes}-minute high-retention YouTube video (about {target} spoken words).
Return JSON: {{"hook": "cold open", "chapters": [{{"title": "...", "focus": "what this chapter covers, using only source facts"}}]}}
"hook" = a 3-4 sentence cold open: sentence 1 drops the viewer into the most dramatic TRUE moment of the story
(no "In this video", no "Welcome", never start with the topic name); then raise the stakes; then end with an open
loop - a specific question the video promises to answer by the end.
Use 6-8 chapters with escalating intrigue (setup, complication, twists, the big reveal, aftermath). Chapter titles
are 2-5 word curiosity phrases, not labels like "Introduction" or "Conclusion"."""
        outline, provider = llm.generate_json(outline_prompt, SYSTEM, 0.7, video_id, role="writer")
        chapters = outline.get("chapters", [])[:8]
        if len(chapters) < 4:
            raise ValueError("outline too thin")
        per = max(140, target // len(chapters))
        hook = {"text": clean_for_speech(outline.get("hook", "")), "visual": topic, "caption_title": ""}
        first = (sentences(hook["text"]) or [""])[0]
        if first:
            better = best_hook(topic, src, first, video_id, channel.get("best_hooks"), fmt="video (cold open)")
            hook["text"] = hook["text"].replace(first, better, 1)
        written: list[list[dict]] = []
        prev = ""
        for i, ch in enumerate(chapters):
            title = str(ch.get("title", ""))[:40]
            p = f"""TOPIC: {topic}
TONE: {channel.get('tone')}{genre_txt}
CHAPTER {i + 1}/{len(chapters)}: {title}
FOCUS: {ch.get('focus')}
PREVIOUS CHAPTER ENDED WITH: {prev[-300:]}
SOURCE:
\"\"\"{src}\"\"\"
Write {per}-{int(per * 1.3)} words of narration for this chapter as 5-8 short paragraphs of 2-3 punchy sentences each,
picking up straight from the previous chapter. Use concrete names, dates, places and numbers from the SOURCE.
Vary sentence length; short sentences for impact. Every paragraph adds a new fact, turn or consequence.
{"Pay off the question from the cold open with the final reveal, end on a memorable closing thought, then one short natural line inviting viewers to subscribe for the next story." if i == len(chapters) - 1 else "Do not wrap up the story. End the chapter on a cliffhanger line that teases what comes next (honest - it must be paid off later)."}
For each paragraph provide a 2-4 word stock footage search phrase.
Return JSON: {{"segments": [{{"text": "...", "visual": "..."}}]}}"""
            data, _ = llm.generate_json(p, SYSTEM, 0.75, video_id, role="writer")
            new = [dict(s, caption_title=title) for s in _norm_segments(data.get("segments", []), topic)]
            written.append(new)
            prev = " ".join(s["text"] for s in new)

        def total_words() -> int:
            return word_count(hook["text"]) + sum(word_count(s["text"]) for c in written for s in c)

        # expansion pass: small models undershoot - extend the thinnest chapters with NEW source details
        for _ in range(len(chapters)):
            if total_words() >= target * 0.9:
                break
            i = min(range(len(written)), key=lambda k: sum(word_count(s["text"]) for s in written[k]))
            title = written[i][0]["caption_title"] if written[i] else str(chapters[i].get("title", ""))[:40]
            have = " ".join(s["text"] for s in written[i])
            p = f"""TOPIC: {topic}
TONE: {channel.get('tone')}{genre_txt}
CHAPTER: {title}
WHAT THIS CHAPTER ALREADY SAYS: {have[-1500:]}
SOURCE:
\"\"\"{src}\"\"\"
Continue this chapter with {per // 2 + 40}-{per} words of NEW narration: additional facts, context or consequences from the SOURCE
that are not already covered. Never repeat what the chapter already says. 2-3 paragraphs.
Return JSON: {{"segments": [{{"text": "...", "visual": "..."}}]}}"""
            data, _ = llm.generate_json(p, SYSTEM, 0.75, video_id, role="writer")
            more = [dict(s, caption_title=title) for s in _norm_segments(data.get("segments", []), topic)]
            if not more:
                break
            written[i] = dedupe_segments(written[i] + more)
        segs = dedupe_segments([hook] + [s for c in written for s in c if s["text"]])
        total = sum(word_count(s["text"]) for s in segs)
        if total < target * 0.6:
            raise ValueError(f"long script too short ({total}/{target} words)")
        logs.info("script", f"long script {total}/{target} words ({minutes} min target) via {provider}", video_id)
        return {"hook": segs[0]["text"], "segments": segs, "cta": "",
                "chapters": [str(c.get("title", ""))[:40] for c in chapters], "provider": provider,
                "target_minutes": minutes}
    except Exception as e:
        logs.warn("script", f"LLM long script failed ({e}); using grounded template writer", video_id)
        return template_script(topic, research, target, short=False)


HOOKS = [
    "Almost nobody knows the real story of {t}. And the ending is the strangest part.",
    "The true story of {t} sounds made up. Wait until you hear how it ends.",
    "What really happened with {t}? The answer is stranger than the rumours.",
    "{t}. Most people get this story completely wrong. Here is what actually happened.",
]


def template_script(topic: str, research: dict, target_words: int, short: bool) -> dict[str, Any]:
    """No-AI fallback: an extractive, source-faithful narration built from the research text.
    Flagged so the quality gate sends it for human review instead of auto-uploading."""
    sents = [clean_for_speech(s) for s in sentences(research["all_text"]) if 6 <= word_count(s) <= 40]
    hook = random.choice(HOOKS).format(t=topic)
    segs = [{"text": hook, "visual": topic, "caption_title": ""}]
    total = word_count(hook)
    group: list[str] = []
    for s in sents:
        group.append(s)
        total += word_count(s)
        if len(group) >= (1 if short else 3):
            txt = " ".join(group)
            segs.append({"text": txt, "visual": _visual_from(txt, topic), "caption_title": ""})
            group = []
        if total >= target_words:
            break
    segs.append({"text": "Follow for more stories like this." if short else
                 "If you enjoyed this story, subscribe for more deep dives like it.", "visual": topic, "caption_title": ""})
    return {"hook": hook, "segments": segs, "cta": segs[-1]["text"], "provider": "template"}
