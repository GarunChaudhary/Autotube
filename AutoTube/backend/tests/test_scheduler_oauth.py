"""OAuth + scheduler tests. All Google calls are mocked - nothing is uploaded and no network is used."""
import datetime as dt
import importlib

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from app import db, pipeline, scheduling, secure
from app.youtube import api as yt
from app.youtube import oauth

CID = "1234567890-abcdefg.apps.googleusercontent.com"
NAMES = ["Tunguska event", "Voynich manuscript", "Antikythera mechanism", "Dancing plague", "Mary Celeste",
         "Baghdad battery", "Dyatlov Pass", "Roanoke colony", "Nazca lines", "Wow signal", "Taos hum", "Oak Island",
         "Phaistos disc", "Shroud Turin", "Bermuda triangle", "Rongorongo script"]


def _topics():
    it = iter(NAMES)
    return lambda n: [next(it) for _ in range(n)]
UTC = dt.timezone.utc


def _creds(token="at1", refresh="rt1", minutes=60):
    return Credentials(token=token, refresh_token=refresh, token_uri="https://oauth2.googleapis.com/token",
                       client_id=CID, client_secret="GOCSPX-secretvalue123", scopes=oauth.SCOPES,
                       expiry=dt.datetime.utcnow() + dt.timedelta(minutes=minutes))


@pytest.fixture
def clean():
    db.execute("DELETE FROM uploads")
    db.execute("DELETE FROM videos")
    db.execute("DELETE FROM schedule_slots")
    db.execute("DELETE FROM oauth_states")
    secure.delete(oauth.token_key())
    db.execute("DELETE FROM youtube_accounts")
    for k, v in {"timezone": "Asia/Kolkata", "shorts_per_day": 3, "longs_per_day": 1, "max_daily_videos": 4,
                 "automation_enabled": True, "auto_upload": True, "automation_paused": False, "emergency_stop": False,
                 "upload_mode": "automatic", "allow_same_time": False, "upload_retry_count": 3,
                 "default_privacy": "public", "scheduled_publishing": True, "allow_private_uploads": False,
                 "allow_unlisted_uploads": False, "production_lead_minutes": 1440, "slot_auto_publish": True}.items():
        db.set_setting(k, v)
    if not db.one("SELECT id FROM channels"):
        db.execute("INSERT INTO channels(name, niche) VALUES ('T','history')")
    yield


@pytest.fixture
def no_queue(monkeypatch):
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: queued.append(vid))
    return queued


def _connect(monkeypatch):
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    url = oauth.authorization_url()
    state = url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds())
    monkeypatch.setattr(oauth, "_fetch_channel", lambda c: {"id": "UC123", "title": "My Channel", "thumbnail": "https://x/a.jpg"})
    oauth.handle_callback(f"http://localhost:8000/api/youtube/oauth/callback?state={state}&code=abc", state)
    return state


# ================================================================== OAuth
def _reset_env():
    import os
    from app import config
    config.ENV_FILE.write_text("# test env\nOTHER_SETTING=keep-me\n", encoding="utf-8")
    os.environ["GOOGLE_CLIENT_ID"] = ""
    os.environ["GOOGLE_CLIENT_SECRET"] = ""


def test_setup_wizard_writes_env(clean):
    from app import config
    _reset_env()
    assert not oauth.client_configured()
    with pytest.raises(oauth.OAuthError):
        oauth.save_client("not-a-client-id", "GOCSPX-secretvalue123")
    with pytest.raises(oauth.OAuthError):
        oauth.save_client(CID, "")                      # secret required the first time
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    assert oauth.client_configured()                    # applies immediately, no restart
    env = config.ENV_FILE.read_text(encoding="utf-8")
    assert f"GOOGLE_CLIENT_ID={CID}" in env and "GOOGLE_CLIENT_SECRET=GOCSPX-secretvalue123" in env
    assert "OTHER_SETTING=keep-me" in env and "# test env" in env      # other config preserved
    oauth.save_client(CID, None)                        # re-save without re-typing the secret keeps it
    assert config.ENV_FILE.read_text(encoding="utf-8").count("GOOGLE_CLIENT_SECRET=") == 1
    assert db.one("SELECT 1 x FROM secure_store WHERE key='oauth_client_secret'") is None   # not in the DB


