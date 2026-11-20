"""Public publishing, Publish Now, thumbnails, Short/Long validation - against a fake YouTube service that returns
Google-shaped responses and errors. Nothing is uploaded; no network is used."""
import datetime as dt
import json
from pathlib import Path

import httplib2
import pytest
from googleapiclient.errors import HttpError
from PIL import Image

from app import db, pipeline, scheduling, secure
from app.media import formats
from app.youtube import api as yt
from app.youtube import oauth

UTC = dt.timezone.utc


def http_error(status, reason, message):
    return HttpError(httplib2.Response({"status": status}),
                     json.dumps({"error": {"code": status, "message": message,
                                           "errors": [{"reason": reason, "message": message}]}}).encode())


class FakeYT:
    """Minimal fake of the googleapiclient YouTube resource."""

    def __init__(self, privacy="private", lock_private=False, thumb_error=None, update_error=None):
        self.videos_db = {}
        self.calls = []
        self.default_privacy = privacy
        self.lock_private = lock_private        # simulate an unaudited API project
        self.thumb_error = thumb_error
        self.update_error = update_error

    # resource accessors
    def videos(self):
        return self

    def thumbnails(self):
        return _Thumbs(self)

    def list(self, part, id):
        self.calls.append(("list", id))
        items = [{"id": i, "status": dict(self.videos_db[i])} for i in id.split(",") if i in self.videos_db]
        return _Exec({"items": items})

    def update(self, part, body):
        self.calls.append(("update", body))
        if self.update_error:
            raise self.update_error
        st = self.videos_db[body["id"]]
        if not self.lock_private:
            st.update(body["status"])
            st.pop("publishAt", None) if "publishAt" not in body["status"] else None
        return _Exec({"id": body["id"], "status": st})


class _Exec:
    def __init__(self, r):
        self.r = r

    def execute(self):
        return self.r


class _Thumbs:
    def __init__(self, fake):
        self.fake = fake

    def set(self, videoId, media_body):
        self.fake.calls.append(("thumbnails.set", videoId))
        if getattr(self.fake, "thumb_errors", None):
            raise self.fake.thumb_errors.pop(0)
        if self.fake.thumb_error:
            raise self.fake.thumb_error
        return _Exec({"items": [{"default": {"url": "https://i.ytimg.com/x.jpg"}}]})


@pytest.fixture
def env(monkeypatch, tmp_path):
    db.execute("DELETE FROM uploads")
    db.execute("DELETE FROM videos")
    for k, v in {"default_privacy": "public", "scheduled_publishing": True, "allow_private_uploads": False,
                 "allow_unlisted_uploads": False, "emergency_stop": False, "upload_mode": "automatic"}.items():
        db.set_setting(k, v)
    if not db.one("SELECT id FROM channels"):
        db.execute("INSERT INTO channels(name, niche) VALUES ('T','history')")
    secure.put(oauth.token_key(), {"token": "t", "refresh_token": "r", "scopes": oauth.SCOPES})
    fake = FakeYT()
    monkeypatch.setattr(yt, "_yt", lambda *a, **k: fake)
    monkeypatch.setattr(formats, "validate", lambda p, k: {"ok": True, "errors": [], "warnings": [], "info": {}, "kind": k})

    def fake_insert(service, body, path, notify):
        vid = f"YT{len(fake.videos_db) + 1}"
        st = dict(body["status"])
        if fake.lock_private:
            st["privacyStatus"] = "private"
            st.pop("publishAt", None)
        st["uploadStatus"] = "uploaded"
        fake.videos_db[vid] = st
        fake.calls.append(("insert", body))
        return {"id": vid}
    monkeypatch.setattr(yt, "_insert", fake_insert)
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (1280, 720), (200, 30, 30)).save(thumb, "JPEG")
    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"0" * 2000)
    return fake, str(thumb), str(video_file)


def _video(env, kind="short", status="READY", **kw):
    fake, thumb, vfile = env
    cid = db.one("SELECT id FROM channels")["id"]
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, status, stage, title, description, tags, "
                     "video_path, thumbnail_path, target_privacy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, kind, f"t{kind}{status}", f"k{kind}{status}", status, "done", "Title", "Desc", "[]",
                      vfile, thumb, kw.get("target_privacy", "public")))
    if kw.get("scheduled_for"):
        db.update_video(vid, scheduled_for=kw["scheduled_for"])
    return vid


