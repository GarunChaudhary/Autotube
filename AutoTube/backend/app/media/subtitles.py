"""Subtitle generator: timed caption chunks -> .srt (for upload/archival) and styled .ass (burned in)."""
from __future__ import annotations

import re
from pathlib import Path


def chunk_captions(timeline: dict, max_words: int) -> list[dict]:
    """Split each sentence into caption chunks, timing proportional to character length."""
    caps = []
    for seg in timeline["segments"]:
        for s in seg["sentences"]:
            words = s["text"].split()
            if not words:
                continue
            groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
            # merge a dangling single word into the previous group
            if len(groups) > 1 and len(groups[-1]) == 1:
                last = groups.pop()
                groups[-1] = groups[-1] + last
            total_chars = sum(len(" ".join(g)) for g in groups)
            t = s["start"]
            span = s["end"] - s["start"]
            for g in groups:
                txt = " ".join(g)
                d = span * len(txt) / max(1, total_chars)
                # per-word timing (proportional to word length) for the active-word highlight
                wchars = [len(w) + 1 for w in g]
                words, wt = [], t
                for w, n in zip(g, wchars):
                    wd = d * n / sum(wchars)
                    words.append({"w": w, "start": wt, "end": wt + wd})
                    wt += wd
                caps.append({"text": txt, "start": t, "end": t + d, "words": words})
                t += d
    # hold each caption over short pauses so text doesn't flicker between sentences
    for a, b in zip(caps, caps[1:]):
        if 0 < b["start"] - a["end"] < 0.6:
            a["end"] = b["start"]
    return caps


def _ts_srt(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int(round((s - int(s)) * 1000)):03}"


def _ts_ass(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02}:{s:05.2f}"


