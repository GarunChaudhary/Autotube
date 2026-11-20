"""Voiceover generator.

Engines (automatic fallback): Kokoro-82M (Apache-2.0, natural neural voice, local CPU)
-> Piper (local neural) -> Windows SAPI.

Synthesises sentence-by-sentence so we know exact timings for captions, scenes and sound design.
Returns narration.wav + a timeline:
  {"segments": [{"start","end","sentences":[{"text","start","end"}]}], "duration": float}
"""
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .. import config, db, logs
from ..agents.text_utils import sentences as split_sentences
from . import ffmpeg

RATE = 24000
SENT_GAP = 0.22
SEG_GAP = 0.45

_cache: dict[str, Any] = {}
KOKORO_DIR = config.VOICES_DIR / "kokoro"


def _to_int16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def _resample(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == RATE:
        return x
    n = int(len(x) * RATE / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _kokoro_pcm(text: str, voice: str, speed: float) -> bytes:
    if "kokoro" not in _cache:
        from kokoro_onnx import Kokoro
        model, voices = KOKORO_DIR / "kokoro-v1.0.onnx", KOKORO_DIR / "voices-v1.0.bin"
        if not model.exists():
            raise FileNotFoundError("Kokoro model missing; run scripts\\setup.ps1")
        _cache["kokoro"] = Kokoro(str(model), str(voices))
    lang = "en-gb" if voice.startswith("b") else "en-us"
    samples, sr = _cache["kokoro"].create(text, voice=voice, speed=speed, lang=lang)
    return _to_int16(_resample(np.asarray(samples, dtype=np.float32), sr))


def _piper_pcm(text: str, voice: str, speed: float) -> bytes:
    from piper import PiperVoice, SynthesisConfig
    if voice not in _cache:
        model = config.VOICES_DIR / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"Piper voice missing: {model}")
        _cache[voice] = PiperVoice.load(str(model))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        _cache[voice].synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=1.0 / speed))
    buf.seek(0)
    with wave.open(buf, "rb") as rf:
        sr = rf.getframerate()
        x = np.frombuffer(rf.readframes(rf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return _to_int16(_resample(x, sr))


def _sapi_pcm(text: str, work: Path, idx: int) -> bytes:
    import pyttsx3
    raw, conv = work / f"sapi_{idx}.wav", work / f"sapi_{idx}_c.wav"
    eng = pyttsx3.init()
    eng.setProperty("rate", 175)
    eng.save_to_file(text, str(raw))
    eng.runAndWait()
    ffmpeg.run(["-i", str(raw), "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", str(conv)])
    with wave.open(str(conv), "rb") as rf:
        return rf.readframes(rf.getnframes())


def voice_for(kind: str) -> str:
    return db.get_setting("voice_short" if kind == "short" else "voice_long") or ("am_michael" if kind == "short" else "bm_george")


def synthesize(segments: list[dict], work: Path, voice: str | None = None, speed: float = 1.0,
               video_id: int | None = None, sent_gap: float = SENT_GAP, seg_gap: float = SEG_GAP,
               tail: float | None = None, voices: list[str] | None = None) -> dict[str, Any]:
    """sent_gap / seg_gap: pauses between sentences / beats. tail: if set, the closing silence is trimmed to this
    many seconds (Shorts loop straight back into the hook). voices: optional Kokoro voice per segment (AI character
    podcasts: hosts take turns); segments beyond the list use `voice`."""
    voice = voice or "am_michael"
    voices = voices or []
    work.mkdir(parents=True, exist_ok=True)
    out = work / "narration.wav"
    SENT_GAP, SEG_GAP = sent_gap, seg_gap      # noqa: N806 - local pacing override
    silence_sent = b"\x00\x00" * int(RATE * SENT_GAP)
    silence_seg = b"\x00\x00" * int(RATE * SEG_GAP)
    pcm = bytearray()
    t = 0.0
    timeline = []
    engines = ["kokoro", "piper", "sapi"]
    engine = engines[0]
    n = 0
    for si, seg in enumerate(segments):
        seg_start = t
        sents_out = []
        seg_voice = voices[si] if si < len(voices) and voices[si] else voice
        for s in split_sentences(seg["text"]) or [seg["text"]]:
            while True:
                try:
                    if engine == "kokoro":
                        chunk = _kokoro_pcm(s, seg_voice, speed)
                    elif engine == "piper":
                        chunk = _piper_pcm(s, config.PIPER_VOICE, speed)
                    else:
                        chunk = _sapi_pcm(s, work, n)
                    break
                except Exception as e:
                    nxt = engines.index(engine) + 1
                    if nxt >= len(engines):
                        raise
                    logs.warn("tts", f"{engine} failed ({e}); falling back to {engines[nxt]}", video_id)
                    engine = engines[nxt]
            n += 1
            d = len(chunk) / 2 / RATE
            sents_out.append({"text": s, "start": round(t, 3), "end": round(t + d, 3)})
            pcm += chunk + silence_sent
            t += d + SENT_GAP
        pcm += silence_seg
        t += SEG_GAP
        timeline.append({"start": round(seg_start, 3), "end": round(t, 3), "sentences": sents_out,
                         **({"voice": seg_voice} if voices else {})})
    if tail is not None and timeline and timeline[-1]["sentences"]:
        speech_end = timeline[-1]["sentences"][-1]["end"]
        t = round(speech_end + tail, 3)
        del pcm[int(t * RATE) * 2:]
        timeline[-1]["end"] = t
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(bytes(pcm))
    used = ", ".join(dict.fromkeys(voices)) if voices else voice
    if engine != "kokoro" and voices:
        logs.warn("tts", f"{engine} has one voice only: the characters' different voices were not used", video_id)
    logs.info("tts", f"{engine} ({used if engine == 'kokoro' else config.PIPER_VOICE}) narration {t:.1f}s, {n} sentences",
              video_id)
    return {"path": str(out), "duration": round(t, 3), "segments": timeline, "engine": engine, "voice": voice}
