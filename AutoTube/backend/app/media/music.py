"""Background music. Two licence-safe sources:

1. YOUR licensed tracks (recommended for best quality): put files in assets/music/<mood>/ where mood is one of
   mysterious, epic, uplifting, calm (or directly in assets/music/). Free legal sources: YouTube Audio Library
   (Studio > Audio Library). If a track needs attribution, put the text in a same-named .txt file.
2. Fallback: an ORIGINAL multi-layer score synthesised per video (pads, sub bass, plucked arpeggio, pulse),
   so there is never any third-party rights issue.
"""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

from .. import config

SR = 44100
MOODS = {
    # (scale intervals, progression degrees, bpm)
    "mysterious": ([0, 2, 3, 5, 7, 8, 10], [0, 5, 3, 6], 78),
    "epic": ([0, 2, 3, 5, 7, 8, 10], [0, 5, 2, 6], 92),
    "uplifting": ([0, 2, 4, 5, 7, 9, 11], [0, 4, 5, 3], 100),
    "calm": ([0, 2, 4, 5, 7, 9, 11], [0, 3, 5, 4], 70),
}


def mood_for(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("mystery", "unsolved", "dark", "crime", "ghost", "disappear", "secret", "unknown")):
        return "mysterious"
    if any(w in t for w in ("war", "battle", "empire", "disaster", "epic", "titan", "space", "volcano")):
        return "epic"
    if any(w in t for w in ("calm", "nature", "ocean", "relax", "garden")):
        return "calm"
    return "uplifting"


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _saw(f: float, t: np.ndarray, detune: float = 0.0) -> np.ndarray:
    x = (t * f * (1 + detune)) % 1.0
    return 2 * x - 1


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """Two cascaded one-pole low-passes (12 dB/oct), vectorised with scipy."""
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = lfilter([1 - a], [1, -a], x)
    return lfilter([1 - a], [1, -a], y)


def _pluck(f: float, dur: float, rng: random.Random) -> np.ndarray:
    """Karplus-Strong plucked string as an IIR filter: y[n] = x[n] + g/2 (y[n-P] + y[n-P-1])."""
    from scipy.signal import lfilter
    n = int(SR * dur)
    period = max(2, int(SR / f))
    excite = np.zeros(n)
    excite[:period] = np.array([rng.uniform(-1, 1) for _ in range(period)])
    excite[:period] = _lowpass(excite[:period], 3500)           # softer, warmer attack
    a = np.zeros(period + 2)
    a[0], a[period], a[period + 1] = 1.0, -0.4975, -0.4975
    return lfilter([1.0], a, excite)


def compose(duration: float, dest: Path, seed: str, mood: str) -> Path:
    rng = random.Random(seed)
    scale, prog, bpm = MOODS.get(mood, MOODS["uplifting"])
    root = rng.choice([45, 47, 48, 50, 52, 53])          # A2..F3
    beat = 60 / bpm
    bar = beat * 4
    total = int(SR * (duration + 2))
    t = np.arange(total) / SR
    mix = np.zeros(total)
    nbars = int(np.ceil((duration + 2) / bar))
    pad_layer = np.zeros(total)
    bass_layer = np.zeros(total)
    for b in range(nbars):
        deg = prog[b % len(prog)]
        chord = [root + 12 + scale[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4)]
        s, e = int(b * bar * SR), min(total, int((b + 1) * bar * SR))
        if s >= total:
            break
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 1.2) * np.minimum(1, (bar - tt) / 0.8 + 0.2)
        for note in chord:
            f = _midi(note)
            pad_layer[s:e] += (_saw(f, tt, 0.003) + _saw(f, tt, -0.004)) * 0.12 * env
        fb = _midi(root - 12 + scale[deg % 7])
        bass_layer[s:e] += np.sin(2 * np.pi * fb * tt) * 0.35 * np.minimum(1, tt / 0.05) * np.exp(-tt * 0.4)
    mix += _lowpass(pad_layer, 1400 if mood in ("mysterious", "calm") else 2200)
    mix += bass_layer
    # plucked arpeggio (sparser for calm/mysterious)
    step = beat / (2 if mood in ("epic", "uplifting") else 1)
    k = 0
    pos = bar  # enter after first bar
    while pos < duration:
        b = int(pos // bar)
        deg = prog[b % len(prog)]
        idx = [0, 2, 4, 2, 7, 4][k % 6]
        note = root + 24 + scale[(deg + idx) % 7] + 12 * ((deg + idx) // 7)
        if rng.random() > (0.35 if mood in ("mysterious", "calm") else 0.1):
            p = _pluck(_midi(note), min(1.5, step * 3), rng) * 0.18
            s = int(pos * SR)
            e = min(total, s + len(p))
            mix[s:e] += p[: e - s]
        pos += step
        k += 1
    # soft pulse for energy
    if mood in ("epic", "uplifting"):
        kick_t = np.arange(int(0.25 * SR)) / SR
        kick = np.sin(2 * np.pi * (50 + 60 * np.exp(-kick_t * 30)) * kick_t) * np.exp(-kick_t * 14) * 0.35
        pos = bar * 2
        while pos < duration:
            s = int(pos * SR)
            e = min(total, s + len(kick))
            mix[s:e] += kick[: e - s]
            pos += beat
    # stereo widening via short delay, fades
    mix = mix[: int(SR * duration)]
    fade_in, fade_out = int(SR * 2), int(SR * 3)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.8
    d = int(0.012 * SR)
    left, right = mix, np.concatenate([np.zeros(d), mix[:-d]])
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.stack([left, right], axis=1) * 32767).astype(np.int16).tobytes())
    return dest


def pick_music(duration: float, work: Path, seed: str, mood: str) -> tuple[Path, str | None, str]:
    """Returns (path, attribution or None, source) - user library first, then original score."""
    exts = (".mp3", ".wav", ".m4a", ".ogg")
    pools = [config.MUSIC_DIR / mood, config.MUSIC_DIR]
    for pool in pools:
        if pool.exists():
            tracks = [p for p in pool.iterdir() if p.suffix.lower() in exts]
            if tracks:
                t = random.Random(seed).choice(tracks)
                credit_file = t.with_suffix(".txt")
                credit = credit_file.read_text(encoding="utf-8").strip() if credit_file.exists() else None
                return t, credit, "library"
    return compose(duration, work / "music.wav", seed, mood), None, "original"
