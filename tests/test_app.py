from fastapi.testclient import TestClient
import app as localtube


def test_library_and_recommender(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "test.db")
    with TestClient(localtube.app) as client:
        def add(url, title, tags):
            return client.post("/api/videos", json={"url": url, "title": title, "tags": tags})

        a = add("https://youtu.be/dQw4w9WgXcQ", "Learning Linux", "linux, security")
        b = add("https://www.youtube.com/watch?v=jNQXAC9IVRw", "More Linux", "linux, coding")
        c = add("https://www.youtube.com/shorts/9bZkp7q19f0", "Music", "music")
        assert (a.status_code, b.status_code, c.status_code) == (201, 201, 201)
        assert len(client.get("/api/videos").json()) == 3
        assert add("dQw4w9WgXcQ", "Duplicate", "").status_code == 409
        assert add("https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ", "Invalid", "").status_code == 400
        assert client.post("/api/videos/dQw4w9WgXcQ/play").status_code == 200
        picks = client.get("/api/recommendations").json()
        assert picks[0]["id"] == "jNQXAC9IVRw"
        assert "linux" in picks[0]["reason"]
        assert client.put("/api/videos/9bZkp7q19f0/feedback", json={"rating": -1}).status_code == 200
        assert "9bZkp7q19f0" not in [v["id"] for v in client.get("/api/recommendations").json()]
        assert client.delete("/api/videos/dQw4w9WgXcQ").status_code == 204
        assert client.delete("/api/videos/dQw4w9WgXcQ").status_code == 404