# ------------------------------------------------------------------ privacy rules
def test_privacy_rules(env):
    future = scheduling.iso(dt.datetime.now(UTC) + dt.timedelta(hours=3))
    assert yt.plan_privacy({})["status"] == {"privacyStatus": "public"}                     # default public
    p = yt.plan_privacy({"scheduled_for": future})
    assert p["status"] == {"privacyStatus": "private", "publishAt": future} and p["scheduled"]
    db.set_setting("scheduled_publishing", False)
    assert yt.plan_privacy({"scheduled_for": future})["status"] == {"privacyStatus": "public"}
    p = yt.plan_privacy({"target_privacy": "private"})                                        # not allowed
    assert p["status"] == {"privacyStatus": "public"} and p["note"]
    db.set_setting("allow_private_uploads", True)
    assert yt.plan_privacy({"target_privacy": "private"})["status"] == {"privacyStatus": "private"}
    db.set_setting("allow_unlisted_uploads", True)
    assert yt.plan_privacy({"target_privacy": "unlisted"})["status"] == {"privacyStatus": "unlisted"}


# ------------------------------------------------------------------ uploads
def test_public_immediate_upload(env):
    fake = env[0]
    vid = _video(env)
    res = pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert res["privacy"] == "public" and v["status"] == "PUBLISHED" and v["yt_privacy"] == "public"
    insert = next(c[1] for c in fake.calls if c[0] == "insert")
    assert insert["status"]["privacyStatus"] == "public" and "publishAt" not in insert["status"]
    assert "#Shorts" in insert["snippet"]["description"]


def test_scheduled_public_upload_and_verification(env):
    fake = env[0]
    when = dt.datetime.now(UTC) + dt.timedelta(hours=4)
    vid = _video(env, scheduled_for=scheduling.iso(when))
    pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert v["status"] == "SCHEDULED" and v["yt_privacy"] == "private" and v["yt_publish_at"] == scheduling.iso(when)
    # YouTube publishes at publishAt -> verification job promotes it only after YouTube reports public
    pipeline.refresh_youtube_status([vid])
    assert db.get_video(vid)["status"] == "SCHEDULED"
    fake.videos_db[v["youtube_video_id"]].update(privacyStatus="public")
    fake.videos_db[v["youtube_video_id"]].pop("publishAt")
    r = pipeline.refresh_youtube_status([vid])
    assert r["published"] == 1 and db.get_video(vid)["status"] == "PUBLISHED" and db.get_video(vid)["yt_privacy"] == "public"


def test_unaudited_project_lock_is_reported_not_hidden(env):
    fake = env[0]
    fake.lock_private = True
    vid = _video(env)
    res = pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert res["privacy_mismatch"] and v["yt_privacy"] == "private"
    assert "compliance audit" in v["error"]


# ------------------------------------------------------------------ thumbnails
def test_thumbnail_set(env):
    vid = _video(env)
    pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert v["thumbnail_status"] == "set" and v["thumbnail_error"] is None


def test_thumbnail_403_does_not_fail_upload(env):
    fake = env[0]
    fake.thumb_error = http_error(403, "forbidden", "The authenticated user doesn't have permissions to upload and set custom video thumbnails.")
    vid = _video(env)
    pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert v["status"] == "PUBLISHED"                                  # publishing continued
    assert v["thumbnail_status"] == "unavailable"
    assert "HTTP 403 forbidden" in v["thumbnail_error"] and "permissions" in v["thumbnail_error"]
    # retry after channel verification succeeds
    fake.thumb_error = None
    assert pipeline.retry_thumbnail(vid)["status"] == "set"
    assert db.get_video(vid)["thumbnail_status"] == "set"


def test_thumbnail_retries_until_video_is_visible(env, monkeypatch):
    """YouTube answers 404 videoNotFound for a just-inserted video; the thumbnail must still end up set."""
    fake = env[0]
    slept = []
    monkeypatch.setattr(yt.time, "sleep", lambda s: slept.append(s))
    nf = "The video that you are trying to insert a thumbnail image for cannot be found."
    fake.thumb_errors = [http_error(404, "videoNotFound", nf), http_error(404, "videoNotFound", nf),
                         http_error(503, "backendError", "Backend Error")]
    vid = _video(env)
    pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert v["thumbnail_status"] == "set" and v["thumbnail_error"] is None
    assert sum(1 for c in fake.calls if c[0] == "thumbnails.set") == 4 and len(slept) == 3


