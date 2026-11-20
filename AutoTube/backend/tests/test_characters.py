"""AI characters & categories API, plus the 'approve before the slot' scheduling defaults."""
import pytest
from fastapi.testclient import TestClient

from app import db, pipeline
from app.main import app

from .conftest import login

GOOD = {"name": "Maya", "gender": "female", "age_range": "20-30", "style": "realistic", "voice": "af_heart",
        "appearance": "Indian woman, wavy black hair, round glasses, mustard hoodie", "original_confirmed": True}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield login(c)


def test_builtin_categories_exist(client):
    r = client.get("/api/characters").json()
    names = {c["name"] for c in r["categories"]}
    assert {"Podcast", "Dance", "Storytelling"} <= names
    assert "af_heart" in r["voices"] and "realistic" in r["styles"]
    assert r["engine"]["can_generate_video"] is False          # honest: no GPU connected


def test_category_crud(client):
    r = client.post("/api/character-categories", json={"name": "Cooking show", "icon": "🍳"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert client.post("/api/character-categories", json={"name": "cooking SHOW"}).status_code == 400   # duplicate
    assert client.post("/api/character-categories", json={"name": "x"}).status_code == 400               # too short
    r = client.put(f"/api/character-categories/{cid}", json={"description": "Recipes"})
    assert r.json()["description"] == "Recipes" and r.json()["name"] == "Cooking show"
    ch = client.post("/api/characters", json={**GOOD, "name": "Chef Ravi", "gender": "male", "voice": "am_michael",
                                              "category_id": cid}).json()
    assert client.delete(f"/api/character-categories/{cid}").status_code == 200
    kept = [c for c in client.get("/api/characters").json()["characters"] if c["id"] == ch["id"]][0]
    assert kept["category_id"] is None                          # character kept, just uncategorised


def test_character_crud_and_validation(client):
    pod = [c for c in client.get("/api/characters").json()["categories"] if c["name"] == "Podcast"][0]
    r = client.post("/api/characters", json={**GOOD, "category_id": pod["id"]})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["category_name"] == "Podcast" and c["status"] == "design"
    assert client.post("/api/characters", json=GOOD).status_code == 400                       # duplicate name
    assert client.post("/api/characters", json={**GOOD, "name": "Ann", "original_confirmed": False}).status_code == 400
    assert client.post("/api/characters", json={**GOOD, "name": "Ann", "voice": "nope"}).status_code == 400
    assert client.post("/api/characters", json={**GOOD, "name": "Ann", "style": "clay"}).status_code == 400
    assert client.post("/api/characters", json={**GOOD, "name": "Ann", "category_id": 99999}).status_code == 400
    r = client.put(f"/api/characters/{c['id']}", json={"status": "approved", "category_id": None})
    assert r.json()["status"] == "approved" and r.json()["category_id"] is None
    assert client.delete(f"/api/characters/{c['id']}").status_code == 200
    assert client.put(f"/api/characters/{c['id']}", json={"status": "design"}).status_code == 400


def test_voice_preview_rejects_unknown(client):
    assert client.get("/api/characters/voice-preview", params={"voice": "../../etc"}).status_code == 400


def test_defaults_make_early_and_wait_for_approval():
    assert db.DEFAULT_SETTINGS["production_lead_minutes"] == 180
    assert db.DEFAULT_SETTINGS["slot_auto_publish"] is False
    saved = {k: db.get_setting(k) for k in ("upload_mode", "slot_auto_publish")}
    try:
        db.set_setting("upload_mode", "approval")
        db.set_setting("slot_auto_publish", False)
        v = {"slot_id": 1, "scheduled_for": "2030-01-01T07:15:00Z", "quality_json": {"score": 95, "blockers": []}}
        assert pipeline.post_qc_status(v) == "WAITING_FOR_APPROVAL"
        assert pipeline.post_qc_status({**v, "quality_json": {"score": 95, "approved_by_user": True}}) == "READY"
    finally:
        for k, val in saved.items():
            db.set_setting(k, val)


# ------------------------------------------------------------------ making videos with characters
def _mk(client, name, voice, gender="female"):
    r = client.post("/api/characters", json={**GOOD, "name": name, "voice": voice, "gender": gender})
    assert r.status_code == 200, r.text
    return r.json()


def test_make_video_validation(client, channel):
    a = _mk(client, "Host A", "af_bella")
    b = _mk(client, "Host B", "am_adam", "male")
    same = _mk(client, "Host C", "af_bella")
    body = {"channel_id": channel["id"], "kind": "short", "topic": "Voynich manuscript"}
    bad = [({"character_mode": "podcast", "character_ids": [a["id"]]}, "exactly 2"),
           ({"character_mode": "podcast", "character_ids": [a["id"], a["id"]]}, "exactly 2"),
           ({"character_mode": "podcast", "character_ids": [a["id"], same["id"]]}, "same voice"),
           ({"character_mode": "dance", "character_ids": [a["id"]]}, "GPU"),
           ({"character_mode": "talking_face", "character_ids": [a["id"]]}, "GPU"),
           ({"character_mode": "narrator", "character_ids": [99999]}, "not found"),
           ({"character_mode": "karaoke", "character_ids": [a["id"]]}, "mode")]
    for extra, needle in bad:
        r = client.post("/api/videos", json={**body, **extra})
        assert r.status_code == 400 and needle in r.json()["detail"], (extra, r.text)
    client.put(f"/api/characters/{b['id']}", json={"enabled": False})
    r = client.post("/api/videos", json={**body, "character_mode": "narrator", "character_ids": [b["id"]]})
    assert r.status_code == 400 and "disabled" in r.json()["detail"]
    modes = {m["id"]: m["available"] for m in client.get("/api/characters").json()["modes"]}
    assert modes == {"narrator": True, "podcast": True, "talking_face": False, "dance": False}


def test_character_video_persona_voices_and_approval(client, channel):
    from app import characters
    a = _mk(client, "Priya", "af_nicole")
    b = _mk(client, "Arjun", "am_eric", "male")
    cast = characters.validate_cast("podcast", [a["id"], b["id"]])
    txt = characters.persona_text("podcast", cast)
    assert "Priya" in txt and "Arjun" in txt and "ALTERNATE" in txt and "SOURCE" in txt
    assert characters.segment_voices("podcast", cast, 5) == ["af_nicole", "am_eric"] * 2 + ["af_nicole"]
    assert characters.segment_voices("narrator", cast[:1], 3) == ["af_nicole"] * 3
    assert "NARRATOR" in characters.persona_text("narrator", cast[:1])
    vid = pipeline.create_video(channel["id"], "short", "Tunguska event", character_ids=[a["id"], b["id"]],
                                character_mode="podcast")
    v = db.get_video(vid)
    assert [c["name"] for c in characters.video_cast(v)] == ["Priya", "Arjun"] and v["character_mode"] == "podcast"
    assert client.get("/api/characters").json()["videos"][0]["id"] == vid
    saved = db.get_setting("upload_mode")
    try:
        db.set_setting("upload_mode", "approval")           # even in approval mode, character videos need no approval
        assert pipeline.post_qc_status({**v, "quality_json": {"score": 99, "blockers": []}}) == "READY"
        # ...but a failed quality check still waits for a human
        assert pipeline.post_qc_status({**v, "quality_json": {"score": 99, "blockers": ["x"]}}) == "WAITING_FOR_APPROVAL"
        assert pipeline.post_qc_status({**v, "quality_json": {"score": 40, "blockers": []}}) == "WAITING_FOR_APPROVAL"
        assert pipeline.post_qc_status({**v, "quality_json": {"score": 40, "approved_by_user": True}}) == "READY"
    finally:
        db.set_setting("upload_mode", saved)
    # "make it now" videos jump ahead of older background jobs in the production queue
    import queue as _queue
    old_q = pipeline._q
    pipeline._q = _queue.PriorityQueue()
    try:
        bg = pipeline.create_video(channel["id"], "long", "Halley's Comet")
        now = pipeline.create_video(channel["id"], "short", "Mount Everest", upload_immediately=True,
                                    character_ids=[a["id"]], character_mode="narrator")
        for x in (bg, now):
            pipeline._queued.discard(x)
            pipeline.enqueue(x)
        assert pipeline._q.get()[2] == now and pipeline._q.get()[2] == bg
    finally:
        pipeline._queued.discard(bg)
        pipeline._queued.discard(now)
        pipeline._q = old_q


def test_tts_uses_each_hosts_voice(tmp_path):
    from app.media import tts
    if not (tts.KOKORO_DIR / "kokoro-v1.0.onnx").exists():
        pytest.skip("Kokoro not installed")
    segs = [{"text": "Hello there, welcome back."}, {"text": "Thanks, glad to be here."}]
    tl = tts.synthesize(segs, tmp_path, voice="af_heart", voices=["af_nicole", "am_eric"])
    assert [s["voice"] for s in tl["segments"]] == ["af_nicole", "am_eric"] and tl["engine"] == "kokoro"
    one = tts.synthesize(segs, tmp_path / "b", voice="af_heart")
    assert "voice" not in one["segments"][0]                 # normal videos unchanged


def test_voice_must_match_gender_and_defaults_differ(client):
    from app import characters
    assert characters.voice_gender("am_adam") == "male" and characters.voice_gender("bf_emma") == "female"
    r = client.post("/api/characters", json={**GOOD, "name": "Boy Wrong", "gender": "male", "voice": "af_heart"})
    assert r.status_code == 400 and "male" in r.json()["detail"]
    nb = client.post("/api/characters", json={**GOOD, "name": "Sam NB", "gender": "non-binary", "voice": "af_sky"})
    assert nb.status_code == 200                                    # non-binary may use any voice
    body = {k: v for k, v in GOOD.items() if k != "voice"}
    m1 = client.post("/api/characters", json={**body, "name": "Auto M1", "gender": "male"}).json()
    m2 = client.post("/api/characters", json={**body, "name": "Auto M2", "gender": "male"}).json()
    assert characters.voice_gender(m1["voice"]) == "male" == characters.voice_gender(m2["voice"])
    assert m1["voice"] != m2["voice"]                               # each new character gets its own voice
    assert client.put(f"/api/characters/{m1['id']}", json={"voice": "af_bella"}).status_code == 400
