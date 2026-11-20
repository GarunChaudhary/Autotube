"""Original sound-design elements synthesised with numpy (no third-party samples, no licensing).

whoosh  - filtered-noise swoosh for transitions
impact  - low cinematic hit for the hook / chapter cards
riser   - short tension build into the hook payoff
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .. import config

SR = 48000
SFX_DIR = config.CACHE_DIR / "sfx"


def _write(x: np.ndarray, path: Path) -> Path:
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.9
    stereo = np.stack([x, x], axis=1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((stereo * 32767).astype(np.int16).tobytes())
    return path


def _sweep_lowpass(noise: np.ndarray, cutoff: np.ndarray) -> np.ndarray:
    """Time-varying one-pole low-pass (cutoff in Hz per sample)."""
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(noise)
    acc = 0.0
    for i in range(len(noise)):
        acc = (1 - a[i]) * noise[i] + a[i] * acc
        y[i] = acc
    return y


def whoosh(seed: int, dur: float = 0.55) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    peak = 0.55 + 0.1 * rng.random()
    cutoff = 300 + 5200 * np.exp(-((t - peak) ** 2) / 0.04)
    y = _sweep_lowpass(noise, cutoff)
    y -= _sweep_lowpass(y, np.full(n, 180.0))           # remove rumble (crude high-pass)
    env = np.exp(-((t - peak) ** 2) / 0.05)
    return y * env


def impact(seed: int, dur: float = 1.6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 55 * np.exp(-t * 1.2) + 32
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 3.2)
    click = rng.standard_normal(n) * np.exp(-t * 60) * 0.5
    tail = _sweep_lowpass(rng.standard_normal(n), np.full(n, 900.0)) * np.exp(-t * 2.5) * 0.8
    return body + click + tail


def riser(seed: int, dur: float = 1.8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.linspace(0, 1, n)
    y = _sweep_lowpass(rng.standard_normal(n), 200 + 7000 * t ** 2)
    tone = np.sin(2 * np.pi * np.cumsum(200 + 600 * t ** 2) / SR) * 0.25
    return (y + tone) * (t ** 2.2)


def tick(seed: int, dur: float = 0.12) -> np.ndarray:
    """Soft, short cut accent (muted click + tiny tonal body) - marks fast cuts without being noticed as a 'sound'."""
    rng = np.random.default_rng(seed)
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 900 + 250 * rng.random()
    body = np.sin(2 * np.pi * f * t * (1 - 0.35 * t / dur)) * np.exp(-t * 45)
    click = _sweep_lowpass(rng.standard_normal(n), np.full(n, 3500.0)) * np.exp(-t * 160) * 0.6
    return body * 0.5 + click


def library() -> dict[str, list[Path]]:
    """Create (once) a small varied library: 4 whooshes, 2 impacts, 2 risers, 3 ticks."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    lib: dict[str, list[Path]] = {"whoosh": [], "impact": [], "riser": [], "tick": []}
    for kind, fn, count in (("whoosh", whoosh, 4), ("impact", impact, 2), ("riser", riser, 2), ("tick", tick, 3)):
        for i in range(count):
            p = SFX_DIR / f"{kind}_{i}.wav"
            if not p.exists():
                _write(fn(1000 + i), p)
            lib[kind].append(p)
    return lib