def test_legacy_db_credentials_move_to_env(clean):
    from app import config
    _reset_env()
    db.set_setting("oauth_client_id", CID)
    secure.put("oauth_client_secret", "GOCSPX-legacysecret99")
    oauth._migrate_legacy_file()
    assert oauth.client_configured() and "GOCSPX-legacysecret99" in config.ENV_FILE.read_text(encoding="utf-8")
    assert secure.get("oauth_client_secret") is None


def test_connect_requires_setup(clean):
    _reset_env()
    with pytest.raises(oauth.OAuthError):
        oauth.authorization_url()
    url_ok = None
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    url_ok = oauth.authorization_url()
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fyoutube%2Foauth%2Fcallback" in url_ok
    assert "accounts.google.com" in url_ok


def test_oauth_initial_connection(clean, monkeypatch):
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    url = oauth.authorization_url()
    assert "access_type=offline" in url and "consent" in url.split("prompt=")[1] and "include_granted_scopes=true" in url
    assert "code_challenge=" in url and "youtube.upload" in url
    _connect(monkeypatch)
    c = oauth.connection()
    assert c["status"] == "connected" and c["channel_id"] == "UC123" and c["channel_title"] == "My Channel"
    assert oauth.is_connected()
    raw = secure.raw_blob(oauth.token_key())
    assert "rt1" not in raw and "at1" not in raw     # tokens encrypted (DPAPI)


def test_state_validation(clean, monkeypatch):
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds())
    monkeypatch.setattr(oauth, "_fetch_channel", lambda c: None)
    with pytest.raises(oauth.OAuthError):
        oauth.handle_callback("http://localhost:8000/cb?state=forged&code=x", "forged")
    state = _connect(monkeypatch)
    with pytest.raises(oauth.OAuthError):             # single use
        oauth.handle_callback(f"http://localhost:8000/cb?state={state}&code=x", state)


def test_refresh_token_preserved_on_reconnect(clean, monkeypatch):
    _connect(monkeypatch)
    url = oauth.authorization_url()
    state = url.split("state=")[1].split("&")[0]
    monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds(token="at9", refresh=None))
    oauth.handle_callback(f"http://localhost:8000/cb?state={state}&code=y", state)
    stored = secure.get(oauth.token_key())
    assert stored["refresh_token"] == "rt1" and stored["token"] == "at9"


def test_access_token_refresh(clean, monkeypatch):
    _connect(monkeypatch)
    oauth.store(_creds(token="old", minutes=-10))     # expired access token

    def fake_refresh(creds):
        creds.token = "fresh"
        creds.expiry = dt.datetime.utcnow() + dt.timedelta(hours=1)
    monkeypatch.setattr(oauth, "_refresh", fake_refresh)
    c = oauth.load()
    assert c and c.token == "fresh"
    stored = secure.get(oauth.token_key())
    assert stored["token"] == "fresh" and stored["refresh_token"] == "rt1"
    assert oauth.connection()["last_refresh_at"]


def test_revoked_token_requires_reauth(clean, monkeypatch):
    _connect(monkeypatch)
    oauth.store(_creds(minutes=-10))

    def boom(creds):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")
    monkeypatch.setattr(oauth, "_refresh", boom)
    assert oauth.load() is None
    assert oauth.connection()["status"] == "reauth_required"
    assert secure.get(oauth.token_key())["refresh_token"] == "rt1"   # kept; reconnect replaces it


def test_no_connection_without_real_callback(clean, monkeypatch):
    """Connection is never reported unless Google's token exchange succeeds."""
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    url = oauth.authorization_url()
    state = url.split("state=")[1].split("&")[0]

    def google_rejects(flow, resp):
        raise RuntimeError("invalid_grant: Bad Request")
    monkeypatch.setattr(oauth, "_exchange_code", google_rejects)
    with pytest.raises(RuntimeError):
        oauth.handle_callback(f"http://localhost:8000/cb?state={state}&code=forged", state)
    assert not oauth.is_connected() and oauth.connection()["status"] == "disconnected"


