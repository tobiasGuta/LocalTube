"""v0.10 research: topic ambiguity and negative feedback, entirely offline.

Arbitrary overlapping word used in fixtures; no app-specific topic mappings.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app as localtube
from contextual_relevance import contextual_scores
from watch_learning import session_examples
from recommender import build_profile
from content_ranker import rank_content


class FakeEncoder:
    """Synthetic semantic concepts, not an actual performance benchmark."""
    def embed(self, texts):
        for text in texts:
            text = text.casefold()
            values = [0.0] * 384
            if any(word in text for word in ("lecture", "academic", "reasoning", "seminar")):
                values[0] = 1
            elif any(word in text for word in ("toy", "plush", "collectible")):
                values[1] = 1
            elif any(word in text for word in ("movie", "trailer")):
                values[2] = 1
            else:
                values[3] = 1
            yield values


def saved(vid: str, title: str, rating: int, *, topic="atlas", date=None):
    return {
        "id": vid, "title": title, "tags": [topic], "channel": "",
        "rating": rating, "watched_seconds": 120 if rating == 1 else 0,
        "completions": 0, "plays": 1 if rating == 1 else 0,
        "last_activity_at": date,
    }


def test_local_query_context_distinguishes_meanings_and_dislikes(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "context.db")
    localtube.init_db()
    library = [
        saved("s0000000001", "Atlas academic lecture on reasoning", 1),
        saved("s0000000002", "Atlas seminar on communication", 1),
        saved("s0000000003", "Atlas collectible toy figures", -1),
    ]
    batches = [("atlas", [
        {"id": "aaaaaaaaaaa", "title": "Atlas plush toy collection", "channel": "Shop"},
        {"id": "bbbbbbbbbbb", "title": "New atlas academic lecture", "channel": "Institute"},
        {"id": "ccccccccccc", "title": "Atlas movie trailer", "channel": "Films"},
        {"id": "ddddddddddd", "title": "Atlas seminar recordings", "channel": "Institute"},
    ])]
    examples = session_examples(library, [])
    contextual = contextual_scores(
        batches, examples, localtube.connect, model=FakeEncoder())
    assert contextual["bbbbbbbbbbb"]["interest_alignment"] > 0
    assert contextual["ddddddddddd"]["interest_alignment"] > 0
    assert contextual["aaaaaaaaaaa"]["interest_alignment"] < 0
    assert contextual["aaaaaaaaaaa"]["disinterest_contrast"] < 0
    profile = build_profile(["atlas"], library)
    ranked = rank_content(batches, profile, library,
                          {"s0000000001", "s0000000002", "s0000000003"},
                          limit=4, context_scores=contextual)
    assert set(v["id"] for v in ranked[:2]) == {"bbbbbbbbbbb", "ddddddddddd"}
    assert "interest_alignment" in ranked[0]["score_breakdown"]
    assert "disinterest_contrast" in ranked[-1]["score_breakdown"]
    assert all(abs(v["score"] - sum(v["score_breakdown"].values())) < 0.03
               for v in ranked)


def test_context_needs_two_positive_examples_not_one(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "sparse.db")
    localtube.init_db()
    v = saved("s0000000001", "Atlas academic lecture", 1)
    batches = [("atlas", [
        {"id": "aaaaaaaaaaa", "title": "Atlas academic seminar"},
        {"id": "bbbbbbbbbbb", "title": "Atlas toy"},
    ])]
    assert contextual_scores(batches, session_examples([v], []),
                             localtube.connect, model=FakeEncoder()) == {}
    assert contextual_scores([("new subject", batches[0][1])],
                             session_examples([v, saved("s0000000002",
                             "Atlas academic reasoning", 1)], []),
                             localtube.connect, model=FakeEncoder()) == {}


def test_explicit_search_uses_context_and_does_not_send_extra_queries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(localtube, "get_model", lambda: FakeEncoder())
    calls = []
    found = [
        {"id": "aaaaaaaaaaa", "title": "Atlas plush toy collection", "channel": "Toy Store"},
        {"id": "bbbbbbbbbbb", "title": "Atlas academic lecture today", "channel": "Study"},
        {"id": "ccccccccccc", "title": "Atlas movie trailer", "channel": "Cinema"},
        {"id": "ddddddddddd", "title": "Atlas seminar recordings", "channel": "Research"},
        {"id": "eeeeeeeeeee", "title": "Atlas collectible toy figures", "channel": "Shop"},
    ]

    def search(query, limit=18):
        calls.append(query)
        return found

    monkeypatch.setattr(localtube, "search_youtube", search)
    with TestClient(localtube.app) as client:
        for vid, title, rating in [
            ("s0000000001", "Atlas academic lecture on reasoning", 1),
            ("s0000000002", "Atlas seminar on communication", 1),
            ("eeeeeeeeeee", "Atlas collectible toy figures", -1),
        ]:
            assert client.post("/api/videos", json={
                "url": vid, "title": title, "tags": "atlas"
            }).status_code == 201
            assert client.put(f"/api/videos/{vid}/feedback",
                              json={"rating": rating}).status_code == 200
        response = client.get("/api/discover", params={"q": "atlas", "ranker": "watch"})
        assert response.status_code == 200, response.text
        ranked = response.json()
        assert [v["id"] for v in ranked[:2]] == ["bbbbbbbbbbb", "ddddddddddd"]
        assert "eeeeeeeeeee" not in {v["id"] for v in ranked}
        assert "disinterest_contrast" in ranked[-1]["score_breakdown"]
        assert len(calls) == 1  # Only the literal term the user typed.
        raw = client.get("/api/discover", params={"q": "atlas", "ranker": "content"}).json()
        assert raw[0]["id"] == "aaaaaaaaaaa"
        assert "eeeeeeeeeee" not in {v["id"] for v in raw}
        assert len(calls) == 1  # Cached.
        # Brand-new query must remain normal search, not overfit to past topics.
        unseen = client.get("/api/discover", params={"q": "new subject", "ranker": "watch"})
        assert unseen.status_code == 200
        assert unseen.json()[0]["id"] == "aaaaaaaaaaa"
        assert calls == ["atlas", "new subject"]


def test_recommendation_feed_reranks_using_context_without_hardcoded_topics(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "feed.db")
    monkeypatch.setattr(localtube, "get_model", lambda: FakeEncoder())
    queries = []
    def search(query, limit=18):
        queries.append(query)
        return [
            {"id": "aaaaaaaaaaa", "title": "Atlas collectible toy figures",
             "channel": "Collectors"},
            {"id": "bbbbbbbbbbb", "title": "Atlas academic lecture",
             "channel": "Research"},
            {"id": "ccccccccccc", "title": "Atlas movie trailer",
             "channel": "Cinema"},
            {"id": "ddddddddddd", "title": "Atlas seminar on reasoning",
             "channel": "Academy"},
        ]
    monkeypatch.setattr(localtube, "search_youtube", search)
    with TestClient(localtube.app) as client:
        for vid, title in [
            ("s0000000001", "Atlas academic lecture on reasoning"),
            ("s0000000002", "Atlas academic seminar for beginners"),
        ]:
            assert client.post("/api/videos", json={"url": vid,
                "title": title, "tags": "atlas"}).status_code == 201
            assert client.put(f"/api/videos/{vid}/feedback",
                              json={"rating": 1}).status_code == 200
        response = client.get("/api/feed?ranker=watch&refine=false")
        assert response.status_code == 200, response.text
        ranked = response.json()["videos"]
        assert set(v["id"] for v in ranked[:2]) == {"bbbbbbbbbbb", "ddddddddddd"}
        assert any("interest_alignment" in v["score_breakdown"] for v in ranked)
        assert len(queries) == 1
        # Clicked/saved content must be absent from the next recommendation feed.
        assert client.post("/api/videos", json={
            "url": "bbbbbbbbbbb", "title": "Atlas academic lecture", "tags": "atlas"
        }).status_code == 201
        assert client.post("/api/videos/bbbbbbbbbbb/play").status_code == 200
        next_feed = client.get("/api/feed?ranker=watch&refine=false").json()
        assert "bbbbbbbbbbb" not in [v["id"] for v in next_feed["videos"]]
        assert len(queries) == 1


def test_latest_not_interested_remains_learning_example():
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    library = [
        saved(f"x{i:010d}", "Different video " + str(i), -1,
              date=(now - timedelta(days=25 - i)).isoformat())
        for i in range(16)
    ]
    negatives = [v["id"] for v, weight in session_examples(library, []) if weight < 0]
    assert len(negatives) == 12
    assert library[-1]["id"] in negatives
    assert library[0]["id"] not in negatives


def test_contextless_batches_do_not_run_embedding_model(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "none.db")
    localtube.init_db()
    class ForbiddenEncoder:
        def embed(self, texts):
            raise AssertionError("No semantic inference without user context")
    result = contextual_scores(
        [("brand new", [{"id": "aaaaaaaaaaa", "title": "Brand new episode"}])],
        [], localtube.connect, model=ForbiddenEncoder())
    assert result == {}