def test_thumbnail_404_gives_up_after_retries(env, monkeypatch):
    fake = env[0]
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    fake.thumb_error = http_error(404, "videoNotFound", "cannot be found")
    vid = _video(env)
    pipeline.do_upload(vid)
    v = db.get_video(vid)
    assert v["status"] == "PUBLISHED" and v["thumbnail_status"] == "failed"
    assert sum(1 for c in fake.calls if c[0] == "thumbnails.set") == len(yt.THUMB_RETRY_DELAYS) + 1


def test_thumbnail_403_is_not_retried(env, monkeypatch):
    fake = env[0]
    monkeypatch.setattr(yt.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait on 403")))
    fake.thumb_error = http_error(403, "forbidden", "no permission")
    vid = _video(env)
    pipeline.do_upload(vid)
    assert sum(1 for c in fake.calls if c[0] == "thumbnails.set") == 1


def test_thumbnail_validation(tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGB", (500, 500)).save(big, "PNG")
    problems = yt.validate_thumbnail(big)
    assert any("width" in p for p in problems) and any("16:9" in p for p in problems)
    gif = tmp_path / "a.gif"
    Image.new("RGB", (1280, 720)).save(gif, "GIF")
    assert any("format" in p for p in yt.validate_thumbnail(gif))
    ok = tmp_path / "ok.jpg"
    Image.new("RGB", (1280, 720)).save(ok, "JPEG")
    assert yt.validate_thumbnail(ok) == []


# ------------------------------------------------------------------ publish now
def test_publish_now_updates_existing_video(env):
    fake = env[0]
    vid = _video(env)
    db.set_setting("allow_private_uploads", True)
    db.update_video(vid, target_privacy="private")
    pipeline.do_upload(vid)
    ytid = db.get_video(vid)["youtube_video_id"]
    assert fake.videos_db[ytid]["privacyStatus"] == "private"
    fake.videos_db[ytid]["license"] = "youtube"
    inserts_before = sum(1 for c in fake.calls if c[0] == "insert")
    res = pipeline.publish_now(vid)
    assert res["privacy"] == "public" and res["changed"]
    upd = next(c[1] for c in fake.calls if c[0] == "update")
    assert upd["id"] == ytid and upd["status"]["privacyStatus"] == "public"
    assert upd["status"]["license"] == "youtube" and upd["status"]["containsSyntheticMedia"] is True   # preserved
    assert sum(1 for c in fake.calls if c[0] == "insert") == inserts_before                      # no re-upload
    v = db.get_video(vid)
    assert v["yt_privacy"] == "public" and v["status"] == "PUBLISHED"


def test_publish_now_requires_manage_scope(env):
    vid = _video(env)
    pipeline.do_upload(vid)
    secure.put(oauth.token_key(), {"token": "t", "refresh_token": "r", "scopes": oauth.SCOPES[:3]})
    with pytest.raises(yt.YouTubeError, match="Reconnect"):
        pipeline.publish_now(vid)


def test_publish_now_reports_lock_honestly(env):
    fake = env[0]
    db.set_setting("allow_private_uploads", True)
    vid = _video(env, target_privacy="private")
    pipeline.do_upload(vid)
    fake.lock_private = True
    with pytest.raises(yt.YouTubeError, match="still private"):
        pipeline.publish_now(vid)
    assert db.get_video(vid)["yt_privacy"] == "private"                    # not marked public


def test_publish_now_google_error(env):
    fake = env[0]
    db.set_setting("allow_private_uploads", True)
    vid = _video(env, target_privacy="private")
    pipeline.do_upload(vid)
    fake.update_error = http_error(403, "forbidden", "Request had insufficient authentication scopes.")
    with pytest.raises(yt.YouTubeError, match="insufficient authentication scopes"):
        pipeline.publish_now(vid)


# ------------------------------------------------------------------ format gate
def test_wrong_format_rejected_before_upload(env, monkeypatch):
    fake = env[0]
    monkeypatch.setattr(formats, "validate", lambda p, k: {"ok": False, "errors": ["a Short must be portrait (height > width)"],
                                                            "warnings": [], "info": {}, "kind": k})
    vid = _video(env)
    with pytest.raises(Exception, match="not a valid short"):
        pipeline.do_upload(vid)
    assert not any(c[0] == "insert" for c in fake.calls)