def write_srt(caps: list[dict], path: Path) -> Path:
    lines = []
    for i, c in enumerate(caps, 1):
        lines += [str(i), f"{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


CHAPTER_CARD = 1.8    # seconds a chapter card holds (long holds lose viewers)

ACCENTS = ["&H00D4FF&", "&H00E5FF&", "&H5BE0FF&", "&HFFD24A&", "&H7CFF6B&"]   # ASS colours are &HBBGGRR&


def _bgr(ass_colour: str) -> str:
    return ass_colour


def write_ass(caps: list[dict], path: Path, portrait: bool, overlays: dict | None = None,
              accent: str = "&H00E5FF&", font: str = "Arial Black") -> Path:
    """Captions + editorial overlays.

    overlays = {
      "hook_title": str,                     # Shorts: headline card in the top third for the first beat
      "hook_end": float,
      "title": str,                          # long: opening title card
      "chapters": [(start_s, "Title", n)],   # long: chapter cards
      "credits": [(start_s, end_s, "Photo: ...")],
      "duration": float,
    }
    """
    ov = overlays or {}
    if portrait:
        W, H = 1080, 1920
        cap_style = f"Style: Cap,{font},90,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1"
        pos = r"{\an5\pos(540,1240)\fscx84\fscy84\t(0,90,\fscx100\fscy100)}"
    else:
        W, H = 1920, 1080
        cap_style = "Style: Cap,Arial,56,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,4,2,2,160,160,64,1"
        pos = ""
    styles = [
        cap_style,
        f"Style: Hook,{font},{84 if portrait else 96},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,4,5,70,70,0,1",
        f"Style: Kicker,Arial,{40 if portrait else 38},{accent},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,6,0,1,3,0,5,70,70,0,1",
        "Style: Credit,Arial,22,&H00FFFFFF,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,1,28,28,22,1",
        "Style: Box,Arial,20,&H00000000,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
    ]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{chr(10).join(styles)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    # --- captions (suppressed under full-screen title / chapter cards on long videos)
    blocked = []
    if not portrait and ov.get("title"):
        blocked.append((0, 3.6))
    for t0, _, _ in ov.get("chapters", []):
        blocked.append((t0, t0 + CHAPTER_CARD))
    # active word: accent colour + slight scale-up, moving word by word with the narration
    on = r"{\c" + accent + (r"\fscx112\fscy112}" if portrait else "}")
    off = r"{\c&HFFFFFF&" + (r"\fscx100\fscy100}" if portrait else "}")
    static_pos = r"{\an5\pos(540,1240)}" if portrait else ""
    for c in caps:
        if any(a <= c["start"] < b for a, b in blocked):
            continue
        words = [_esc(w["w"]) for w in c.get("words", [])] or _esc(c["text"]).split()
        timing = c.get("words") or [{"start": c["start"], "end": c["end"]}] * len(words)
        if portrait:
            words = [w.upper() for w in words]
        if not words:
            continue
        for j, wt in enumerate(timing):
            s0 = c["start"] if j == 0 else wt["start"]
            s1 = c["end"] if j == len(timing) - 1 else timing[j + 1]["start"]
            if s1 - s0 < 0.02:
                continue
            body = " ".join(on + w + off if k == j else w for k, w in enumerate(words))
            lead = pos if j == 0 else static_pos        # Shorts: pop-in only when the chunk appears
            events.append(f"Dialogue: 1,{_ts_ass(s0)},{_ts_ass(s1)},Cap,,0,0,0,,{lead}{body}")

    def wrap(txt: str, n: int) -> str:
        words, lines, cur = txt.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > n and cur:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
        return r"\N".join(lines)

    # --- hook headline card: on screen from frame 1, pops in, holds through the hook beat
    #     (Shorts: top third; long-form: upper area, replacing the old slow full-screen title card)
    if ov.get("hook_title"):
        end = max(2.2, min(ov.get("hook_end", 3.0), 4.5))
        txt = wrap(_esc(ov["hook_title"]).upper(), 16 if portrait else 26)
        xy = "540,470" if portrait else "960,250"
        events.append(f"Dialogue: 2,{_ts_ass(0.0)},{_ts_ass(end)},Hook,,0,0,0,,"
                      r"{\an5\pos(" + xy + r")\fscx70\fscy70\t(0,160,\fscx104\fscy104)\t(160,260,\fscx100\fscy100)\fad(0,220)}" + txt)
    # --- long-form opening title card
    if not portrait and ov.get("title"):
        events.append(r"Dialogue: 0,0:00:00.00,0:00:03.60,Box,,0,0,0,,{\an7\pos(0,0)\1c&H000000&\1a&H60&\fad(0,400)\p1}m 0 0 l 1920 0 l 1920 1080 l 0 1080{\p0}")
        events.append(f"Dialogue: 2,0:00:00.20,0:00:03.50,Hook,,0,0,0,,"
                      r"{\an5\pos(960,560)\fad(300,350)\blur0.6}" + wrap(_esc(ov["title"]).upper(), 28))
    # --- chapter cards
    for t0, title, n in ov.get("chapters", []):
        a, b = _ts_ass(t0 + 0.1), _ts_ass(t0 + CHAPTER_CARD)
        events.append(f"Dialogue: 0,{a},{b},Box,,0,0,0,,"
                      r"{\an7\pos(0,0)\1c&H000000&\1a&H70&\fad(250,300)\p1}m 0 0 l 1920 0 l 1920 1080 l 0 1080{\p0}")
        events.append(f"Dialogue: 2,{a},{b},Kicker,,0,0,0,,{{\\an5\\pos(960,470)\\fad(250,300)}}CHAPTER {n}")
        events.append(f"Dialogue: 2,{a},{b},Hook,,0,0,0,,"
                      r"{\an5\pos(960,560)\fad(250,300)\fscx92\fscy92\t(0,400,\fscx100\fscy100)}" + wrap(_esc(title).upper(), 30))
    # --- silent follow / subscribe prompt (pill with accent outline)
    if ov.get("subscribe"):
        t0, t1, text = ov["subscribe"]
        xy = "540,300" if portrait else "1600,960"
        size = 46 if portrait else 34
        events.append(f"Dialogue: 4,{_ts_ass(t0)},{_ts_ass(t1)},Kicker,,0,0,0,,"
                      r"{\an5\pos(" + xy + r")\fs" + str(size) + r"\bord3\3c&H000000&\1c&HFFFFFF&\fad(200,150)"
                      r"\fscx80\fscy80\t(0,180,\fscx100\fscy100)}" + _esc(text))
    # --- on-screen attribution for CC images (bottom-left, small)
    for t0, t1, text in ov.get("credits", []):
        y = H - (40 if portrait else 18)
        events.append(f"Dialogue: 3,{_ts_ass(t0)},{_ts_ass(t1)},Credit,,0,0,0,,{{\\an1\\pos(28,{y})\\alpha&H30&}}{_esc(text)[:110]}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path
