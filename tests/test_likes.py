"""Direct like buttons need the same API behavior as the old save-first menu."""
from fastapi.testclient import TestClient
import app as localtube


def test_unsaved_video_can_be_liked_and_unliked_without_playing(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "like.db")
    with TestClient(localtube.app) as client:
        saved = client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ",
            "title": "Linux tutorials",
            "tags": "linux",
        })
        assert saved.status_code == 201
        assert saved.json()["plays"] == 0
        liked = client.put("/api/videos/dQw4w9WgXcQ/feedback", json={"rating": 1})
        assert liked.status_code == 200
        assert liked.json()["rating"] == 1
        assert liked.json()["plays"] == 0
        assert liked.json()["last_activity_at"] is not None
        assert "linux" in client.get("/api/profile").text
        unliked = client.put("/api/videos/dQw4w9WgXcQ/feedback", json={"rating": 0})
        assert unliked.status_code == 200
        assert unliked.json()["rating"] == 0
        assert client.get("/api/videos").json()[0]["plays"] == 0
        assert client.get("/api/recommendations").status_code == 200


def test_like_missing_video_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "other.db")
    with TestClient(localtube.app) as client:
        result = client.put("/api/videos/xxxxxxxxxxx/feedback", json={"rating": 1})
        assert result.status_code == 404
