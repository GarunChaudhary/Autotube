"""Fact-checking agent: checks each narration segment against the research source.
Unsupported segments are rewritten (LLM) or removed. Produces a support score 0-100."""
from __future__ import annotations

from typing import Any

from .. import llm, logs
from .text_utils import clean_for_speech, content_words, copies_source

BATCH = 5

SYSTEM = ("You are a strict fact-checker. You judge ONLY against the SOURCE text provided. "
          "Opinions, transitions, hooks phrased as questions and calls to action count as supported.")


def _lexical_support(text: str, source: str) -> float:
    words = content_words(text)
    if not words:
        return 1.0
    src = content_words(source)
    return len(words & src) / len(words)


STRICT = ("You are a strict fact-checker for a single short sentence. Judge ONLY against the SOURCE. A short "
          "sentence naturally leaves most facts out - omissions are FINE and never a reason to reject. Reject it only "
          "if something it DOES say is false or not in the SOURCE: wrong who, how many, when or where (e.g. 'nine "
          "people' when the source says one), an exaggeration, or an added detail.")


def verify_claim(text: str, source: str, video_id: int | None = None) -> bool:
    """Strict check of one high-visibility line (a hook). Unverifiable (no AI) counts as not supported."""
    try:
        data, _ = llm.generate_json(
            f'SOURCE:\n"""{source[:6000]}"""\n\nSENTENCE: {text}\n\nIs everything the SENTENCE says true according to the '
            f'SOURCE (same people, numbers, places, times)? Ignore what it leaves out. Return JSON: {{"supported": true or false, '
            f'"problem": "what is wrong, or empty"}}', STRICT, 0.0, video_id, role="writer")
        ok = data.get("supported") is True
        if not ok:
            logs.info("factcheck", f"hook rejected: '{text}' ({data.get('problem', '')})", video_id)
        return ok
    except Exception as e:
        logs.warn("factcheck", f"could not verify hook ({e})", video_id)
        return False


def check(script: dict, research: dict, video_id: int | None = None) -> dict[str, Any]:
    source = research["all_text"][:9000]
    segments = script["segments"]
    results: list[dict[str, Any]] = []
    used_llm = False
    try:
        # Small local models lose track on long lists, so check in batches of 5.
        for b0 in range(0, len(segments), BATCH):
            batch = segments[b0:b0 + BATCH]
            numbered = "\n".join(f"{k}. {s['text']}" for k, s in enumerate(batch))
            prompt = f"""SOURCE:
\"\"\"{source}\"\"\"

NARRATION SEGMENTS:
{numbered}

For each segment number, decide if every factual claim in it is supported by the SOURCE.
Return JSON: {{"results": [{{"i": 0, "supported": true, "problem": "", "fixed_text": ""}}]}}
If unsupported, give "problem" and a "fixed_text" rewrite IN YOUR OWN WORDS (do not copy source sentences) that keeps
the storytelling but only uses supported facts (or empty string if the segment should be deleted)."""
            data, _ = llm.generate_json(prompt, SYSTEM, 0.1, video_id, role="writer")   # 7B: far fewer false flags
            by_i = {int(r.get("i", -1)): r for r in data.get("results", []) if str(r.get("i", "")).lstrip("-").isdigit()}
            for k, s in enumerate(batch):
                r = by_i.get(k, {"supported": True})
                supported = bool(r.get("supported", True))
                # guard against small-model false negatives: near-total source overlap means supported
                if not supported and _lexical_support(s["text"], source) >= 0.8:
                    supported = True
                results.append({"i": b0 + k, "supported": supported,
                                "problem": "" if supported else r.get("problem", ""),
                                "fixed_text": "" if supported else r.get("fixed_text", "")})
        used_llm = True
    except Exception as e:
        results = []
        logs.warn("factcheck", f"LLM fact-check unavailable ({e}); using lexical source-overlap check", video_id)

    if not used_llm:
        for i, s in enumerate(segments):
            ratio = _lexical_support(s["text"], source)
            ok = ratio >= 0.5 or i == 0 or i == len(segments) - 1   # hook / CTA are framing
            results.append({"i": i, "supported": ok, "problem": "" if ok else f"low source overlap ({ratio:.0%})",
                            "fixed_text": ""})

    fixed_segments = []
    removed, rewritten = 0, 0
    for s, r in zip(segments, results):
        if r["supported"]:
            fixed_segments.append(s)
        elif r.get("fixed_text"):
            # double-check the rewrite: must be source-supported (no hallucinated "fix") but not copied verbatim
            fixed = clean_for_speech(r["fixed_text"])
            if _lexical_support(fixed, source) >= 0.4 and not copies_source(fixed, source):
                fixed_segments.append({**s, "text": fixed})
                rewritten += 1
            else:
                removed += 1
        else:
            removed += 1
    from .script import dedupe_segments
    before = len(fixed_segments)
    fixed_segments = dedupe_segments(fixed_segments)
    removed += before - len(fixed_segments)
    supported = sum(1 for r in results if r["supported"])
    score = round(100 * supported / max(1, len(results)))
    logs.info("factcheck", f"score {score}: {supported}/{len(results)} supported, {rewritten} rewritten, {removed} removed",
              video_id)
    return {"score": score, "method": "llm" if used_llm else "lexical", "results": results,
            "rewritten": rewritten, "removed": removed,
            "script": {**script, "segments": fixed_segments}}
