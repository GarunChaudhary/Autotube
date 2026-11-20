"""Several YouTube accounts: each channel profile has its own Google authorization, uploads go to the right one."""
import datetime as dt

import pytest

from app import db, pipeline, scheduling, secure
from app.youtube import api as yt
from app.youtube import oauth

from .test_scheduler_oauth import CID, _creds, _reset_env

UTC = dt.timezone.utc


@pytest.fixture
def two(monkeypatch):
    """Two channel profiles, both signed in to different (fake) YouTube channels."""
    _reset_env()
    for t in ("uploads", "videos", "schedule_slots", "oauth_states", "youtube_accounts"):
        db.execute(f"DELETE FROM {t}")
    ids = [r["id"] for r in db.query("SELECT id FROM channels ORDER BY id")]
    while len(ids) < 2:
        ids.append(db.execute("INSERT INTO channels(name, niche) VALUES (?, 'history')", (f"Profile {len(ids)}",)))
    a, b = ids[0], ids[1]
    for cid in ids:
        secure.delete(oauth.token_key(cid))
    secure.delete(oauth.LEGACY_TOKEN_KEY)
    for k, v in {"timezone": "Asia/Kolkata", "shorts_per_day": 3, "longs_per_day": 1, "max_daily_videos": 10,
                 "automation_enabled": True, "auto_upload": True, "automation_paused": False, "emergency_stop": False,
                 "upload_mode": "automatic", "allow_same_time": False, "production_lead_minutes": 1440}.items():
        db.set_setting(k, v)
    oauth.save_client(CID, "GOCSPX-secretvalue123")

    def connect(profile, yt_id, title, refresh):
        url = oauth.authorization_url(profile)
        state = url.split("state=")[1].split("&")[0]
        monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds(token="at-" + refresh, refresh=refresh))
        monkeypatch.setattr(oauth, "_fetch_channel", lambda c: {"id": yt_id, "title": title, "thumbnail": None})
        return oauth.handle_callback(f"http://localhost:8000/api/youtube/oauth/callback?state={state}&code=x", state)

    connect(a, "UC_A", "Channel A", "rt-a")
    connect(b, "UC_B", "Channel B", "rt-b")
    return a, b, connect


def test_each_profile_has_its_own_account(two):
    a, b, _ = two
    assert oauth.connection(a)["channel_title"] == "Channel A" and oauth.connection(b)["channel_title"] == "Channel B"
    assert secure.get(oauth.token_key(a))["refresh_token"] == "rt-a"
    assert secure.get(oauth.token_key(b))["refresh_token"] == "rt-b"
    assert set(oauth.connected_profiles()) >= {a, b} and oauth.overall_status() == "connected"
    assert db.one("SELECT youtube_title FROM channels WHERE id=?", (b,))["youtube_title"] == "Channel B"
    oauth.disconnect(b)                                          # disconnecting one leaves the other untouched
    assert not oauth.is_connected(b) and oauth.is_connected(a)
    assert secure.get(oauth.token_key(a))["refresh_token"] == "rt-a"


def test_same_youtube_channel_cannot_be_linked_twice(two):
    a, b, connect = two
    with pytest.raises(oauth.OAuthError, match="already linked"):
        connect(b, "UC_A", "Channel A", "rt-x")
    assert oauth.connection(b)["channel_title"] == "Channel B"           # unchanged
    assert secure.get(oauth.token_key(b))["refresh_token"] == "rt-b"


def test_switching_account_never_reuses_old_refresh_token(two, monkeypatch):
    a, b, connect = two
    url = oauth.authorization_url(b)
    state = url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds(token="at-new", refresh=None))
    monkeypatch.setattr(oauth, "_fetch_channel", lambda c: {"id": "UC_C", "title": "Channel C", "thumbnail": None})
    with pytest.raises(oauth.OAuthError, match="refresh token"):
        oauth.handle_callback(f"http://localhost:8000/cb?state={state}&code=x", state)


def test_uploads_go_to_the_videos_own_account(two, monkeypatch, tmp_path):
    a, b, _ = two
    from app.media import formats
    used = []

    class Fake:
        def __init__(self, cid):
            self.cid = cid

    monkeypatch.setattr(yt, "_yt", lambda cid=None: used.append(cid) or Fake(cid))
    monkeypatch.setattr(formats, "validate", lambda p, k: {"ok": True, "errors": [], "warnings": [], "info": {}, "kind": k})
    monkeypatch.setattr(yt, "_insert", lambda service, body, path, notify: {"id": f"YT-{service.cid}"})
    monkeypatch.setattr(yt, "_status", lambda service, vid: {"privacyStatus": "public", "uploadStatus": "uploaded"})
    monkeypatch.setattr(yt, "set_thumbnail", lambda service, vid, path: {"status": "set", "error": None})
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0" * 2000)
    past = scheduling.iso(dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    vids = {}
    for cid in (a, b):
        vids[cid] = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status, stage, title, description, "
                               "tags, video_path, target_privacy, scheduled_for) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (cid, "short", f"topic {cid}", f"topic {cid}", "READY", "done", "T", "D", "[]", str(f),
                                "public", past))
    oauth.disconnect(b)
    assert scheduling.dispatch() == [vids[a]]                    # B's account is not linked: its video waits
    assert db.get_video(vids[a])["youtube_video_id"] == f"YT-{a}" and used == [a]
    assert db.get_video(vids[b])["status"] == "READY"
    assert "not connected" in pipeline.immediate_block_reason(b) and pipeline.immediate_block_reason(a) is None


def test_planner_fills_each_profiles_slots(two, monkeypatch):
    a, b, _ = two
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: queued.append(vid))
    now = dt.datetime.now(UTC)
    t = (now + dt.timedelta(hours=2)).astimezone(scheduling.tz()).strftime("%H:%M")
    scheduling.create_slot("A short", "short", t)
    scheduling.create_slot("B short", "short", t, channel_id=b)    # same time on another account is allowed
    with pytest.raises(scheduling.ScheduleError, match="already uses"):
        scheduling.create_slot("B clash", "short", t, channel_id=b)
    names = iter(["Tunguska event", "Voynich manuscript", "Mary Celeste", "Dyatlov Pass"])
    ids = scheduling.plan(now, suggest=lambda n: [next(names) for _ in range(n)])
    owners = sorted(db.get_video(v)["channel_id"] for v in ids)
    assert owners == sorted([a, b])


def test_legacy_single_token_moves_to_first_profile(two):
    a, b, _ = two
    secure.delete(oauth.token_key(a))
    secure.put(oauth.LEGACY_TOKEN_KEY, {"token": "t", "refresh_token": "legacy-rt"})
    assert oauth.default_channel_id() == a
    assert secure.get(oauth.token_key(a)) is None
    assert oauth._stored(a)["refresh_token"] == "legacy-rt"
    assert secure.get(oauth.LEGACY_TOKEN_KEY) is None and secure.get(oauth.token_key(a))["refresh_token"] == "legacy-rt"
    assert oauth._stored(b)["refresh_token"] == "rt-b"


def test_unfinished_google_signin_is_reported(two):
    a, b, connect = two
    oauth.authorization_url(b)                        # user goes to Google, Google never sends them back
    db.execute("UPDATE oauth_states SET created_at=datetime('now','-5 minutes') WHERE channel_id=?", (b,))
    assert oauth.signin_unfinished(b) and not oauth.signin_unfinished(a)
    assert next(c for c in oauth.connections() if c["profile_id"] == b)["signin_unfinished"]
    connect(b, "UC_B", "Channel B", "rt-b2")          # a later successful sign-in clears it
    assert oauth.signin_unfinished(b) is None
