"""Growth engine tests: learning from real stats, genre choice, titles, hooks, prompts, comments. No network."""
import datetime as dt
import random
import unittest.mock as m

import pytest

from app import db, growth, pipeline, scheduling
from app.agents import script, seo
from app.media import subtitles

UTC = dt.timezone.utc


@pytest.fixture
def ch():
    db.execute("DELETE FROM analytics_snapshots")
    db.execute("DELETE FROM uploads")
    db.execute("DELETE FROM videos")
    db.execute("DELETE FROM schedule_slots")
    for k, v in {"growth_genres": ["mystery", "history", "space"], "engagement_comment": True,
                 "subscribe_prompt": True, "emergency_stop": False, "timezone": "Asia/Kolkata"}.items():
        db.set_setting(k, v)
    if not db.one("SELECT id FROM channels"):
        db.execute("INSERT INTO channels(name, niche) VALUES ('T','history')")
    return db.one("SELECT id FROM channels ORDER BY id")["id"]


_n = iter(range(10_000))


def _video(cid, genre, views, kind="short", days_ago=5, likes=None, title=None):
    i = next(_n)
    live = (dt.datetime.now(UTC) - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    vid = db.execute("INSERT INTO videos(channel_id, kind, topic, topic_key, title, genre, status, youtube_video_id, published_at, "
                     "yt_privacy) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (cid, kind, f"topic {i}", f"topic{i}", title or f"title {i}", genre, "PUBLISHED", f"yt{i}", live, "public"))
    db.execute("INSERT INTO analytics_snapshots(youtube_video_id, views, likes, comments) VALUES (?,?,?,0)",
               (f"yt{i}", views, likes if likes is not None else views // 20))
    return vid


def test_genre_report_uses_views_per_day(ch):
    _video(ch, "mystery", 1000, days_ago=10)        # 100/day
    _video(ch, "mystery", 3000, days_ago=10)        # 300/day
    _video(ch, "history", 500, days_ago=1)          # 500/day
    rep = {r["genre"]: r for r in growth.genre_report(ch, "short")}
    assert rep["mystery"]["avg_views_per_day"] == 200 and rep["mystery"]["videos_with_stats"] == 2
    assert rep["history"]["avg_views_per_day"] == 500


def test_pick_genre_tests_untested_then_exploits_winner(ch):
    # nothing known: every candidate gets tested, least-made first
    assert growth.pick_genre(ch, "short", random.Random(1)) in {"mystery", "history", "space"}
    for g, views in (("mystery", 9000), ("history", 300), ("space", 200)):
        _video(ch, g, views)
        _video(ch, g, views)
    picks = [growth.pick_genre(ch, "short", random.Random(s)) for s in range(300)]
    assert picks.count("mystery") > 0.8 * len(picks)            # clear winner gets most slots
    assert set(picks) <= {"mystery", "history", "space"}        # only the user's chosen genres


def test_top_examples_need_enough_data(ch):
    _video(ch, "mystery", 50, title="low")
    assert growth.top_examples(ch, "short") == []
    _video(ch, "mystery", 5000, title="winner")
    _video(ch, "mystery", 100, title="mid")
    _video(ch, "mystery", 10, title="flop")
    assert growth.top_examples(ch, "short", n=2) == ["winner", "mid"]


def test_title_picker_prefers_curiosity_over_weak_titles():
    topic = "Dyatlov Pass incident"
    opts = ["The Story of the Dyatlov Pass Incident", "Dyatlov Pass incident explained",
            "Nine Hikers Fled Their Tent Into -30C. Nobody Knows Why"]
    assert seo.pick_title(opts, topic) == opts[2]
    assert seo.pick_title(["9 Hikers, 39 Years of Silence", "Why Did They Flee Barefoot?"], topic,
                          "In 1959 nine hikers fled") == "Why Did They Flee Barefoot?"     # invented number dropped
    assert seo.title_score("A" * 90, topic) < seo.title_score("Why Nine Hikers Never Came Back", topic)


def test_hook_rules():
    assert script.hook_ok("Nine hikers cut their tent open from the inside and ran.", "Dyatlov Pass")
    assert not script.hook_ok("Did you know nine hikers died in the mountains?", "Dyatlov Pass")
    assert not script.hook_ok("Dyatlov Pass is a mystery that people love.", "Dyatlov Pass")
    assert not script.hook_ok("Too short.", "x")


def test_best_hook_keeps_current_when_alternatives_break_rules():
    bad = {"hooks": ["Did you know this?", "This is crazy"], "ranking": ["Did you know this?"]}
    with m.patch.object(script.llm, "generate_json", return_value=(bad, "test")):
        assert script.best_hook("Topic", "src", "Current strong hook line here now.") == "Current strong hook line here now."
    good = {"hooks": ["Nine hikers tongues were all missing from the bodies.",
                      "Nine hikers ran barefoot into a blizzard at night."], "ranking": []}
    # script.llm and factcheck.llm are the same module: calls are [alternatives, verdict 1, verdict 2]
    with m.patch.object(script.llm, "generate_json", side_effect=[(good, "t"), ({"supported": False}, "t"),
                                                                  ({"supported": True}, "t")]):
        # the false hook is rejected by the strict fact check; the next, true one is used
        assert script.best_hook("Topic", "src", "old hook line that is fine") == good["hooks"][1]
    with m.patch.object(script.llm, "generate_json", side_effect=[(good, "t"), ({"supported": False}, "t"),
                                                                  ({"supported": False}, "t")]):
        assert script.best_hook("Topic", "src", "old hook line that is fine") == "old hook line that is fine"


def test_subscribe_prompt_overlay(ch, tmp_path):
    v = {"kind": "short", "genre": "mystery"}
    t0, t1, text = pipeline.subscribe_prompt(v, 50.0)
    assert 47 < t0 < t1 <= 50 and text == "FOLLOW FOR MORE MYSTERY STORIES"
    long_t0, _, long_text = pipeline.subscribe_prompt({"kind": "long", "genre": None}, 480.0)
    assert long_t0 == 90 and long_text.startswith("SUBSCRIBE")
    ass = subtitles.write_ass([], tmp_path / "c.ass", True, {"subscribe": (t0, t1, text)})
    body = ass.read_text(encoding="utf-8")
    assert "FOLLOW FOR MORE MYSTERY STORIES" in body and r"{\an5\pos(540,300)\fs46\bord3" in body
    db.set_setting("subscribe_prompt", False)
    assert pipeline.subscribe_prompt(v, 50.0) is None


def test_planner_uses_growth_genre_and_bank(ch, monkeypatch):
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: queued.append(vid))
    slot = scheduling.create_slot("S", "short", "12:45")
    occ = [{"slot": slot, "date": "2026-11-20", "at": scheduling.local_to_utc(dt.date(2026, 11, 20), "12:45")}]
    taken = []
    monkeypatch.setattr("app.agents.topics.take", lambda g, c, t: taken.append((g, t)))
    ids = scheduling._plan_by_growth(db.one("SELECT * FROM channels WHERE id=?", (ch,)), occ,
                                     pick=lambda c, k: "space", suggest=lambda g: ["Wow! signal"])
    v = db.get_video(ids[0])
    assert v["genre"] == "space" and v["topic"] == "Wow! signal" and v["scheduled_for"] == "2026-11-20T07:15:00Z"
    assert taken == [("space", "Wow! signal")] and queued == ids


def test_engagement_comment_posted_once(ch):
    a = _video(ch, "mystery", 900, days_ago=1, title="Best one")
    b = _video(ch, "mystery", 10, days_ago=1)
    db.update_video(b, seo_json={"comment": "What do you think happened?"})
    posted = []
    with m.patch("app.youtube.api.post_comment", side_effect=lambda v, t: posted.append((v["id"], t)) or f"c{v['id']}"):
        assert growth.post_engagement_comments() == 2
        assert growth.post_engagement_comments() == 0           # never twice
    texts = dict(posted)
    assert texts[b].startswith("What do you think happened?") and "watch next: Best one" in texts[b]
    assert db.get_video(a)["engagement_comment_id"] == f"c{a}"


def test_engagement_comment_failure_is_capped(ch):
    v = _video(ch, "mystery", 10, days_ago=1)
    with m.patch("app.youtube.api.post_comment", side_effect=RuntimeError("comments disabled")) as p:
        for _ in range(5):
            growth.post_engagement_comments()
    assert p.call_count == 3 and db.get_video(v)["engagement_comment_id"] is None


def test_monetization_tiers(ch):
    stats = {"subscribers": 1200, "total_views": 1, "videos": 3, "long_watch_hours_365d": 100.0,
             "shorts_views_90d": 3_500_000, "analytics_error": None}
    for _ in range(3):
        _video(ch, "mystery", 1, days_ago=2)
    with m.patch("app.youtube.api.monetization_stats", return_value=stats):
        r = growth.monetization(ch)
    assert r["tiers"]["fan_funding"]["eligible"] is True          # 500 subs + 3 uploads + 3M Shorts views
    assert r["tiers"]["ads"]["eligible"] is False                 # needs 10M Shorts views or 4000 h


def test_channel_library_classifies_and_totals(ch):
    from app.youtube import api as yt
    yt._LIBRARY_CACHE.clear()
    mine = _video(ch, "mystery", 5, kind="long")                 # AutoTube record wins over length
    mine_yt = db.get_video(mine)["youtube_video_id"]

    class Req:
        def __init__(self, r): self.r = r
        def execute(self): return self.r

    class YT:
        def channels(self): return self
        def playlistItems(self): return self
        def videos(self): return self

        def list(self, part, **k):
            if "mine" in k:
                return Req({"items": [{"id": "UC1", "snippet": {"title": "Chan"}, "statistics": {"subscriberCount": "60",
                            "viewCount": "100", "videoCount": "3"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
            if "playlistId" in k:
                return Req({"items": [{"contentDetails": {"videoId": i}} for i in ("a", "b", mine_yt)]})
            mk = lambda i, d, v: {"id": i, "snippet": {"title": i}, "statistics": {"viewCount": v, "likeCount": "1",
                                  "commentCount": "2"}, "contentDetails": {"duration": d}, "status": {"privacyStatus": "public"}}
            return Req({"items": [mk("a", "PT45S", "10"), mk("b", "PT8M3S", "20"), mk(mine_yt, "PT50S", "5")]})

    with m.patch.object(yt, "_yt", return_value=YT()):
        r = yt.channel_library(ch)
    kinds = {v["youtube_video_id"]: v["kind"] for v in r["videos"]}
    assert kinds == {"a": "short", "b": "long", mine_yt: "long"}
    assert r["totals"]["short"] == {"uploads": 1, "views": 10, "likes": 1, "comments": 2}
    assert r["totals"]["long"]["uploads"] == 2 and r["channel"]["subscribers"] == 60
    assert yt.iso_duration("PT1H2M3S") == 3723 and yt.iso_duration("PT45S") == 45


def test_trend_patterns_and_make(ch, monkeypatch):
    from app import trends
    p = trends.patterns("Why Nobody Survived 3 Days Here?", 45)
    assert {"number in title", "question", "curiosity words", "Short"} <= set(p)
    monkeypatch.setattr("app.agents.research.verify_topic", lambda t: "Voyager 1")
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: queued.append(vid))
    vids = trends.make(ch, "voyager", ["short", "long"], "schedule", "2030-01-05T18:00", source={"title": "x"})
    assert [v["kind"] for v in vids] == ["short", "long"] and all(v["topic"] == "Voyager 1" for v in vids)
    assert vids[0]["scheduled_for"] == "2030-01-05T12:30:00Z" and vids[1]["scheduled_for"] == "2030-01-05T14:30:00Z"
    assert queued == [v["id"] for v in vids]
    with pytest.raises(ValueError):
        trends.make(ch, "voyager", ["short"], "now")                         # same story again -> duplicate
    with pytest.raises(ValueError):
        trends.make(ch, "voyager", ["short"], "schedule", None)
    # with "upload without approval" on, scheduled (non-slot) videos skip approval like slot videos
    before = db.get_setting("slot_auto_publish")
    db.set_setting("slot_auto_publish", True)
    try:
        assert pipeline.post_qc_status({"scheduled_for": "2030-01-05T12:30:00Z",
                                        "quality_json": {"score": 10, "blockers": ["x"]}}) == "READY"
    finally:
        db.set_setting("slot_auto_publish", before)


def test_trend_tags_skip_show_names(monkeypatch):
    from app import trends
    monkeypatch.setattr(trends.llm, "generate_json", lambda *a, **k: ({"subjects": ["FAA outage"]}, "t"))
    c = trends.topic_candidates({"title": "x", "channel": "The Daily Show",
                                 "tags": ["The Daily Show", "Jon Stewart", "full episode recap", "air traffic control"]})
    assert c == ["FAA outage", "Jon Stewart", "air traffic control"]


def test_gta_aliases_and_preset(ch, monkeypatch):
    from app import trends
    assert trends.resolve_alias("GTA 6") == "Grand Theft Auto VI" and trends.resolve_alias(" gta  vi ") == "Grand Theft Auto VI"
    assert trends.resolve_alias("Voyager 1") == "Voyager 1"
    assert "Grand Theft Auto VI" in trends.PRESETS["gta6"]["topics"]
    seen = []
    monkeypatch.setattr("app.agents.research.verify_topic", lambda t: seen.append(t) or t)
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: None)
    vids = trends.make(ch, "GTA 6", ["short"], "now")
    assert seen == ["Grand Theft Auto VI"] and vids[0]["topic"] == "Grand Theft Auto VI" and vids[0]["upload_immediately"] == 1
    monkeypatch.setattr(trends.llm, "generate_json", lambda *a, **k: ({"subjects": []}, "t"))
    assert trends.topic_candidates({"title": "Is 30 FPS really a problem for GTA 6?", "tags": []})[0] == "Grand Theft Auto VI"


def test_trend_just_make_and_upload_kit(ch, monkeypatch):
    from app import main, trends
    monkeypatch.setattr("app.agents.research.verify_topic", lambda t: "Rockstar Games")
    monkeypatch.setattr(pipeline, "enqueue", lambda vid: None)
    v = trends.make(ch, "rockstar", ["short"], "make")[0]
    assert v["upload_immediately"] == 0 and v["scheduled_for"] is None          # made only, never auto-uploaded
    db.update_video(v["id"], status="READY", stage="done", title="Why Rockstar Waited 12 Years #Shorts",
                    description="desc", tags='["gta", "rockstar"]',
                    seo_json={"hashtags": ["Shorts", "GTA6"], "title_options": ["A", "B"]})
    assert scheduling.dispatch() == []                                          # unscheduled READY stays in library
    kit = next(x for x in main.trends_videos()["videos"] if x["id"] == v["id"])
    assert kit["hashtags"] == ["Shorts", "GTA6"] and kit["tags"] == ["gta", "rockstar"] and kit["description"] == "desc"
    with pytest.raises(ValueError):
        trends.make(ch, "rockstar 2", ["short"], "later")


def test_one_click_make_uses_trend_or_falls_back(ch, monkeypatch):
    import threading
    from app import trends
    made = []
    monkeypatch.setattr(trends, "make", lambda c, t, k, m, w=None, g=None, s=None, sr=False: made.append((t, g, k, m)) or [])
    monkeypatch.setattr(threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    # trend with a real subject -> that subject
    monkeypatch.setattr(trends, "idea", lambda v: {"topic": "Voyager 1", "tried": []})
    assert trends.start(ch, "", ["short"], "now", source={"title": "x"}) == {"started": True}
    # meme trend with no subject -> best genre's top verified story
    monkeypatch.setattr(trends, "idea", lambda v: {"topic": None, "tried": []})
    monkeypatch.setattr(trends, "fallback_topic", lambda c, k: ("Dyatlov Pass incident", "mystery"))
    trends.start(ch, "", ["long"], "make", source={"title": "Unboxing the world's largest toy"})
    # typed topic wins
    trends.start(ch, "Titanic", ["short", "long"], "now")
    assert made == [("Voyager 1", None, ["short"], "now"), ("Dyatlov Pass incident", "mystery", ["long"], "make"),
                    ("Titanic", None, ["short", "long"], "now")]
    # bad choices are rejected at once, before any background work
    with pytest.raises(ValueError):
        trends.start(ch, "", ["short"], "schedule", None)
    with pytest.raises(ValueError):
        trends.start(ch, "", [], "now")
    assert not trends.good_subject("Part") and not trends.good_subject("Unboxing") and trends.good_subject("Titanic")
