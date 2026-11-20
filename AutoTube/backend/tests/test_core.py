"""Unit tests: database, settings, quota, duplicate prevention, agents (offline), subtitles, SEO, token crypto."""
import json

from app import db, quota
from app.agents import factcheck, research, script, seo
from app.agents.text_utils import jaccard, normalize_key
from app.media import subtitles

from .conftest import FAKE_RESEARCH


def test_migrations_idempotent():
    assert db.migrate() == []
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"videos", "channels", "uploads", "quota_usage", "logs", "settings"} <= tables


def test_settings_roundtrip():
    assert db.get_setting("public_publishing_approved") is False      # safe default
    db.set_setting("max_daily_videos", 7)
    assert db.get_setting("max_daily_videos") == 7
    db.set_setting("max_daily_videos", 4)


def test_quota_limits():
    before = quota.used("pixabay")
    quota.record("pixabay", "test", 3)
    assert quota.used("pixabay") == before + 3
    assert quota.allow("pixabay", 1)
    assert not quota.allow("pixabay", 10_000_000)


def test_duplicate_prevention(channel):
    db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key) VALUES (?,?,?,?)",
               (channel["id"], "short", "The Voynich Manuscript", normalize_key("The Voynich Manuscript")))
    assert research.is_duplicate(channel["id"], "the voynich manuscript")
    assert research.is_duplicate(channel["id"], "Voynich manuscript")
    assert not research.is_duplicate(channel["id"], "Tunguska event")
    assert jaccard("a b c", "x y z") == 0


def test_template_script_and_factcheck():
    s = script.template_script("Voynich manuscript", FAKE_RESEARCH, 120, short=True)
    assert s["provider"] == "template"
    assert len(s["segments"]) >= 4
    fc = factcheck.check(s, FAKE_RESEARCH)
    assert fc["method"] == "lexical"
    assert fc["score"] >= 80          # extractive script must be source-supported


def test_factcheck_removes_unsupported():
    s = {"segments": [{"text": "Hook line here."},
                      {"text": "The Voynich manuscript was carbon-dated to the early 15th century."},
                      {"text": "Aliens from Neptune wrote spaceship blueprints using lasers."},
                      {"text": "Follow for more."}]}
    fc = factcheck.check(s, FAKE_RESEARCH)
    texts = [x["text"] for x in fc["script"]["segments"]]
    assert not any("Neptune" in t for t in texts)
    assert fc["removed"] == 1


def test_captions_timing():
    tl = {"segments": [{"start": 0, "end": 4, "sentences": [
        {"text": "One two three four five six seven", "start": 0.0, "end": 2.0},
        {"text": "Eight nine", "start": 2.3, "end": 3.0}]}]}
    caps = subtitles.chunk_captions(tl, 3)
    assert caps[0]["start"] == 0.0
    assert all(c["end"] > c["start"] for c in caps)
    assert caps[-1]["text"] == "Eight nine"
    assert " ".join(c["text"] for c in caps).split() == "One two three four five six seven Eight nine".split()


def test_srt_and_ass(tmp_path):
    caps = [{"text": "Hello world", "start": 0, "end": 1.5}, {"text": "{bad} braces", "start": 1.5, "end": 3}]
    srt = subtitles.write_srt(caps, tmp_path / "a.srt").read_text()
    assert "00:00:00,000 --> 00:00:01,500" in srt
    ass = subtitles.write_ass(caps, tmp_path / "a.ass", portrait=True).read_text()
    assert "PlayResY: 1920" in ass and "{bad}" not in ass


def test_seo_fallback_limits(channel):
    s = script.template_script("Voynich manuscript", FAKE_RESEARCH, 120, short=True)
    meta = seo.optimize("Voynich manuscript", "short", s, FAKE_RESEARCH, dict(channel), ["Video by X on Pexels"])
    assert "#Shorts" in meta["title"]
    assert len(meta["title"]) <= 100
    assert sum(len(t) + 2 for t in meta["tags"]) <= 500
    # description = what the video shows + hashtags; no links, domains or credit lists (credits are on screen)
    d = meta["description"]
    assert "wikipedia.org" not in d and "http" not in d and "Pexels" not in d and "Sources:" not in d
    assert d.rstrip().endswith(" ".join(f"#{h}" for h in meta["hashtags"])) and "#Shorts" in d
    assert len(meta["description"]) <= 5000


def test_secure_store_encrypts_at_rest():
    from app import secure
    secure.put("unit_test_secret", {"token": "very-secret-value"})
    assert secure.get("unit_test_secret") == {"token": "very-secret-value"}
    raw = secure.raw_blob("unit_test_secret")
    assert "very-secret-value" not in raw
    import base64
    assert base64.b64decode(raw).startswith(b"dpapi:")       # Windows DPAPI
    secure.delete("unit_test_secret")
    assert secure.get("unit_test_secret") is None


