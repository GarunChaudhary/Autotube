"""Thumbnail generator (Pillow). 1280x720 JPEG < 2 MB as required by YouTube."""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .. import config
from . import ffmpeg

ACCENTS = [(255, 214, 0), (0, 229, 255), (255, 82, 82), (124, 255, 107), (255, 145, 0)]


def _font(size: int):
    try:
        return ImageFont.truetype(config.FONT_HEAVY or config.FONT_BOLD, size)
    except Exception:
        return ImageFont.load_default()


def frame_from_asset(asset: dict, dest: Path) -> Path:
    if asset["type"] == "image":
        return Path(asset["path"])
    ffmpeg.run(["-ss", "1.5", "-i", asset["path"], "-frames:v", "1", "-q:v", "2", str(dest)])
    return dest


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_thumbnail(bg_path: Path, headline: str, dest: Path, seed: str, size=(1280, 720)) -> Path:
    rnd = random.Random(seed)
    accent = rnd.choice(ACCENTS)
    W, H = size
    bg = Image.open(bg_path).convert("RGB")
    bg = ImageOps.fit(bg, (W, H), Image.LANCZOS)
    bg = ImageEnhance.Contrast(ImageEnhance.Color(bg).enhance(1.35)).enhance(1.15)
    # dark gradient on the text side
    left = rnd.random() < 0.5
    grad = Image.linear_gradient("L").rotate(90 if left else -90).resize((W, H))
    shade = Image.new("RGB", (W, H), (0, 0, 0))
    bg = Image.composite(shade, bg, grad.point(lambda v: int(v * 0.85)))
    d = ImageDraw.Draw(bg)
    words = headline.upper().split()[:6]
    text = " ".join(words)
    fs = 150
    while fs > 60:
        f = _font(fs)
        lines = _wrap(d, text, f, int(W * 0.58))
        if len(lines) <= 3:
            break
        fs -= 10
    f = _font(fs)
    lines = _wrap(d, text, f, int(W * 0.58))[:3]
    total_h = len(lines) * fs * 1.08
    y = (H - total_h) / 2
    x0 = 60 if left else W - 60
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        x = x0 if left else x0 - tw
        fill = accent if i == len(lines) - 1 else (255, 255, 255)
        # drop shadow
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((x + 8, y + 8), ln, font=f, fill=(0, 0, 0, 200))
        bg.paste(shadow.filter(ImageFilter.GaussianBlur(6)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(6)))
        d.text((x, y), ln, font=f, fill=fill, stroke_width=max(3, fs // 22), stroke_fill=(0, 0, 0))
        y += fs * 1.08
    # accent bar
    bx = 60 if left else W - 60 - 220
    d.rectangle([bx, H - 70, bx + 220, H - 52], fill=accent)
    dest.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    bg.save(dest, "JPEG", quality=q)
    while dest.stat().st_size > 1_900_000 and q > 50:
        q -= 10
        bg.save(dest, "JPEG", quality=q)
    return dest