def test_restart_and_reconnect(clean, monkeypatch):
    oauth.save_client(CID, "GOCSPX-secretvalue123")
    url = oauth.authorization_url()
    state = url.split("state=")[1].split("&")[0]
    importlib.reload(oauth)                            # simulate app restart between redirect and callback
    monkeypatch.setattr(oauth, "_exchange_code", lambda flow, resp: _creds())
    monkeypatch.setattr(oauth, "_fetch_channel", lambda c: {"id": "UC1", "title": "C", "thumbnail": None})
    oauth.handle_callback(f"http://localhost:8000/cb?state={state}&code=z", state)
    importlib.reload(oauth)                            # restart again: saved connection is reused, no login
    assert oauth.is_connected()
    assert oauth.load().refresh_token == "rt1"


def test_disconnect(clean, monkeypatch):
    _connect(monkeypatch)
    monkeypatch.setattr("requests.post", lambda *a, **k: None)
    oauth.disconnect()
    assert not oauth.is_connected() and secure.get(oauth.token_key()) is None


# ================================================================== scheduler
def _slot(label, kind, t, days="mon,tue,wed,thu,fri,sat,sun"):
    return scheduling.create_slot(label, kind, t, days)


def test_ist_scheduling(clean):
    t = scheduling.local_to_utc(dt.date(2026, 11, 20), "09:00")
    assert scheduling.iso(t) == "2026-11-20T03:30:00Z"           # IST = UTC+5:30
    _slot("S1", "short", "20:30")
    occ = scheduling.occurrences(dt.datetime(2026, 11, 20, 0, 0, tzinfo=UTC), dt.datetime(2026, 11, 21, 0, 0, tzinfo=UTC))
    assert [scheduling.iso(o["at"]) for o in occ] == ["2026-11-20T15:00:00Z"]


def test_slot_validation(clean):
    _slot("S1", "short", "09:00")
    with pytest.raises(scheduling.ScheduleError):
        _slot("S2", "long", "09:00")                               # same time, overlapping days
    with pytest.raises(scheduling.ScheduleError):
        _slot("S3", "long", "09:00", "sat")                        # still overlaps (S1 runs daily)


def test_slot_same_time_rules(clean):
    _slot("S1", "short", "09:00", "mon")
    _slot("S2", "short", "09:00", "tue")                           # different days: allowed
    with pytest.raises(scheduling.ScheduleError):
        _slot("bad", "short", "9am")
    db.set_setting("allow_same_time", True)
    _slot("S4", "long", "09:00", "mon")                            # explicitly allowed


def test_planner_persistence_and_idempotence(clean, no_queue):
    for i, t in enumerate(["09:00", "13:00", "18:00"]):
        _slot(f"Short {i + 1}", "short", t)
    _slot("Long", "long", "20:30")
    now = scheduling.local_to_utc(dt.date(2026, 11, 20), "00:30")
    sug = _topics()
    ids = scheduling.plan(now, suggest=sug)
    rows = db.query("SELECT kind, scheduled_for, slot_date, status FROM videos ORDER BY scheduled_for")
    day = [r for r in rows if r["slot_date"] == "2026-11-20"]
    assert [r["scheduled_for"] for r in day] == ["2026-11-20T03:30:00Z", "2026-11-20T07:30:00Z",
                                                 "2026-11-20T12:30:00Z", "2026-11-20T15:00:00Z"]
    assert all(r["status"] == "DRAFT" for r in rows) and len(no_queue) == len(ids)
    assert scheduling.plan(now, suggest=sug) == []   # idempotent
    # "restart": queue memory is gone, recovery re-queues everything from the database
    no_queue.clear()
    pipeline._queued.clear()
    rec = pipeline.recover()
    assert rec["requeued"] == len(ids) and sorted(no_queue) == sorted(ids)


