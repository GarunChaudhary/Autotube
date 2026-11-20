"""Professional editing engine.

Per shot:
  * stills  -> graded frame (blurred-backdrop framing when aspect differs) + sub-pixel smooth Ken Burns motion
               rendered with Pillow affine transforms and piped to FFmpeg (no zoompan jitter)
  * video   -> cover-crop, frame-rate normalised, graded
Assembly:
  * frame-accurate xfade transitions (crossfade / smooth slides / dip-to-black at chapters), timed so every
    shot change lands exactly on its narration beat
Finishing:
  * colour grade + vignette, Shorts progress bar, ASS overlays (captions, hook/title card, chapter cards,
    on-screen photo credits), fades
Audio:
  * narration + ducked music + sound-design bed (whooshes on transitions, impact on hook/chapters),
    loudness normalised to -14 LUFS / -1.5 dBTP
"""
from __future__ import annotations

import math
import random
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .. import config, logs
from . import ffmpeg, sfx

FPS = 30
XF = 0.35              # standard transition length (s)
XF_CHAPTER = 0.6


def dims(portrait: bool) -> tuple[int, int]:
    return (1080, 1920) if portrait else (1920, 1080)


# ------------------------------------------------------------------ stills
def _graded_canvas(path: str, W: int, H: int, scale: float = 1.22) -> Image.Image:
    """Return an oversize (scale x) graded canvas. Mismatched aspect -> sharp image over blurred backdrop."""
    cw, ch = int(W * scale), int(H * scale)
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.08)).enhance(1.06)
    src_ar, dst_ar = im.width / im.height, cw / ch
    if abs(math.log(src_ar / dst_ar)) < 0.28:                   # close enough: fill the frame
        return ImageOps.fit(im, (cw, ch), Image.LANCZOS)
    bg = ImageOps.fit(im, (cw // 4, ch // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(14))
    bg = ImageEnhance.Brightness(bg.resize((cw, ch), Image.BICUBIC)).enhance(0.45)
    fg = ImageOps.contain(im, (int(cw * 0.94), int(ch * 0.80)), Image.LANCZOS)
    shadow = Image.new("RGBA", (fg.width + 60, fg.height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([30, 30, fg.width + 30, fg.height + 30], fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    x, y = (cw - fg.width) // 2, (ch - fg.height) // 2
    bg.paste(shadow, (x - 30, y - 22), shadow)
    bg.paste(fg, (x, y))
    return bg


PUNCH = 0.09          # punch-in zoom at the start of each new beat (retention "pattern interrupt")


def _kenburns(i: int, asset: dict, D: float, W: int, H: int, out: Path, rnd: random.Random,
              punch: bool = False, energetic: bool = False, take: int = 0) -> None:
    canvas = _graded_canvas(asset["path"], W, H)
    cw, ch = canvas.size
    frames = max(1, int(round(D * FPS)))
    # move: zoom in or out (Shorts move harder), drift; a re-used asset (take>0) gets the opposite zoom + mirrored drift
    lo, hi = (0.10, 0.18) if energetic else (0.06, 0.12)
    zoom_in = (rnd.random() < 0.6) != bool(take % 2)
    amt = rnd.uniform(lo, hi)
    z0, z1 = (1.0, 1.0 + amt) if zoom_in else (1.0 + amt, 1.0)
    base = min(cw / W, ch / H)                       # visible window at zoom 1 covers W*base x H*base
    dx, dy = rnd.uniform(-0.05, 0.05), rnd.uniform(-0.035, 0.035)
    if take % 2:
        dx, dy = -dx, -dy
    cmd = [config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ffmpeg.CREATE_NO_WINDOW)
    try:
        for f in range(frames):
            u = f / max(1, frames - 1)
            e = u * u * (3 - 2 * u)                       # ease in-out
            z = z0 + (z1 - z0) * e
            if punch:                                     # fast ease-out from a tighter frame over ~0.25 s
                z *= 1.0 + PUNCH * math.exp(-f / (0.08 * FPS))
            vw, vh = W * base / z, H * base / z           # source window size
            cx = cw / 2 + dx * cw * (e - 0.5)
            cy = ch / 2 + dy * ch * (e - 0.5)
            # keep the window inside the canvas - never reveal a black edge
            cx = min(max(cx, vw / 2), cw - vw / 2)
            cy = min(max(cy, vh / 2), ch - vh / 2)
            x0, y0 = cx - vw / 2, cy - vh / 2
            sx, sy = vw / W, vh / H
            frame = canvas.transform((W, H), Image.AFFINE, (sx, 0, x0, 0, sy, y0), resample=Image.BILINEAR)
            p.stdin.write(frame.tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise ffmpeg.FFmpegError(p.stderr.read().decode(errors="replace")[-800:])
    finally:
        if p.poll() is None:
            p.kill()


def _video_clip(asset: dict, D: float, W: int, H: int, out: Path, rnd: random.Random | None = None) -> None:
    """Real footage. Long archive films are entered at a random point past the opening title cards, so each shot
    shows a different moment. Footage whose shape differs a lot from the frame (e.g. 4:3 newsreel in a 9:16 Short)
    is shown whole over a blurred fill of itself instead of being cropped to a sliver."""
    try:
        info = ffmpeg.probe(asset["path"])
        vs = next(s for s in info["streams"] if s["codec_type"] == "video")
        sw, sh, L = int(vs["width"]), int(vs["height"]), float(info["format"].get("duration") or 0)
    except Exception:
        sw, sh, L = W, H, 0.0
    seek = []
    if L > D + 6:
        lo, hi = min(L * 0.08, 12.0), L * 0.92 - D
        if hi > lo:
            seek = ["-ss", f"{(rnd or random).uniform(lo, hi):.2f}"]
    grade = f"fps={FPS},setsar=1,eq=contrast=1.05:saturation=1.08"
    mismatch = abs((sw / max(1, sh)) / (W / H) - 1) > 0.3
    if mismatch:
        fc = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=24:2,eq=brightness=-0.10[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{grade}[v]")
        vargs = ["-filter_complex", fc, "-map", "[v]"]
    else:
        vargs = ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{grade}"]
    ffmpeg.run([*seek, "-stream_loop", "-1", "-i", asset["path"], *vargs, "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", f"{D:.3f}", str(out)])


# ------------------------------------------------------------------ transitions & overlays
SOFT = ["fade", "fade", "fade", "smoothleft", "smoothright", "smoothup", "circlecrop", "zoomin"]
PUNCHY = ["zoomin", "slideleft", "slideup", "smoothleft", "smoothup", "wipeleft"]   # Shorts beat changes
CUT = 0.07            # near-hard cut (2 frames) inside a beat - keeps the pace without jarring flashes
XF_BEAT_SHORT = 0.18
XF_BEAT_LONG = 0.28
XF_CUT_LONG = 0.12


def plan_transitions(shots: list[dict], portrait: bool, seed: str) -> list[dict]:
    """Transition INTO each shot (index>=1).

    Shorts: hard-feeling cuts inside a beat, quick energetic moves on beat changes.
    Long:   quick cuts inside a beat, short smooth moves on beat changes, dip-to-black at chapters."""
    rnd = random.Random(seed)
    out = []
    for i, s in enumerate(shots):
        if i == 0:
            out.append({"type": None, "d": 0})
        elif s.get("chapter_start"):
            out.append({"type": "fadeblack", "d": XF_CHAPTER})
        elif s.get("segment_start"):
            out.append({"type": rnd.choice(PUNCHY), "d": XF_BEAT_SHORT} if portrait
                       else {"type": rnd.choice(SOFT[3:6]), "d": XF_BEAT_LONG})
        else:
            out.append({"type": "fade", "d": CUT if portrait else XF_CUT_LONG})
    return out


def _sfx_bed(duration: float, events: list[tuple[float, str, float]], path: Path, seed: str) -> Path:
    lib = sfx.library()
    rnd = random.Random(seed)
    sr = sfx.SR
    bed = np.zeros((int(sr * (duration + 2)), 2), dtype=np.float32)
    for t, kind, gain in events:
        src = rnd.choice(lib[kind])
        with wave.open(str(src), "rb") as wf:
            x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768
        s = max(0, int(t * sr))
        e = min(len(bed), s + len(x))
        bed[s:e] += x[: e - s] * gain
    bed = bed[: int(sr * duration)]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.clip(bed, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _copy_fonts(work: Path) -> None:
    fdir = work / "fonts"
    fdir.mkdir(exist_ok=True)
    names = ("ariblk.ttf", "arial.ttf", "arialbd.ttf", "impact.ttf", "segoeuib.ttf",
             "Arial_Black.ttf", "Arial.ttf", "Arial_Bold.ttf", "Impact.ttf")      # Windows / msttcorefonts names
    for d in config.SYSTEM_FONT_DIRS:
        for name in names:
            src = d / name
            if src.exists() and not (fdir / name).exists():
                shutil.copy(src, fdir / name)
    for f in (config.ASSETS_DIR / "fonts").glob("*.ttf"):
        shutil.copy(f, fdir / f.name)


# ------------------------------------------------------------------ main entry
def render(shots: list[dict], narration: Path, music: Path, ass_path: Path | None, out_path: Path,
           portrait: bool, duration: float, work: Path, video_id: int | None = None,
           seed: str = "x", sfx_events: list[tuple[float, str, float]] | None = None) -> Path:
    W, H = dims(portrait)
    trans = plan_transitions(shots, portrait, seed)
    shot_dir = work / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, s in enumerate(shots):
        tail = trans[i + 1]["d"] if i + 1 < len(shots) else 0.0
        D = max(0.6, s["end"] - s["start"] + tail)
        jobs.append((i, s, D))
    logs.info("render", f"rendering {len(shots)} shots at {W}x{H}, {duration:.1f}s", video_id)

    def work_one(job):
        i, s, D = job
        out = shot_dir / f"shot_{i:04d}.mp4"
        rnd = random.Random(f"{seed}-{i}")
        if s["asset"]["type"] == "video":
            _video_clip(s["asset"], D, W, H, out, rnd)
        else:
            _kenburns(i, s["asset"], D, W, H, out, rnd, punch=bool(s.get("segment_start")) and i > 0,
                      energetic=portrait, take=int(s.get("take", 0)))
        return out

    with ThreadPoolExecutor(max(2, min(6, (__import__("os").cpu_count() or 4) // 3))) as ex:
        clips = list(ex.map(work_one, jobs))

    # ---- assemble with xfade, in groups to keep the filter graph small
    joined = _assemble(clips, shots, trans, work, W, H)

    # ---- finishing pass
    vf = ["eq=contrast=1.03:saturation=1.05", "vignette=angle=PI/5:mode=forward"]
    if ass_path:
        _copy_fonts(work)
        vf.append(f"subtitles={ass_path.name}:fontsdir=fonts")
    if not portrait:        # Shorts: no fade in/out - frame 1 must hook instantly and the end loops into the start
        vf += ["fade=t=in:st=0:d=0.25", f"fade=t=out:st={max(0, duration - 0.8):.2f}:d=0.8"]
    filters = f"[0:v]{','.join(vf)}[vg]"
    inputs = ["-i", joined.name, "-i", str(narration), "-stream_loop", "-1", "-i", str(music)]
    if portrait:   # thin progress bar (retention cue on Shorts)
        filters += (f";color=c=0xFFD400@0.95:s={W}x10:r={FPS}[bar];"
                    f"[vg][bar]overlay=x='-W+W*t/{duration:.3f}':y=H-10:shortest=1,"
                    f"scale=out_range=tv,format=yuv420p,setsar=1[v]")
    else:
        filters += ";[vg]scale=out_range=tv,format=yuv420p,setsar=1[v]"
    sfx_path = _sfx_bed(duration, sfx_events or [], work / "sfx.wav", seed)
    inputs += ["-i", str(sfx_path)]
    filters += (
        ";[1:a]aresample=48000,highpass=f=70,acompressor=threshold=0.12:ratio=3:attack=8:release=120,"
        "equalizer=f=3200:t=q:w=1.2:g=2,asplit=2[v1][v2]"
        ";[2:a]aresample=48000,volume=0.28[m]"
        ";[m][v1]sidechaincompress=threshold=0.025:ratio=9:attack=20:release=500[md]"
        ";[3:a]aresample=48000,volume=0.55[fx]"
        ";[v2][md][fx]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    )
    tmp = out_path.with_suffix(".tmp.mp4")
    ffmpeg.run([*inputs, "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{duration:.3f}",
                "-movflags", "+faststart", str(tmp)], cwd=work)
    tmp.replace(out_path)
    return out_path


# ------------------------------------------------------------------ the two separate products
def render_short(shots, narration, music, ass_path, work, duration, video_id=None, seed="x", sfx_events=None) -> Path:
    """YouTube Short: 1080x1920 9:16 portrait - fast cuts, energetic transitions, big centred captions,
    hook headline card, progress bar. Output validated against formats.SHORT."""
    from . import formats
    out = render(shots, narration, music, ass_path, work / "short.mp4", True, duration, work, video_id, seed, sfx_events)
    _require(formats.validate(out, "short"))
    return out


def render_long(shots, narration, music, ass_path, work, duration, video_id=None, seed="x", sfx_events=None) -> Path:
    """Long-form: 1920x1080 16:9 landscape - documentary pacing, soft transitions, lower captions, title and
    chapter cards, no Shorts elements. Output validated against formats.LONG."""
    from . import formats
    out = render(shots, narration, music, ass_path, work / "video.mp4", False, duration, work, video_id, seed, sfx_events)
    _require(formats.validate(out, "long"))
    return out


def _require(result: dict) -> None:
    if not result["ok"]:
        raise ffmpeg.FFmpegError(f"rendered {result['kind']} failed format validation: {result['errors']}")


def _assemble(clips: list[Path], shots: list[dict], trans: list[dict], work: Path, W: int, H: int) -> Path:
    """Chain xfades. offset for shot k = its nominal start, so picture changes stay on the narration beat."""
    GROUP = 24
    starts = [s["start"] for s in shots]
    pieces: list[tuple[Path, float]] = []     # (file, nominal start of its first shot)
    for g0 in range(0, len(clips), GROUP):
        g = list(range(g0, min(len(clips), g0 + GROUP)))
        out = work / f"part_{g0:04d}.mp4"
        if len(g) == 1:
            shutil.copy(clips[g[0]], out)
        else:
            inputs, chain, last = [], [], "[0:v]"
            for k, idx in enumerate(g):
                inputs += ["-i", str(clips[idx])]
            for k in range(1, len(g)):
                idx = g[k]
                t = trans[idx]
                off = starts[idx] - starts[g[0]]
                lbl = f"[x{k}]"
                chain.append(f"{last}[{k}:v]xfade=transition={t['type'] or 'fade'}:duration={t['d']:.3f}:offset={off:.3f}{lbl}")
                last = lbl
            ffmpeg.run([*inputs, "-filter_complex", ";".join(chain), "-map", last, "-c:v", "libx264", "-preset",
                        "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
        pieces.append((out, starts[g[0]]))
    if len(pieces) == 1:
        return pieces[0][0]
    # join groups with the transition planned into the first shot of each group
    inputs, chain, last = [], [], "[0:v]"
    for k, (p, _) in enumerate(pieces):
        inputs += ["-i", str(p)]
    for k in range(1, len(pieces)):
        idx = k * GROUP
        t = trans[idx]
        chain.append(f"{last}[{k}:v]xfade=transition={t['type'] or 'fade'}:duration={t['d']:.3f}:offset={pieces[k][1]:.3f}[j{k}]")
        last = f"[j{k}]"
    out = work / "video_only.mp4"
    ffmpeg.run([*inputs, "-filter_complex", ";".join(chain), "-map", last, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])
    return out


def verify(path: Path, portrait: bool, min_dur: float, max_dur: float) -> dict:
    """Independent check of the rendered file with ffprobe + audio level measurement."""
    info = ffmpeg.probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    W, H = dims(portrait)
    problems = []
    if not v:
        problems.append("no video stream")
    elif (int(v["width"]), int(v["height"])) != (W, H):
        problems.append(f"wrong size {v['width']}x{v['height']}")
    if not a:
        problems.append("no audio stream")
    if not (min_dur <= dur <= max_dur):
        problems.append(f"duration {dur:.1f}s outside {min_dur}-{max_dur}s")
    loud = None
    try:
        p = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True, creationflags=ffmpeg.CREATE_NO_WINDOW, timeout=600,
                           encoding="utf-8", errors="replace")
        import re as _re
        m = _re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
        loud = float(m[-1]) if m else None
        if loud is not None and not (-17.5 <= loud <= -11.5):
            problems.append(f"loudness {loud} LUFS outside -17.5..-11.5")
    except Exception:
        pass
    return {"ok": not problems, "problems": problems, "duration": dur, "loudness_lufs": loud,
            "size_mb": round(path.stat().st_size / 1e6, 1), "resolution": f"{v['width']}x{v['height']}" if v else None}
