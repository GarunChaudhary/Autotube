"""Story genres and the 'upload immediately' flow (fake YouTube, no network)."""
import pytest
from fastapi.testclient import TestClient

from app import db, pipeline, scheduling
from app.agents import genres, script as script_agent, seo
from app.main import app
from app.youtube import oauth

from .conftest import FAKE_RESEARCH, login
from .test_publishing import _video, env  # noqa: F401  (fixture re-export)


@pytest.fixture
def connected(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(oauth, "is_connected", lambda *a, **k: True)
    return env


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield login(c)


def test_genres_api_lists_all(client):
    j = client.get("/api/genres").json()["genres"]
    keys = {g["key"] for g in j}
    assert {"horror", "comedy", "mystery", "true_crime", "motivation"} <= keys
    assert all(g["label"] and g["pitch"] for g in j)


def test_every_genre_is_complete():
    for k, g in genres.GENRES.items():
        for f in ("label", "pitch", "topic_hint", "style", "tone", "voice", "mood", "title_style", "tags", "hashtags"):
            assert g.get(f), f"{k}.{f}"
        assert g["mood"] in ("mysterious", "epic", "uplifting", "calm")
        assert len(g["hashtags"]) == 3


def test_unknown_genre_rejected(client):
    cid = client.get("/api/channels").json()[0]["id"] if client.get("/api/channels").json() else \
        client.post("/api/channels", json={"name": "G", "niche": "history"}).json()["id"]
    r = client.post("/api/videos", json={"channel_id": cid, "kind": "short", "topic": "x", "genre": "nope"})
    assert r.status_code == 400
    assert client.post(f"/api/channels/{cid}/suggest-topics?genre=nope").status_code == 400


def test_immediate_and_schedule_are_exclusive(client):
    cid = client.get("/api/channels").json()[0]["id"]
    r = client.post("/api/videos", json={"channel_id": cid, "kind": "short", "topic": "Dyatlov Pass incident",
                                         "upload_immediately": True, "scheduled_for": "2030-01-01T00:00:00Z"})
    assert r.status_code == 400


def test_create_video_stores_genre_and_immediate(channel):
    vid = pipeline.create_video(channel["id"], "short", "Mary Celeste", genre="mystery", upload_immediately=True,
                                skip_review=True)
    v = db.get_video(vid)
    assert v["genre"] == "mystery" and v["upload_immediately"] == 1 and v["skip_review"] == 1
    assert v["target_privacy"] == "public" and v["scheduled_for"] is None
    # skip_review is only honoured together with upload immediately
    vid2 = pipeline.create_video(channel["id"], "short", "Tunguska event 1908", skip_review=True)
    assert db.get_video(vid2)["skip_review"] == 0


def test_genre_style_reaches_script_prompt(channel, monkeypatch):
    seen = {}

    def fake_json(prompt, system, temp, vid, role=None):
        seen["prompt"] = prompt
        raise RuntimeError("no llm in tests")
    monkeypatch.setattr(script_agent.llm, "generate_json", fake_json)
    ch = genres.channel_with_genre(channel, "horror")
    out = script_agent.write_short("Voynich manuscript", FAKE_RESEARCH, ch)
    assert "GENRE: Horror" in seen["prompt"] and "ominous" in seen["prompt"]
    assert out["provider"] == "template"       # falls back honestly; quality gate flags it


def test_genre_seo_tags_and_hashtags(channel, monkeypatch):
    monkeypatch.setattr(seo.llm, "generate_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    ch = genres.channel_with_genre(channel, "comedy")
    script = {"segments": [{"text": "A war was lost over a bucket."}, {"text": "Really."}, {"text": "Yes."}]}
    meta = seo.optimize("War of the Bucket", "short", script, FAKE_RESEARCH, ch, [])
    assert "funny history" in meta["tags"] and meta["tags"][0] == "War of the Bucket"
    assert meta["hashtags"][0] == "Shorts" and "comedy" in [h.lower() for h in meta["hashtags"]]
    assert sum(len(t) + 2 for t in meta["tags"]) <= 500


def test_skip_review_bypasses_only_approval_mode():
    before = db.get_setting("upload_mode")
    db.set_setting("upload_mode", "approval")
    clean = {"quality_json": {"score": 90, "blockers": []}}
    assert pipeline.post_qc_status(clean) == "WAITING_FOR_APPROVAL"
    assert pipeline.post_qc_status({**clean, "skip_review": 1}) == "READY"
    # quality blockers always need a human, even when skipping review
    bad = {"quality_json": {"score": 90, "blockers": ["fact-check score 40 < 60"]}, "skip_review": 1}
    assert pipeline.post_qc_status(bad) == "WAITING_FOR_APPROVAL"
    db.set_setting("upload_mode", before)


def test_immediate_upload_publishes_now(connected):
    env = connected
    vid = _video(env)
    db.update_video(vid, upload_immediately=1)
    db.set_setting("auto_upload", False)          # independent of the slot auto-upload switch
    db.set_setting("automation_paused", False)
    res = pipeline.upload_immediate(vid)
    v = db.get_video(vid)
    assert res and v["status"] == "PUBLISHED" and v["yt_privacy"] == "public"
    # a second call never re-uploads
    assert pipeline.upload_immediate(vid) is None
    assert sum(1 for c in env[0].calls if c[0] == "insert") == 1


def test_immediate_upload_waits_when_blocked(connected):
    env = connected
    vid = _video(env)
    db.update_video(vid, upload_immediately=1)
    db.set_setting("emergency_stop", True)
    try:
        assert pipeline.upload_immediate(vid) is None
        v = db.get_video(vid)
        assert v["status"] == "READY" and "emergency stop" in v["error"]
        assert scheduling.dispatch_immediate() == []
    finally:
        db.set_setting("emergency_stop", False)
    assert scheduling.dispatch_immediate() == [vid]
    assert db.get_video(vid)["status"] == "PUBLISHED"


def test_non_immediate_videos_not_touched_by_immediate_dispatch(connected):
    env = connected
    vid = _video(env)
    assert scheduling.dispatch_immediate() == []
    assert db.get_video(vid)["status"] == "READY"


def test_immediate_upload_waits_without_youtube(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(oauth, "is_connected", lambda *a, **k: False)
    vid = _video(env)
    db.update_video(vid, upload_immediately=1)
    assert pipeline.upload_immediate(vid) is None
    v = db.get_video(vid)
    assert v["status"] == "READY" and "not connected" in v["error"] and not v["youtube_video_id"]


# ------------------------------------------------------------------ invented topics are rejected
FAKE_WIKI = {
    "Mary Celeste": "The Mary Celeste was an American merchant ship discovered adrift and deserted in the "
                    "Atlantic Ocean in 1872. The crew was never seen again. " * 20,
    "Vaux-le-Vicomte": "Vaux-le-Vicomte is a baroque French chateau built for Nicolas Fouquet. Gardens by Le Notre. " * 20,
}


@pytest.fixture
def fake_wiki(monkeypatch):
    from app.agents import research

    def search(q, limit=10):
        ql = q.lower()
        return [t for t in FAKE_WIKI if any(w in ql for w in research._cw(t))][:limit]
    monkeypatch.setattr(research, "wiki_search", search)
    monkeypatch.setattr(research, "wiki_article", lambda t, max_chars=9000: {"title": t, "url": "u", "text": FAKE_WIKI[t][:max_chars]})
    return research


def test_verify_topic_accepts_real_and_rejects_invented(fake_wiki):
    assert fake_wiki.verify_topic("The Mystery of the Mary Celeste: Ghost Ship") == "Mary Celeste"
    # a real place does not make an invented legend about it real
    assert fake_wiki.verify_topic("The Phantom Horse of Vaux-le-Vicomte: A Haunted Estate Mystery") is None
    assert fake_wiki.verify_topic("The Haunted Lighthouse of Old Mill Creek") is None


def test_suggestions_only_return_verified_topics(fake_wiki, channel, monkeypatch):
    from app.agents import topics
    monkeypatch.setattr(topics, "find", lambda *a, **k: [])          # genre finder empty -> AI ideas fallback
    monkeypatch.setattr(fake_wiki.llm, "generate_json", lambda *a, **k: (
        {"topics": ["The Phantom Horse of Vaux-le-Vicomte", "Mary Celeste ghost ship", "Old Mill Creek lighthouse ghost"]}, "x"))
    assert fake_wiki.suggest_topics(channel, 3, genre="horror") == ["Mary Celeste"]


def test_typed_invented_topic_rejected_at_creation(fake_wiki, client):
    cid = client.get("/api/channels").json()[0]["id"]
    r = client.post("/api/videos", json={"channel_id": cid, "kind": "short", "upload_immediately": True,
                                         "topic": "The Phantom Horse of Vaux-le-Vicomte: A Haunted Estate Mystery"})
    assert r.status_code == 400 and "No Wikipedia source" in r.json()["detail"]


def test_no_source_fails_immediately_without_retries(fake_wiki, channel):
    vid = pipeline.create_video(channel["id"], "short", "Invented legend of nowhere")
    pipeline.run_video(vid)
    v = db.get_video(vid)
    assert v["status"] == "FAILED" and "No Wikipedia source" in v["error"]


def test_genre_finder_ranks_real_topics_by_demand(channel, monkeypatch):
    from app.agents import topics
    monkeypatch.setattr(topics, "candidates", lambda g: ["Chile", "Bermuda Triangle", "Mary Celeste", "Bell Witch",
                                                         "Short stub", "Ghost (2026 film)"])
    monkeypatch.setattr(topics, "_stats", lambda ts: {
        "Chile": {"views": 900000, "bytes": 90000, "desc": "Country in South America"},
        "Bermuda Triangle": {"views": 86000, "bytes": 40000, "desc": "Urban legend based on a region in North Atlantic"},
        "Mary Celeste": {"views": 34000, "bytes": 60000, "desc": "Ship found deserted in 1872"},
        "Bell Witch": {"views": 17000, "bytes": 90000, "desc": "American folklore legend"},
        "Short stub": {"views": 99000, "bytes": 1200, "desc": ""},
        "Ghost (2026 film)": {"views": 99000, "bytes": 20000, "desc": "2026 film"}})
    monkeypatch.setattr(topics, "_intros", lambda ts: {
        "Bermuda Triangle": "A region where ships and aircraft are said to have disappeared under mysterious "
                            "circumstances; a paranormal legend.",
        "Mary Celeste": "A ship found adrift; the crew vanished, giving rise to ghost legends.",
        "Bell Witch": "A legend of a haunting by a spirit in Tennessee folklore.",
        "Chile": "A country. Thousands disappeared under the dictatorship."})
    monkeypatch.setattr(topics, "_judge", lambda g, ts, st, it: None)   # AI unavailable -> keyword rules
    got = [t["topic"] for t in topics.find("horror", channel["id"], 3)]
    # with the AI judge, only approved candidates are used (still in demand order)
    monkeypatch.setattr(topics, "_judge", lambda g, ts, st, it: ["Bell Witch", "Bermuda Triangle"])
    assert [t["topic"] for t in topics.find("horror", channel["id"], 3)] == ["Bermuda Triangle", "Bell Witch"]
    assert got == ["Bermuda Triangle", "Mary Celeste", "Bell Witch"]     # highest demand first, no country/film/stub


def test_research_uses_only_the_verified_article(fake_wiki):
    r = fake_wiki.research("The Mystery of the Mary Celeste: Ghost Ship", "history")
    assert [s["title"] for s in r["sources"]] == ["Mary Celeste"]
    with pytest.raises(fake_wiki.NoSourceError):
        fake_wiki.research("The Phantom Horse of Vaux-le-Vicomte", "history")


def test_topic_bank_is_instant_and_skips_used(channel, monkeypatch):
    from app.agents import topics
    calls = []

    def fake_find(g, cid, count, sample=200):
        calls.append(g)
        return [{"topic": f"Story {i}", "views_30d": 100 - i} for i in range(count)]
    monkeypatch.setattr(topics, "find", fake_find)
    assert [t["topic"] for t in topics.ready("horror", channel["id"], 2)] == ["Story 0", "Story 1"]
    assert topics.ready("horror", channel["id"], 3) and len(calls) == 1          # served from the bank
    topics.take("horror", channel["id"], "Story 0")
    assert "Story 0" not in [t["topic"] for t in topics.banked("horror", channel["id"])]
