"""Watch progress, resume, statistics and discovery rotation; no live YouTube."""
import sqlite3

from fastapi.testclient import TestClient
import app as localtube
from recommender import build_profile, rank_feed


def test_playback_progress_resume_completions_and_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "watch.db")
    with TestClient(localtube.app) as client:
        response = client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ", "title": "Reverse engineering",
            "tags": "cybersecurity, linux", "channel": "Linux Teacher",
            "duration_seconds": 600,
        })
        assert response.status_code == 201
        assert response.json()["channel"] == "Linux Teacher"
        assert response.json()["resume_position"] == 0
        assert client.get("/api/history").json() == []
        # Opening a video twice is stored separately from actual watch seconds.
        assert client.post("/api/videos/dQw4w9WgXcQ/play").status_code == 200
        assert client.post("/api/videos/dQw4w9WgXcQ/play").status_code == 200
        assert client.get("/api/videos").json()[0]["plays"] == 2
        assert client.get("/api/stats").json()["total_watched_seconds"] == 0
        played = client.put("/api/videos/dQw4w9WgXcQ/progress", json={
            "position_seconds": 80, "duration_seconds": 600, "watched_delta": 10,
        })
        assert played.status_code == 200
        assert played.json()["resume_position"] == 80
        assert played.json()["watched_seconds"] == 10
        assert len(client.get("/api/history").json()) == 1
        stats = client.get("/api/stats").json()
        assert stats["total_watched_seconds"] == 10
        assert stats["topics"][0]["name"] == "cybersecurity"
        assert stats["creators"][0]["name"] == "Linux Teacher"

        completed = client.put("/api/videos/dQw4w9WgXcQ/progress", json={
            "position_seconds": 600, "duration_seconds": 600,
            "watched_delta": 3, "ended": True
        })
        assert completed.status_code == 200
        assert completed.json()["completions"] == 1
        assert completed.json()["resume_position"] == 0
        assert completed.json()["watched_seconds"] == 13
        assert client.get("/api/stats").json()["total_completions"] == 1
        # Seeking through a video never generates a completion on its own.
        just_seeked = client.put("/api/videos/dQw4w9WgXcQ/progress", json={
            "position_seconds": 590, "duration_seconds": 600, "watched_delta": 0,
        })
        assert just_seeked.json()["completions"] == 1
        assert just_seeked.json()["watched_seconds"] == 13


def test_progress_input_validation_and_unknown_video(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "bounds.db")
    with TestClient(localtube.app) as client:
        client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ", "title": "Demo", "tags": "test"
        })
        url = "/api/videos/dQw4w9WgXcQ/progress"
        for bad in [
            {"position_seconds": 40, "duration_seconds": 30, "watched_delta": 1},
            {"position_seconds": 3, "duration_seconds": 60, "watched_delta": 16},
            {"position_seconds": -1, "duration_seconds": 60, "watched_delta": 1},
            {"position_seconds": 60, "duration_seconds": 0, "watched_delta": 0, "ended": True},
        ]:
            assert client.put(url, json=bad).status_code == 422
        assert client.put("/api/videos/xxxxxxxxxxx/progress", json={
            "position_seconds": 0, "duration_seconds": 10, "watched_delta": 1
        }).status_code == 404


def test_v05_database_migrates_without_data_loss(tmp_path, monkeypatch):
    filename = tmp_path / "legacy.db"
    with sqlite3.connect(filename) as db:
        db.execute("""CREATE TABLE videos (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, tags TEXT NOT NULL,
            created_at TEXT NOT NULL, plays INTEGER NOT NULL DEFAULT 0,
            rating INTEGER NOT NULL DEFAULT 0,
            last_activity_at TEXT
        )""")
        db.execute("""INSERT INTO videos VALUES (
            'dQw4w9WgXcQ', 'Existing title', '["linux"]',
            '2026-10-20T10:00:00+00:00', 7, 1, NULL
        )""")
    monkeypatch.setattr(localtube, "DB_PATH", filename)
    with TestClient(localtube.app) as client:
        video = client.get("/api/videos").json()[0]
        assert video["title"] == "Existing title"
        assert video["plays"] == 7
        assert video["rating"] == 1
        assert video["watched_seconds"] == 0
        assert video["channel"] == ""
        assert client.get("/api/profile").status_code == 200
        with sqlite3.connect(filename) as db:
            names = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
        assert {"channel", "duration_seconds", "watched_seconds",
                "resume_position", "completions", "last_watched_at"}.issubset(names)


def test_watch_time_and_creator_affinity_influence_ranking():
    sample = {
        "id": "dQw4w9WgXcQ", "title": "A tutorial", "tags": ["electronics"],
        "plays": 1, "rating": 0, "watched_seconds": 1600,
        "completions": 2, "channel": "TechLab"
    }
    profile = build_profile(["electronics"], [sample])
    assert profile["tags"]["electronics"] > 10
    assert profile["creators"]["techlab"] > 0
    batches = [("electronics", [
        {"id": "aaaaaaaaaaa", "title": "Video A", "channel": "Unknown"},
        {"id": "bbbbbbbbbbb", "title": "Video B", "channel": "TechLab"},
    ])]
    picked = rank_feed(batches, profile, set())
    assert picked[0]["id"] == "bbbbbbbbbbb"


def test_feed_refresh_rotates_queries_and_has_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "rotate.db")
    clock = [1000000]
    monkeypatch.setattr(localtube.time, "time", lambda: clock[0])
    calls = []

    def search(query, limit=18):
        calls.append((query, limit))
        return [{"id": "dQw4w9WgXcQ", "title": "Example", "channel": "One"}]

    monkeypatch.setattr(localtube, "search_youtube", search)
    with TestClient(localtube.app) as client:
        client.post("/api/topics", json={"topic": "gaming"})
        first = client.get("/api/feed").json()
        assert first["cursor"] == 0
        assert calls == [("gaming", 18)]
        refreshed = client.get("/api/feed?refresh=true").json()
        assert refreshed["cursor"] == 1
        assert calls[-1] == ("gaming latest", 18)
        # Immediate repeated refresh doesn't trigger a new search batch.
        again = client.get("/api/feed?refresh=true").json()
        assert again["cursor"] == 1
        assert len(calls) == 2
        clock[0] += 31
        newer = client.get("/api/feed?refresh=true").json()
        assert newer["cursor"] == 2
        assert calls[-1] == ("gaming highlights", 18)
