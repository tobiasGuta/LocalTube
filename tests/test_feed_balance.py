"""LocalTube v0.8 Feed Balance: ranking variety without changing candidate discovery."""
from fastapi.testclient import TestClient
import app as localtube
from content_ranker import rank_content
from recommender import build_profile, rank_feed


def _fixture():
    history = [{
        "id": "h0000000000", "title": "Amazing horror gameplay",
        "tags": ["gaming"], "rating": 1,
        "plays": 4, "watched_seconds": 120, "completions": 1,
        "channel": "Horror Channel",
    }]
    profile = build_profile(["gaming", "knowledge"], history)
    batches = [
        ("gaming", [{
            "id": f"g{i:010d}", "title": f"Fresh gameplay picks {i}",
            "channel": f"Gaming creator {i}",
        } for i in range(5)]),
        ("knowledge", [{
            "id": f"k{i:010d}", "title": f"Interesting learning documentaries {i}",
            "channel": f"Knowledge creator {i}",
        } for i in range(3)]),
    ]
    return profile, history, batches


def test_feed_balance_changes_topic_mix_but_not_candidate_set():
    profile, history, batches = _fixture()
    focused = rank_content(batches, profile, history, {history[0]["id"]},
                           balance=0)
    balanced = rank_content(batches, profile, history, {history[0]["id"]},
                            balance=65)
    explore = rank_content(batches, profile, history, {history[0]["id"]},
                           balance=100)
    assert len(focused) == len(balanced) == len(explore) == 8
    assert {v["id"] for v in focused} == {v["id"] for v in explore}
    assert len(set(v["tags"][0] for v in explore[:3])) == 2
    assert sum(v["tags"][0] == "gaming" for v in focused[:3]) > sum(
        v["tags"][0] == "gaming" for v in explore[:3])
    # Default value must preserve v0.7's diversity penalties exactly.
    default = rank_content(batches, profile, history, {history[0]["id"]})
    assert [v["id"] for v in default] == [v["id"] for v in balanced]
    assert all("score_breakdown" in v for v in focused + explore)


def test_balance_must_be_in_documented_range():
    profile, history, batches = _fixture()
    for invalid in (-1, 101, 200, 50.5, "65", None):
        try:
            rank_content(batches, profile, history, set(), balance=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid balance {invalid!r}")


def test_old_baseline_still_stands_alone():
    profile, history, batches = _fixture()
    original = rank_feed(batches, profile, set())
    updated = rank_feed(batches, profile, set())
    assert [v["id"] for v in original] == [v["id"] for v in updated]
    assert all("score_breakdown" not in video for video in original)


def test_feed_balance_api_keeps_same_cached_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "feedbalance.db")
    queries = []

    def fake_search(query, limit=18):
        queries.append((query, limit))
        return [{
            "id": f"{'k' if 'knowledge' in query else 'g'}{i:010d}",
            "title": f"{query} video {i}",
            "channel": f"Creator {i}",
        } for i in range(8)]

    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        for topic in ("gaming", "knowledge"):
            assert client.post("/api/topics", json={"topic": topic}).status_code == 201

        focused = client.get("/api/feed", params={"balance": 0})
        assert focused.status_code == 200
        assert focused.json()["ranker"] == "content"
        assert focused.json()["balance"] == 0
        assert focused.json()["queries_used"] == 2

        diverse = client.get("/api/feed", params={"balance": 100})
        assert diverse.status_code == 200
        assert diverse.json()["balance"] == 100
        assert diverse.json()["candidates_considered"] == focused.json()["candidates_considered"]
        assert len(queries) == 2  # Slider does not cause more YouTube searches.
        assert {v["id"] for v in focused.json()["videos"]} == {
            v["id"] for v in diverse.json()["videos"]}

        old = client.get("/api/feed", params={"ranker": "baseline", "balance": 100})
        assert old.status_code == 200
        assert old.json()["ranker"] == "baseline"
        assert old.json()["balance"] is None
        assert len(queries) == 2

        assert client.get("/api/feed", params={"balance": 101}).status_code == 422
        assert client.get("/api/feed", params={"balance": -1}).status_code == 422
        assert client.get("/api/feed", params={"balance": "hello"}).status_code == 422