def test_daily_limits(clean, no_queue):
    for i, t in enumerate(["09:00", "13:00", "18:00"]):
        _slot(f"Short {i + 1}", "short", t)
    _slot("Long", "long", "20:30")
    db.set_setting("shorts_per_day", 2)
    db.set_setting("max_daily_videos", 2)
    now = scheduling.local_to_utc(dt.date(2026, 11, 20), "00:30")
    scheduling.plan(now, suggest=_topics())
    c = scheduling.daily_counts("2026-11-20")
    assert c["short"] == 2 and c["total"] == 2 and c["long"] == 0   # total cap wins over the long slot


def test_paused_slot_and_global_pause(clean, no_queue):
    s = _slot("Short 1", "short", "09:00")
    scheduling.update_slot(s["id"], paused=True)
    now = scheduling.local_to_utc(dt.date(2026, 11, 20), "00:30")
    assert scheduling.plan(now, suggest=lambda n: ["x unique topic"] * n) == []
    scheduling.update_slot(s["id"], paused=False)
    db.set_setting("automation_paused", True)
    assert scheduling.plan(now, suggest=lambda n: ["y unique topic"] * n) == []


def _ready_video(when: dt.datetime, kind="short", status="READY") -> int:
    cid = db.one("SELECT id FROM channels")["id"]
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status, stage, scheduled_for, title, description, "
                     "tags, video_path) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, kind, f"topic {when}", f"key {when}", status, "done", scheduling.iso(when), "T", "D", "[]", "x.mp4"))
    return vid


def _mock_upload(monkeypatch, calls, fail_times=0, publish_at=None):
    state = {"n": 0}

    def fake(video):
        state["n"] += 1
        calls.append(video["id"])
        if state["n"] <= fail_times:
            raise yt.YouTubeError("upload failed: HTTP 503 backendError")
        return {"youtube_video_id": f"yt{video['id']}x{state['n']}", "privacy": "private", "publish_at": publish_at,
                "upload_status": "uploaded", "requested_privacy": "private", "notes": [], "confirmed": True}
    monkeypatch.setattr(yt, "upload", fake)
    monkeypatch.setattr(oauth, "is_connected", lambda *a, **k: True)


def test_automatic_publishing(clean, monkeypatch):
    db.set_setting("scheduled_publishing", False)        # immediate path: upload at the slot time
    calls = []
    _mock_upload(monkeypatch, calls)
    now = dt.datetime(2026, 11, 20, 4, 0, tzinfo=UTC)
    due = _ready_video(now - dt.timedelta(minutes=30))
    later = _ready_video(now + dt.timedelta(hours=3))
    assert scheduling.dispatch(now) == [due]
    v = db.get_video(due)
    assert v["status"] == "PUBLISHED" and v["youtube_url"] == f"https://youtu.be/yt{due}x1"
    assert db.one("SELECT status FROM uploads WHERE video_id=?", (due,))["status"] == "confirmed"
    assert db.get_video(later)["status"] == "READY"                 # not due yet


def test_public_scheduling_uses_publish_at(clean, monkeypatch):
    when = dt.datetime.now(UTC) + dt.timedelta(hours=5)
    plan = yt.plan_privacy({"target_privacy": "public", "scheduled_for": scheduling.iso(when)})
    assert plan["status"] == {"privacyStatus": "private", "publishAt": scheduling.iso(when)} and plan["scheduled"]
    # dispatcher uploads early for scheduled public publishing; confirmed publishAt -> SCHEDULED
    calls = []
    _mock_upload(monkeypatch, calls, publish_at=scheduling.iso(when))
    vid = _ready_video(when)
    db.update_video(vid, target_privacy="public")
    scheduling.dispatch(dt.datetime.now(UTC))
    assert db.get_video(vid)["status"] == "SCHEDULED"


