"""All discovery tests stub network search. No Google/YouTube calls in CI."""
from fastapi.testclient import TestClient
import app as localtube


def test_feed_search_cache_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "test.db")
    calls = []

    def fake_search(query, limit=8):
        calls.append((query, limit))
        return [
            {"id": "dQw4w9WgXcQ", "title": "A Linux introduction",
             "channel": "Creator", "duration": 42},
            {"id": "jNQXAC9IVRw", "title": "Learn more Linux",
             "channel": "Teacher", "duration": None},
        ]

    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        assert client.get("/api/feed").json() == {"topics": [], "videos": []}
        assert client.post("/api/topics", json={"topic": "Linux"}).status_code == 201
        assert client.get("/api/topics").json() == ["linux"]
        feed = client.get("/api/feed")
        assert feed.status_code == 200
        assert feed.json()["topics"] == ["linux"]
        assert len(feed.json()["videos"]) == 2
        assert feed.json()["videos"][0]["reason"] == "Because you're into linux"
        assert client.get("/api/feed").status_code == 200
        assert calls == [("linux", 8)]
        search = client.get("/api/discover", params={"q": "linux"})
        assert search.status_code == 200
        assert search.json()[0]["channel"] == "Creator"
        assert calls == [("linux", 8)]  # Cached for one hour.
        assert client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ", "title": "A Linux introduction", "tags": "linux"
        }).status_code == 201
        remaining = client.get("/api/feed").json()["videos"]
        assert len(remaining) == 1
        assert remaining[0]["id"] == "jNQXAC9IVRw"
        assert client.delete("/api/topics/linux").status_code == 204
        assert client.get("/api/topics").json() == []
        assert client.post("/api/topics", json={"topic": "x" * 31}).status_code == 422


def test_discovery_failure_keeps_library_available(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "test2.db")

    def unavailable(query, limit=8):
        raise RuntimeError("YouTube blocked automated searches")

    monkeypatch.setattr(localtube, "search_youtube", unavailable)
    with TestClient(localtube.app) as client:
        assert client.post("/api/topics", json={"topic": "coding"}).status_code == 201
        response = client.get("/api/feed")
        assert response.status_code == 502
        assert "unavailable" in response.json()["detail"].lower()
        assert client.get("/api/videos").json() == []
