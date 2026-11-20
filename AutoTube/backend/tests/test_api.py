"""API tests through FastAPI's TestClient (safety controls, channels, uploads guarded)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

from .conftest import login


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield login(c)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert "ffmpeg" in j and "ollama" in j and j["emergency_stop"] is False


def test_channel_crud(client):
    r = client.post("/api/channels", json={"name": "API ch", "niche": "space science", "long_minutes": 99})
    assert r.status_code == 200 and r.json()["long_minutes"] == 20       # clamped
    cid = r.json()["id"]
    r = client.put(f"/api/channels/{cid}", json={"name": "API ch2", "niche": "space"})
    assert r.json()["name"] == "API ch2"


def test_protected_settings(client):
    r = client.put("/api/settings", json={"values": {"public_publishing_approved": True}})
    assert r.status_code == 400
    r = client.put("/api/settings", json={"values": {"nonsense": 1}})
    assert r.status_code == 400
    r = client.put("/api/settings", json={"values": {"max_retries": 2}})
    assert r.status_code == 200 and r.json()["max_retries"] == 2
    assert client.put("/api/settings", json={"values": {"shorts_per_day": 11}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"longs_per_day": 6}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"upload_mode": "yolo"}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"timezone": "Mars/Base"}}).status_code == 400
    r = client.put("/api/settings", json={"values": {"shorts_per_day": 5, "upload_mode": "manual"}})
    assert r.json()["shorts_per_day"] == 5 and r.json()["upload_mode"] == "manual"


def test_public_publishing_requires_exact_phrase(client):
    r = client.post("/api/public-publishing", json={"approve": True, "confirm_text": "yes"})
    assert r.status_code == 400
    assert db.get_setting("public_publishing_approved") is False
    r = client.post("/api/public-publishing", json={"approve": True, "confirm_text": "I APPROVE PUBLIC PUBLISHING"})
    assert r.status_code == 200 and db.get_setting("public_publishing_approved") is True
    client.post("/api/public-publishing", json={"approve": False})
    assert db.get_setting("public_publishing_approved") is False


def test_emergency_stop_blocks_creation(client):
    client.post("/api/emergency-stop", json={"active": True})
    assert db.get_setting("automation_enabled") is False
    cid = client.get("/api/channels").json()[0]["id"]
    r = client.post("/api/videos", json={"channel_id": cid, "kind": "short", "topic": "Tunguska event"})
    assert r.status_code == 409
    client.post("/api/emergency-stop", json={"active": False})


def test_upload_requires_rendered_video(client):
    cid = client.get("/api/channels").json()[0]["id"]
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status) VALUES (?,?,?,?,?)",
                     (cid, "short", "Some topic", "some topic", "DRAFT"))
    r = client.post(f"/api/videos/{vid}/upload")
    assert r.status_code == 400


def test_upload_blocked_without_youtube(client):
    from app.youtube import api as yt
    with pytest.raises(yt.YouTubeError):
        yt.upload({"id": 1, "title": "t", "description": "d", "tags": "[]", "video_path": "x.mp4"})


def test_youtube_status_without_client(client):
    j = client.get("/api/youtube/status").json()
    assert j["connected"] is False
    assert j["redirect_uri"].endswith("/api/youtube/oauth/callback")
    assert client.get("/api/youtube/connect", follow_redirects=False).status_code == 400


def test_bad_client_secret_rejected(client):
    r = client.post("/api/youtube/client-secret", files={"file": ("x.json", b'{"foo": 1}', "application/json")})
    assert r.status_code == 400
    r = client.put("/api/youtube/config", json={"client_id": "nope", "client_secret": "x"})
    assert r.status_code == 400


def test_dashboard_and_logs(client):
    assert client.get("/api/dashboard").status_code == 200
    assert isinstance(client.get("/api/logs").json(), list)
    assert client.get("/api/quota").status_code == 200
    jobs = {j["id"] for j in client.get("/api/dashboard").json()["jobs"]}
    assert {"upload_dispatcher", "production_planner", "retry_queue", "token_health", "daily_report"} <= jobs


def test_setup_wizard_api(client):
    import os
    from app import config
    config.ENV_FILE.write_text("", encoding="utf-8")
    os.environ["GOOGLE_CLIENT_ID"] = os.environ["GOOGLE_CLIENT_SECRET"] = ""
    assert client.get("/api/youtube/status").json()["setup_required"] is True
    assert client.get("/api/youtube/connect", follow_redirects=False).status_code == 400
    r = client.put("/api/youtube/setup", json={"client_id": "111-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-abcdefghij"})
    assert r.status_code == 200 and r.json()["setup_required"] is False
    assert "GOCSPX" not in str(r.json())                  # secret never returned to the browser
    r = client.get("/api/youtube/connect", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("https://accounts.google.com/")