def test_manual_approval_mode(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls)
    db.set_setting("upload_mode", "approval")
    db.set_setting("slot_auto_publish", False)      # user switched "Scheduled videos upload without approval" off
    assert pipeline.post_qc_status({"quality_json": {"score": 95, "blockers": []}}) == "WAITING_FOR_APPROVAL"
    now = dt.datetime(2026, 11, 20, 4, 0, tzinfo=UTC)
    vid = _ready_video(now - dt.timedelta(minutes=5), status="WAITING_FOR_APPROVAL")
    assert scheduling.dispatch(now) == [] and calls == []
    from app.main import approve
    approve(vid)
    assert db.get_video(vid)["status"] == "READY"
    scheduling.dispatch(now)
    assert db.get_video(vid)["status"] == "PUBLISHED" and calls == [vid]


def test_blocked_content_never_auto_publishes(clean):
    db.set_setting("upload_mode", "automatic")
    assert pipeline.post_qc_status({"quality_json": {"score": 95, "blockers": ["fact-check score 20 < 60"]}}) == "WAITING_FOR_APPROVAL"
    assert pipeline.post_qc_status({"quality_json": {"score": 95, "blockers": []}}) == "READY"


def test_manual_mode_never_auto_uploads(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls)
    db.set_setting("upload_mode", "manual")
    _ready_video(dt.datetime(2026, 11, 20, 3, 0, tzinfo=UTC))
    assert scheduling.dispatch(dt.datetime(2026, 11, 20, 4, 0, tzinfo=UTC)) == [] and calls == []


