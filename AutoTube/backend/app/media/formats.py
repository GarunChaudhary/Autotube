"""Output format specifications + ffprobe validation for the two separate products.

SHORT : 1080x1920 (9:16 portrait), H.264 yuv420p 30 fps, AAC 48 kHz, MP4 + faststart, 30-60 s target, <=180 s hard max
LONG  : 1920x1080 (16:9 landscape), H.264 yuv420p 30 fps, AAC 48 kHz, MP4 + faststart, 6-12 min target

YouTube itself classifies Shorts (vertical/square, <=3 min); we make sure what we call a Short qualifies and
that a long video can never be a vertical render. Every rendered file is validated, and validated again
immediately before upload.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import ffmpeg


@dataclass(frozen=True)
class Spec:
    kind: str
    width: int
    height: int
    target_min: float        # seconds - outside this window = quality warning (blocker for review)
    target_max: float
    hard_min: float          # outside the hard window = invalid, never uploaded
    hard_max: float
    fps: int = 30


SHORT = Spec("short", 1080, 1920, 30, 60, 10, 180)
LONG = Spec("long", 1920, 1080, 6 * 60, 12 * 60, 3 * 60, 60 * 60)
SPECS = {"short": SHORT, "long": LONG}


def spec(kind: str) -> Spec:
    return SPECS[kind]


def _moov_before_mdat(path: Path) -> bool:
    """faststart check: the 'moov' atom must come before 'mdat' (streamable / quick processing)."""
    with open(path, "rb") as f:
        pos = 0
        size = path.stat().st_size
        while pos < size:
            f.seek(pos)
            hdr = f.read(16)
            if len(hdr) < 8:
                return False
            box = int.from_bytes(hdr[:4], "big")
            typ = hdr[4:8]
            if typ == b"moov":
                return True
            if typ == b"mdat":
                return False
            if box == 1:
                box = int.from_bytes(hdr[8:16], "big")
            if box < 8:
                return False
            pos += box
    return False


def validate(path: Path | str, kind: str) -> dict:
    """Validate a rendered file against its spec. Returns {ok, errors, warnings, info}."""
    s = spec(kind)
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {"ok": False, "errors": ["file missing"], "warnings": [], "info": {}}
    info = ffmpeg.probe(path)
    fmt = info.get("format", {})
    v = next((x for x in info["streams"] if x["codec_type"] == "video"), None)
    a = next((x for x in info["streams"] if x["codec_type"] == "audio"), None)
    dur = float(fmt.get("duration") or 0)
    out = {"duration": round(dur, 2), "container": fmt.get("format_name")}
    if "mp4" not in (fmt.get("format_name") or ""):
        errors.append(f"container {fmt.get('format_name')} is not MP4")
    if not v:
        errors.append("no video stream")
    else:
        w, h = int(v["width"]), int(v["height"])
        fps = float(Fraction(v.get("avg_frame_rate") or "0/1")) if v.get("avg_frame_rate") not in (None, "0/0") else 0
        out.update(width=w, height=h, codec=v.get("codec_name"), pix_fmt=v.get("pix_fmt"), fps=round(fps, 2),
                   sar=v.get("sample_aspect_ratio", "1:1"))
        if (w, h) != (s.width, s.height):
            errors.append(f"resolution {w}x{h}, expected {s.width}x{s.height}")
        if kind == "short" and not h > w:
            errors.append("a Short must be portrait (height > width)")
        if kind == "long" and not w > h:
            errors.append("a long video must be landscape (width > height) - vertical Shorts-style output rejected")
        if v.get("codec_name") != "h264":
            errors.append(f"video codec {v.get('codec_name')}, expected h264")
        if v.get("pix_fmt") != "yuv420p":
            errors.append(f"pixel format {v.get('pix_fmt')}, expected yuv420p")
        if abs(fps - s.fps) > 0.5:
            errors.append(f"frame rate {fps:.2f}, expected {s.fps}")
        if v.get("sample_aspect_ratio") not in (None, "1:1", "0:1"):
            errors.append(f"non-square pixels (SAR {v.get('sample_aspect_ratio')})")
    if not a:
        errors.append("no audio stream")
    else:
        out.update(audio_codec=a.get("codec_name"), sample_rate=int(a.get("sample_rate", 0)))
        if a.get("codec_name") != "aac":
            errors.append(f"audio codec {a.get('codec_name')}, expected aac")
    if not (s.hard_min <= dur <= s.hard_max):
        errors.append(f"duration {dur:.1f}s outside allowed {s.hard_min:.0f}-{s.hard_max:.0f}s for a {kind}")
    elif not (s.target_min <= dur <= s.target_max):
        warnings.append(f"duration {dur:.1f}s outside the {s.target_min:.0f}-{s.target_max:.0f}s target for a {kind}")
    try:
        faststart = _moov_before_mdat(path)
        out["faststart"] = faststart
        if not faststart:
            errors.append("MP4 is not faststart (moov atom after mdat)")
    except OSError as e:
        errors.append(f"could not read file: {e}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": out, "kind": kind}
