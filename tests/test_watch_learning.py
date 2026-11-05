"""v0.10 watch-driven learning: no model downloads and no YouTube access."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as localtube
import watch_learning as watch
from recommender import build_profile

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


class FakeEncoder:
    def embed(self, texts):
        for title in texts:
            low = title.lower()
            index = (
                0 if any(s in low for s in ("thinking", "speaking", "reasoning"))
                else 1 if any(s in low for s in ("horror", "gameplay", "scary"))
                else 2
            )
            vec = [0.0] * 384
            vec[index] = 1.0
            yield vec


def sample_video(vid="dQw4w9WgXcQ", title="Thinking better each day",
                 rating=0, played=0):
    return {
        "id": vid, "title": title,
        "rating": rating, "plays": played,
        "tags": ["knowledge"], "channel": "Creator",
        "watched_seconds": 0.0, "duration_seconds": 300.0,
        "completions": 0,
        "last_activity_at": NOW.isoformat(),
    }


def session(video, watched, duration=300, completed=False, ident="session0000000001"):
    return {
        "session_id": ident, "video_id": video["id"],
        "watched_seconds": watched,
        "duration_seconds": duration,
        "completed": int(completed),
    }


def test_click_only_and_brief_exits_are_neutral():
    v = sample_video(played=9)
    assert watch.watch_weight(v, []) == 0
    assert watch.watch_weight(v, [session(v, 3)]) == 0
    assert watch.watch_weight(v, [session(v, 29)]) == 0  # 10% of 300 = 30s
    assert watch.watch_weight(v, [session(v, 40)]) > 0
    assert watch.watch_weight(v, [session(v, 120)]) > watch.watch_weight(v, [session(v, 40)])
    assert watch.watch_weight(v, [session(v, 120), session(v, 100, ident="session0000000002")]) > watch.watch_weight(v, [session(v, 120)])
    disliked = {**v, "rating": -1}
    assert watch.watch_weight(disliked, [session(v, 240)]) == -1.0
    liked = {**v, "rating": 1}
    assert watch.watch_weight(liked, []) > 0


def test_long_video_watching_is_not_rejected_for_low_completion():
    long = sample_video()
    assert watch.meaningful_threshold(7200) == 180
    assert watch.watch_weight(long, [session(long, 120, duration=7200)]) == 0
    assert watch.watch_weight(long, [session(long, 600, duration=7200)]) > 0


def test_weak_play_clicks_do_not_steer_watch_mode_profile():
    weak = sample_video(played=50)
    p = watch.watch_profile_library([weak], [session(weak, 2)])
    assert p[0]["plays"] == 0
    assert p[0]["tags"] == []
    profile = build_profile([], p)
    assert "knowledge" not in profile["tags"]
    strong = watch.watch_profile_library([weak], [session(weak, 160)])
    assert "knowledge" in build_profile([], strong)["tags"]


def test_watch_features_cluster_and_dislike_without_handcoded_topics(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "vectors.db")
    localtube.init_db()
    a = sample_video(title="Thinking better")
    b = sample_video("jNQXAC9IVRw", "Horror gameplay walkthrough")
    c = sample_video("9bZkp7q19f0", "Soccer game highlights", rating=-1)
    library = [a, b, c]
    sessions = [session(a, 155), session(b, 380, duration=1600, ident="session0000000002")]
    batches = [("knowledge", [
        {"id": "aaaaaaaaaaa", "title": "Reasoning exercises", "channel": "A"},
        {"id": "bbbbbbbbbbb", "title": "Scary horror gameplay", "channel": "B"},
        {"id": "ccccccccccc", "title": "Soccer game highlights 2026", "channel": "C"},
    ])]
    values, info = watch.watch_features(
        batches, library, sessions, localtube.connect, model=FakeEncoder(), now=NOW)
    assert info["groups"] == 2
    assert info["meaningful_sessions"] == 2
    assert info["positive_examples"] == 2
    assert info["negative_examples"] == 1
    assert values["aaaaaaaaaaa"]["semantic_positive"] > 1
    assert values["bbbbbbbbbbb"]["semantic_positive"] > 1
    assert values["ccccccccccc"]["semantic_negative"] < 0


def test_watch_session_progress_deduplicates_and_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "sessions.db")
    with TestClient(localtube.app) as client:
        vid = "dQw4w9WgXcQ"
        assert client.post("/api/videos", json={"url": vid, "title": "Thinking better",
                                                 "tags": "knowledge"}).status_code == 201
        sid = "firstwatchsession000001"
        start = client.post(f"/api/videos/{vid}/play", json={"session_id": sid})
        assert start.status_code == 200
        assert client.post(f"/api/videos/{vid}/play", json={"session_id": sid}).json()["plays"] == 1
        payload = {"session_id": sid, "sequence": 1,
                   "position_seconds": 8, "duration_seconds": 100,
                   "watched_delta": 8, "ended": False}
        p1 = client.put(f"/api/videos/{vid}/progress", json=payload)
        assert p1.status_code == 200
        assert p1.json()["watched_seconds"] == pytest.approx(8)
        assert client.put(f"/api/videos/{vid}/progress", json=payload).json()["watched_seconds"] == pytest.approx(8)
        later = {**payload, "sequence": 2, "position_seconds": 18, "watched_delta": 10}
        assert client.put(f"/api/videos/{vid}/progress", json=later).json()["watched_seconds"] == pytest.approx(18)
        stale = {**payload, "sequence": 1, "position_seconds": 5}
        assert client.put(f"/api/videos/{vid}/progress", json=stale).json()["watched_seconds"] == pytest.approx(18)
        # Seeking to the end after just 18 seconds must NOT count as completed.
        early_end = {**payload, "sequence": 3, "position_seconds": 100,
                     "watched_delta": 0, "ended": True}
        data = client.put(f"/api/videos/{vid}/progress", json=early_end).json()
        assert data["completions"] == 0
        # A new real viewing session at >= meaningful threshold can complete.
        sid2 = "secondwatchsession00002"
        assert client.post(f"/api/videos/{vid}/play", json={"session_id": sid2}).status_code == 200
        for seq in range(1, 5):
            next_p = {"session_id": sid2, "sequence": seq,
                      "position_seconds": seq * 10, "duration_seconds": 100,
                      "watched_delta": 10, "ended": False}
            assert client.put(f"/api/videos/{vid}/progress", json=next_p).status_code == 200
        done = {"session_id": sid2, "sequence": 5, "position_seconds": 100,
                "duration_seconds": 100, "watched_delta": 3, "ended": True}
        last = client.put(f"/api/videos/{vid}/progress", json=done)
        assert last.json()["completions"] == 1
        assert client.put(f"/api/videos/{vid}/progress", json=done).json()["completions"] == 1
        stats = client.get("/api/watch-learning/stats").json()
        assert stats["sessions"] == 2
        assert stats["meaningful_sessions"] == 1
        assert stats["positive_examples"] >= 1
        with localtube.connect() as db:
            sessions = db.execute("SELECT * FROM watch_sessions").fetchall()
        assert len(sessions) == 2
        assert sum(row["watched_seconds"] for row in sessions) == pytest.approx(61)
        assert client.delete("/api/watch-learning/sessions").status_code == 204
        assert client.get("/api/watch-learning/stats").json()["sessions"] == 0
        # Reset sessions doesn't delete existing watch history or likes.
        assert client.get("/api/videos").json()[0]["watched_seconds"] == pytest.approx(61)


def test_progress_validation_and_session_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "validation.db")
    with TestClient(localtube.app) as client:
        first, second = "dQw4w9WgXcQ", "jNQXAC9IVRw"
        for vid in (first, second):
            assert client.post("/api/videos", json={"url": vid, "title": "Sample"}).status_code == 201
        sid = "validationSession0000001"
        client.post(f"/api/videos/{first}/play", json={"session_id": sid})
        assert client.post(f"/api/videos/{second}/play", json={"session_id": sid}).status_code == 409
        payload = {"session_id": sid, "sequence": 1, "watched_delta": 5,
                   "position_seconds": 5, "duration_seconds": 200}
        assert client.put(f"/api/videos/{second}/progress", json=payload).status_code == 409
        assert client.put(f"/api/videos/{first}/progress", json={
            "session_id": sid, "watched_delta": 4, "position_seconds": 5,
            "duration_seconds": 200}).status_code == 422
        assert client.put(f"/api/videos/{first}/progress", json={
            **payload, "session_id": "unknownSession00000001"}).status_code == 404
        assert client.put(f"/api/videos/{first}/progress", json={
            **payload, "watched_delta": 99}).status_code == 422
        assert client.delete(f"/api/videos/{first}").status_code == 204
        with localtube.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM watch_sessions").fetchone()[0] == 0


def test_original_progress_endpoint_still_supports_legacy_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "legacy.db")
    with TestClient(localtube.app) as client:
        vid = "dQw4w9WgXcQ"
        client.post("/api/videos", json={"url": vid, "title": "Legacy"})
        record = client.put(f"/api/videos/{vid}/progress", json={
            "position_seconds": 9, "duration_seconds": 100,
            "watched_delta": 8, "ended": False,
        })
        assert record.status_code == 200
        assert record.json()["watched_seconds"] == pytest.approx(8)
        assert client.get("/api/watch-learning/stats").json()["sessions"] == 0


def test_watch_ranking_uses_local_encoder_without_any_manual_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "feed.db")
    model = FakeEncoder()
    monkeypatch.setattr(localtube, "get_model", lambda: model)
    calls = []
    def fake_search(query, limit=18):
        calls.append(query)
        return [
            {"id": "aaaaaaaaaaa", "title": "Reasoning exercises", "channel": "School"},
            {"id": "bbbbbbbbbbb", "title": "Scary horror gameplay", "channel": "Gaming"},
            {"id": "ccccccccccc", "title": "Soccer highlights", "channel": "Sport"},
        ]
    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        vid = "dQw4w9WgXcQ"
        client.post("/api/videos", json={"url": vid, "title": "Thinking about science",
                                         "tags": "knowledge", "duration_seconds": 500})
        sid = "watchfeedSession0000001"
        client.post(f"/api/videos/{vid}/play", json={"session_id": sid})
        for seq in range(1, 8):
            payload = {"session_id": sid, "sequence": seq,
                       "position_seconds": seq * 10, "duration_seconds": 500,
                       "watched_delta": 10, "ended": False}
            assert client.put(f"/api/videos/{vid}/progress", json=payload).status_code == 200
        feed = client.get("/api/feed?ranker=watch&refine=false")
        assert feed.status_code == 200, feed.text
        data = feed.json()
        assert data["ranker"] == "watch"
        assert data["watch_learning"]["meaningful_sessions"] == 1
        assert data["watch_learning"]["groups"] == 1
        assert data["videos"][0]["id"] == "aaaaaaaaaaa"
        assert "semantic_positive" in data["videos"][0]["score_breakdown"]
        assert data["semantic_expanded"] is False
        other = client.get("/api/feed?ranker=semantic&refine=false")
        assert other.status_code == 200
        assert len(calls) == 1  # Same cached YouTube result pool.
        assert set(v["id"] for v in data["videos"]) == set(v["id"] for v in other.json()["videos"])
        assert "watch_learning" not in (other.json()["watch_learning"] or {})