def test_wikimedia_license_filter(monkeypatch):
    from app.media import visuals

    def page(pid, lic, w=1600, h=1000, url="https://x/a.jpg"):
        return {"pageid": pid, "title": f"File:{pid}.jpg", "imageinfo": [{
            "width": w, "height": h, "thumburl": url,
            "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a>Someone</a>"}}}]}

    fake = {"query": {"pages": [page(1, "CC BY-SA 4.0"), page(2, "CC BY-NC 2.0"), page(3, "All rights reserved"), page(7, "CC BY-ND 4.0"),
                                page(4, "Public domain"), page(5, "CC0", w=300, h=200), page(6, "CC BY 2.0", url="https://x/a.svg")]}}
    monkeypatch.setattr(visuals, "_cached_json", lambda key, fetch: fake)
    got = visuals._wikimedia("anything", 10)
    assert [g["id"] for g in got] == ["wm_1", "wm_4"]          # NC, ND, unlicensed, tiny and SVG rejected
    assert "Someone" in got[0]["credit"] and "<a>" not in got[0]["credit"]


def test_relevance_filter():
    from app.media import visuals
    assert visuals.relevant("Tunguska event fallen trees 1908", "Tunguska event")
    assert not visuals.relevant("Cute kitten on sofa", "Tunguska event")


def test_copy_detection():
    from app.agents.text_utils import copies_source
    src = "The vellum on which it is written has been carbon-dated to the early 15th century by researchers."
    assert copies_source("Experts say the vellum on which it is written has been carbon-dated to the early 15th century.", src)
    assert not copies_source("Scientists dated its animal-skin pages to the early 1400s.", src)


def test_quality_blocks_template_and_copies(channel):
    """The quality gate must hold back template-written / copied scripts for human review."""
    from app import pipeline
    from .conftest import FAKE_RESEARCH as R
    s = script.template_script("Voynich manuscript", R, 120, short=True)
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status, stage, video_path) VALUES (?,?,?,?,?,?,?)",
                     (channel["id"], "short", "Voynich q", "voynich q", "QUALITY_CHECK", "quality", "x.mp4"))
    db.update_video(vid, script_json=s, research_json=R, factcheck_json={"score": 100},
                    storyboard_json={"visual_stats": {"unique": 10, "empty_scenes": 0}, "shots": 10})
    import app.media.formats as f
    import app.media.render as r
    orig, orig_f = r.verify, f.validate
    r.verify = lambda *a, **k: {"ok": True, "problems": [], "duration": 40.0, "resolution": "1080x1920", "size_mb": 3}
    f.validate = lambda p, k: {"ok": True, "errors": [], "warnings": [], "info": {}, "kind": k}
    try:
        pipeline.st_quality(db.get_video(vid), dict(channel))
    finally:
        r.verify, f.validate = orig, orig_f
    v = db.get_video(vid)
    assert v["status"] == "WAITING_FOR_APPROVAL"
    assert any("template" in b for b in v["quality_json"]["blockers"])
    assert any("copy" in b for b in v["quality_json"]["blockers"])


def test_login_required_everywhere():
    """Every page and API call needs a login (local and remote); credentials come from the hashed credentials file."""
    import json
    from fastapi.testclient import TestClient
    from app import main, remote_auth
    from .conftest import TEST_PASS, TEST_USER
    c = TestClient(main.app)
    tunnel = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "x"}
    assert c.get("/api/ping").status_code == 200                            # liveness probe stays open
    assert c.get("/api/health").status_code == 401                          # local: login needed too
    assert c.get("/api/health", headers=tunnel).status_code == 401
    r = c.get("/youtube/?tab=a", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fyoutube%2F%3Ftab%3Da"
    page = c.get("/login")
    assert page.status_code == 200 and 'name="password"' in page.text
    bad = c.post("/api/login", data={"username": TEST_USER, "password": "nope"}, follow_redirects=False)
    assert bad.status_code == 401 and "Wrong username or password" in bad.text
    wrong_user = c.post("/api/login", data={"username": "x", "password": TEST_PASS}, follow_redirects=False)
    assert remote_auth.COOKIE not in wrong_user.cookies and remote_auth.COOKIE not in bad.cookies
    ok = c.post("/api/login", data={"username": TEST_USER, "password": TEST_PASS, "next": "//evil.com"},
                follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"          # no open redirect
    tok = ok.cookies.get(remote_auth.COOKIE)
    assert remote_auth.valid(tok)
    fresh = TestClient(main.app)
    assert fresh.get("/api/health", cookies={remote_auth.COOKIE: tok}).status_code == 200
    assert fresh.get("/api/health", headers=tunnel, cookies={remote_auth.COOKIE: tok}).status_code == 200
    assert fresh.get("/api/health", cookies={remote_auth.COOKIE: tok[:-2] + "00"}).status_code == 401
    # the password is never stored in plain text; a plain "password" entry is hashed in place
    f = remote_auth.creds_file()
    stored = json.loads(f.read_text())
    assert TEST_PASS not in f.read_text() and stored["password_hash"].startswith("pbkdf2_sha256$")
    try:
        f.write_text(json.dumps({"username": TEST_USER, "password": "changed-pass"}))
        assert not remote_auth.valid(tok)                                   # new password logs devices out
        assert "changed-pass" not in f.read_text() and "password_hash" in f.read_text()
        assert fresh.post("/api/login", data={"username": TEST_USER, "password": "changed-pass"},
                          follow_redirects=False).status_code == 303
    finally:
        remote_auth.set_credentials(TEST_USER, TEST_PASS)
        remote_auth._fails.clear()
    out = c.post("/api/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/login"
