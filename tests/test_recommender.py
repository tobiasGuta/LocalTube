"""Recommendation ranking tests with no YouTube network requests."""
from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient

import app as localtube
from recommender import build_profile, rank_feed, topics_to_search, words


NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def video(vid, tag, title, *, plays=0, rating=0, last_activity_at=None):
    return {
        "id": vid, "title": title, "tags": [tag], "plays": plays,
        "rating": rating, "created_at": "2026-11-01T10:00:00+00:00",
        "last_activity_at": last_activity_at,
    }


def test_likes_and_plays_influence_profile_and_queries():
    p = build_profile(["gameplays", "knowledge"], [
        video("a", "gameplays", "Fun playthrough", plays=4),
        video("b", "knowledge", "Trivia about planets", rating=1),
    ], now=NOW)
    assert p["tags"]["knowledge"] > 10
    assert p["tags"]["gameplays"] > 10
    assert topics_to_search(p) == ["knowledge", "gameplays"]
    assert "trivia" in p["terms"]


def test_old_activity_fades_without_being_deleted():
    newer = build_profile([], [
        video("a", "linux", "Kernel hacking", rating=1,
              last_activity_at=NOW.isoformat()),
    ], now=NOW)
    older = build_profile([], [
        video("a", "linux", "Kernel hacking", rating=1,
              last_activity_at=(NOW - timedelta(days=160)).isoformat()),
    ], now=NOW)
    assert 0 < older["tags"]["linux"] < newer["tags"]["linux"]


def test_dislike_does_not_cancel_explicit_interest_and_filters_id():
    p = build_profile(["gameplays"], [
        video("a", "gameplays", "Bad game", rating=-1),
    ], now=NOW)
    assert "gameplays" in topics_to_search(p)
    picks = rank_feed([("gameplays", [
        {"id": "a", "title": "Bad game", "channel": "A"},
        {"id": "b", "title": "Fun game", "channel": "B"},
    ])], p, {"a"})
    assert [item["id"] for item in picks] == ["b"]


def test_diversity_across_topics_and_creators():
    p = build_profile(["gaming", "linux"], [], now=NOW)
    batches = [
        ("gaming", [{"id": str(i).zfill(11), "title": f"Gameplay {i}",
                     "channel": "Same channel"} for i in range(8)]),
        ("linux", [{"id": f"Linux{i:06d}", "title": f"Linux tutorial {i}",
                    "channel": f"Other channel {i}"} for i in range(8)]),
    ]
    picks = rank_feed(batches, p, set())
    assert len(picks) == 16
    assert len({item["id"] for item in picks}) == 16
    assert any("linux" in item["tags"] for item in picks[:4])
    assert any("gaming" in item["tags"] for item in picks[:4])


def test_liked_title_tokens_improve_order_within_topic():
    p = build_profile(["security"], [
        video("x", "security", "Firmware reverse engineering", rating=1),
    ], now=NOW)
    batches = [("security", [
        {"id": "a", "title": "Beginner security basics", "channel": "A"},
        {"id": "b", "title": "Firmware reverse engineering tutorial", "channel": "B"},
    ])]
    assert rank_feed(batches, p, set())[0]["id"] == "b"
    assert "youtube" not in words("The best youtube video game")


def test_existing_database_is_migrated_without_losing_videos(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE videos (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, tags TEXT NOT NULL,
            created_at TEXT NOT NULL, plays INTEGER NOT NULL DEFAULT 0,
            rating INTEGER NOT NULL DEFAULT 0 CHECK (rating IN (-1, 0, 1))
        )""")
        db.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?)",
            ("dQw4w9WgXcQ", "An old saved video", '["linux"]',
             "2026-10-30T13:00:00+00:00", 3, 0),
        )
    monkeypatch.setattr(localtube, "DB_PATH", db_path)
    with TestClient(localtube.app) as client:
        original = client.get("/api/videos").json()
        assert len(original) == 1
        assert original[0]["title"] == "An old saved video"
        assert original[0]["plays"] == 3
        assert original[0]["last_activity_at"] is None
        assert client.post("/api/videos/dQw4w9WgXcQ/play").status_code == 200
        assert client.put("/api/videos/dQw4w9WgXcQ/feedback",
                          json={"rating": 1}).status_code == 200
        updated = client.get("/api/videos").json()[0]
        assert updated["plays"] == 4
        assert updated["rating"] == 1
        assert updated["last_activity_at"]
        profile = client.get("/api/profile")
        assert profile.status_code == 200
        assert profile.json()["interests"][0]["topic"] == "linux"