def test_duplicate_publishing_prevented(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls)
    now = dt.datetime(2026, 11, 20, 4, 0, tzinfo=UTC)
    vid = _ready_video(now - dt.timedelta(minutes=1))
    scheduling.dispatch(now)
    scheduling.dispatch(now)
    with pytest.raises(RuntimeError):
        pipeline.do_upload(vid)
    assert calls == [vid]
    # database-level guards
    import sqlite3
    other = _ready_video(now + dt.timedelta(hours=1))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE videos SET youtube_video_id=? WHERE id=?", (f"yt{vid}x1", other))
    db.execute("UPDATE videos SET slot_id=1, slot_date='2026-11-20' WHERE id=?", (vid,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE videos SET slot_id=1, slot_date='2026-11-20' WHERE id=?", (other,))


def test_failed_upload_retry_with_backoff(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls, fail_times=1)
    now = dt.datetime.now(UTC)
    vid = _ready_video(now - dt.timedelta(minutes=1))
    scheduling.dispatch(now)
    v = db.get_video(vid)
    assert v["status"] == "READY" and v["upload_attempts"] == 1 and v["next_retry_at"]
    assert scheduling.dispatch(now) == []                          # backoff respected
    later = scheduling.parse(v["next_retry_at"]) + dt.timedelta(seconds=1)
    scheduling.dispatch(later)
    assert db.get_video(vid)["status"] == "PUBLISHED" and len(calls) == 2


def test_upload_gives_up_after_limit(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls, fail_times=99)
    db.set_setting("upload_retry_count", 2)
    vid = _ready_video(dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    t = dt.datetime.now(UTC)
    for _ in range(3):
        scheduling.dispatch(t)
        t += dt.timedelta(hours=4)
    v = db.get_video(vid)
    assert v["status"] == "FAILED" and v["upload_attempts"] == 2 and len(calls) == 2


def test_crash_during_upload_is_not_blindly_retried(clean, no_queue):
    vid = _ready_video(dt.datetime.now(UTC), status="UPLOADING")
    pipeline.recover()
    v = db.get_video(vid)
    assert v["status"] == "FAILED" and "interrupted" in v["error"]


def test_reschedule_rules(clean):
    now = dt.datetime.now(UTC)
    a = _ready_video(now + dt.timedelta(hours=2))
    b = _ready_video(now + dt.timedelta(hours=3))
    with pytest.raises(scheduling.ScheduleError):
        scheduling.reschedule(b, scheduling.iso(now + dt.timedelta(hours=2)))      # clash
    with pytest.raises(scheduling.ScheduleError):
        scheduling.reschedule(b, scheduling.iso(now - dt.timedelta(hours=2)))      # past
    v = scheduling.reschedule(b, scheduling.iso(now + dt.timedelta(hours=5)))
    assert v["rescheduled"] == 1
    db.update_video(a, status="PUBLISHED")
    with pytest.raises(scheduling.ScheduleError):
        scheduling.reschedule(a, scheduling.iso(now + dt.timedelta(hours=9)))


def test_emergency_stop_blocks_dispatch(clean, monkeypatch):
    calls = []
    _mock_upload(monkeypatch, calls)
    _ready_video(dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    db.set_setting("emergency_stop", True)
    assert scheduling.dispatch() == [] and calls == []


def test_today_summary(clean, no_queue):
    _slot("S1", "short", "09:00")
    _slot("L", "long", "20:30")
    t = scheduling.today_summary()
    assert t["short"]["target"] == 1 and t["long"]["target"] == 1 and t["timezone"] == "Asia/Kolkata"


def test_production_starts_one_hour_before_slot(clean, no_queue):
    db.set_setting("production_lead_minutes", 60)
    _slot("Short 1", "short", "12:45")
    day = dt.date(2026, 11, 20)
    sug = _topics()
    # 61 minutes before the slot: too early, nothing is produced yet
    assert scheduling.plan(scheduling.local_to_utc(day, "11:44"), suggest=sug) == []
    # 60 minutes before: production starts
    ids = scheduling.plan(scheduling.local_to_utc(day, "11:45"), suggest=sug)
    assert len(ids) == 1 and db.get_video(ids[0])["scheduled_for"] == "2026-11-20T07:15:00Z"
    # later planner runs inside the window don't duplicate it
    assert scheduling.plan(scheduling.local_to_utc(day, "12:30"), suggest=sug) == []


def test_slot_videos_jump_ahead_of_unscheduled_jobs(clean, monkeypatch):
    import queue as _queue
    monkeypatch.setattr(pipeline, "_q", _queue.PriorityQueue())
    monkeypatch.setattr(pipeline, "_queued", set())
    ch = db.one("SELECT id FROM channels")["id"]
    manual = [pipeline.create_video(ch, "short", t) for t in NAMES[:2]]
    now = pipeline.create_video(ch, "short", NAMES[2], upload_immediately=True)     # "make it now"
    late = pipeline.create_video(ch, "short", NAMES[3], scheduled_for="2026-11-20T12:30:00Z")
    early = pipeline.create_video(ch, "short", NAMES[4], scheduled_for="2026-11-20T07:15:00Z")
    for v in manual + [now, late, early]:
        pipeline.enqueue(v)
    order = [pipeline._q.get()[2] for _ in range(5)]
    assert order == [early, late, now] + manual      # slots first, then make-it-now, then background jobs


def test_slot_videos_skip_approval(clean):
    ch = db.one("SELECT id FROM channels")["id"]
    slot = _slot("Short 1", "short", "12:45")["id"]
    flagged = {"score": 40, "blockers": ["fact-check score 50 < 60"]}
    db.set_setting("upload_mode", "approval")
    v = {"slot_id": slot, "quality_json": flagged}
    assert pipeline.post_qc_status(v) == "READY"                       # scheduler video: no human gate
    assert pipeline.post_qc_status({"quality_json": flagged}) == "WAITING_FOR_APPROVAL"   # manual video: still gated
    db.set_setting("slot_auto_publish", False)
    assert pipeline.post_qc_status(v) == "WAITING_FOR_APPROVAL"
    db.set_setting("slot_auto_publish", True)
    # a slot video already held for review is released by the dispatcher and uploaded at its slot
    vid = pipeline.create_video(ch, "short", NAMES[5], scheduled_for="2026-11-20T07:15:00Z", slot_id=slot,
                                slot_date="2026-11-20")
    db.update_video(vid, status="WAITING_FOR_APPROVAL")
    import unittest.mock as m
    with m.patch.object(oauth, "connected_profiles", return_value=[ch]), \
         m.patch.object(pipeline, "do_upload") as up:
        db.set_setting("upload_mode", "automatic")
        assert scheduling.dispatch(dt.datetime(2026, 11, 20, 7, 16, tzinfo=UTC)) == [vid]
        up.assert_called_once_with(vid)
