"""Integration tests: real TTS, music, SFX, thumbnail and full FFmpeg rendering of both formats (offline).

Input images are drawn locally as test fixtures; production never uses generated placeholder scenes."""
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import config
from app.media import ffmpeg, music, render, sfx, subtitles, thumbnail, tts

pytestmark = pytest.mark.skipif(not config.FFMPEG, reason="ffmpeg not installed")

SEGS = [{"text": "Most people never heard this story."},
        {"text": "It begins in a quiet library. Nobody expected what came next."},
        {"text": "Follow for more."}]


def _fixture_image(path: Path, size, hue: int) -> Path:
    im = Image.new("RGB", size, (hue, 90, 160 - hue // 2))
    d = ImageDraw.Draw(im)
    for k in range(0, size[0], 60):
        d.line([k, 0, size[0] - k, size[1]], fill=(255, 255 - hue, hue), width=6)
    im.save(path, quality=92)
    return path


def _build(tmp: Path, portrait: bool) -> Path:
    tl = tts.synthesize(SEGS, tmp, voice="am_michael")
    assert tl["duration"] > 3 and Path(tl["path"]).exists()
    caps = subtitles.chunk_captions(tl, 3 if portrait else 8)
    overlays = {"hook_title": "The Book Nobody Can Read" if portrait else None, "hook_end": tl["segments"][0]["end"],
                "title": None if portrait else "Test Title", "chapters": [] if portrait else [(tl["segments"][1]["start"], "Part Two", 2)],
                "credits": [(0.3, 2.0, "Image: test fixture, CC0")]}
    ass = subtitles.write_ass(caps, tmp / "captions.ass", portrait, overlays)
    mus = music.compose(tl["duration"], tmp / "music.wav", "t", "mysterious")
    shots = []
    for i, s in enumerate(tl["segments"]):
        # mix of matching and mismatched aspect ratios to exercise both framing paths
        size = (1600, 1000) if i % 2 == 0 else (900, 1400)
        img = _fixture_image(tmp / f"img{i}.jpg", size, 40 * i)
        shots.append({"start": s["start"], "end": s["end"], "asset": {"type": "image", "path": str(img)},
                      "segment_start": True, "chapter_start": (not portrait and i == 1)})
    shots[0]["start"], shots[-1]["end"] = 0.0, tl["duration"]
    events = [(1.0, "whoosh", 0.3), (2.0, "impact", 0.5)]
    return render.render(shots, Path(tl["path"]), mus, ass, tmp / "out.mp4", portrait, tl["duration"], tmp,
                         seed="t", sfx_events=events)


def test_render_short(tmp_path):
    out = _build(tmp_path, portrait=True)
    chk = render.verify(out, True, 3, 60)
    assert chk["ok"], chk
    assert chk["resolution"] == "1080x1920"
    assert -17.5 <= chk["loudness_lufs"] <= -11.5


def test_render_long_format(tmp_path):
    out = _build(tmp_path, portrait=False)
    chk = render.verify(out, False, 3, 60)
    assert chk["ok"], chk
    assert chk["resolution"] == "1920x1080"


def test_kokoro_voice_is_primary(tmp_path):
    tl = tts.synthesize([{"text": "Testing the narration engine."}], tmp_path, voice="bm_george")
    assert tl["engine"] == "kokoro"
    with wave.open(tl["path"]) as w:
        assert w.getframerate() == tts.RATE


def test_sfx_library():
    lib = sfx.library()
    assert len(lib["whoosh"]) >= 3 and lib["impact"] and lib["riser"]
    for p in lib["whoosh"]:
        assert p.stat().st_size > 10000


def test_music_moods(tmp_path):
    for mood in music.MOODS:
        p = music.compose(8, tmp_path / f"{mood}.wav", "s", mood)
        assert abs(ffmpeg.duration(p) - 8) < 0.2
    assert music.mood_for("unsolved mystery") == "mysterious"


def test_thumbnail_spec(tmp_path):
    bg = _fixture_image(tmp_path / "bg.jpg", (1600, 900), 100)
    t = thumbnail.make_thumbnail(bg, "The Book Nobody Can Read", tmp_path / "t.jpg", "s")
    im = Image.open(t)
    assert im.size == (1280, 720) and t.stat().st_size < 2_000_000


def test_format_validation_short_vs_long(tmp_path):
    """Real renders: the vertical file passes the Short spec and is rejected as a long video; and vice versa."""
    from app.media import formats
    short = _build(tmp_path / "s", portrait=True) if (tmp_path / "s").mkdir() is None else None
    r = formats.validate(short, "short")
    assert not [e for e in r["errors"] if "duration" not in e], r          # codec/size/fps/faststart all valid
    assert r["info"]["width"] == 1080 and r["info"]["height"] == 1920 and r["info"]["pix_fmt"] == "yuv420p"
    assert r["info"]["codec"] == "h264" and r["info"]["audio_codec"] == "aac" and r["info"]["faststart"]
    bad = formats.validate(short, "long")
    assert any("landscape" in e for e in bad["errors"])                     # a Short can never pass as long
    long = _build(tmp_path / "l", portrait=False) if (tmp_path / "l").mkdir() is None else None
    r = formats.validate(long, "long")
    assert [e for e in r["errors"] if "duration" not in e] == [], r        # only too short (fixture is ~6 s)
    assert any("portrait" in e for e in formats.validate(long, "short")["errors"])
