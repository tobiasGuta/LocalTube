"""v0.7: privacy-conscious candidate planning and explainable local reranking.

No network calls: searches are stubbed in API integration tests.
"""
from fastapi.testclient import TestClient
import app as localtube
from content_ranker import rank_content
from discovery_plan import MAX_QUERIES, build_queries
from recommender import build_profile


def saved(id, title, tags, *, rating=0, seconds=0, channel=""):
    return {
        "id": id, "title": title, "tags": tags, "rating": rating,
        "plays": 1 if seconds else 0, "watched_seconds": seconds,
        "completions": 0, "channel": channel,
    }


def test_queries_start_with_topics_and_expand_only_after_real_positive_signals():
    library = [
        saved("a", "The Paranormal Horror Game That Was Cancelled", ["gameplays"],
              rating=1, seconds=120, channel="Hollow"),
        saved("b", "Very Private Notes Do Not Send", ["knowledge"]),
        saved("c", "Bad Video Should Not Seed Searches", ["gameplays"], rating=-1),
    ]
    profile = build_profile(["gameplays", "knowledge"], library)
    queries = build_queries(profile, library, cursor=0)
    actual = [q for _, q in queries]
    assert actual[0] in ("gameplays", "knowledge")
    assert {"gameplays", "knowledge"}.issubset(actual)
    assert any("paranormal horror" in q for q in actual)
    assert any("Hollow gameplays".lower() == q.lower() for q in actual)
    assert not any("private notes" in q.lower() for q in actual)
    assert len(actual) == len(set(actual)) <= MAX_QUERIES
    assert all(2 <= len(q) <= 80 for q in actual)
    assert build_queries(build_profile(["linux"], []), [], cursor=1) == [
        ("linux", "linux latest")
    ]


def test_positive_title_matches_better_than_generic_unrelated_title():
    likes = [saved("liked", "Firmware reverse engineering",
                   ["security"], rating=1)]
    profile = build_profile(["security"], likes)
    results = [
        {"id": "aaaaaaaaaaa", "title": "General security tips", "channel": "A"},
        {"id": "bbbbbbbbbbb", "title": "Firmware reverse engineering tutorial", "channel": "B"},
    ]
    ranked = rank_content([("security", results)], profile, likes, {"liked"})
    assert [v["id"] for v in ranked] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    assert ranked[0]["score_breakdown"]["title"] > ranked[1]["score_breakdown"]["title"]
    assert "Similar" in ranked[0]["reason"]
    assert abs(ranked[0]["score"] - sum(ranked[0]["score_breakdown"].values())) < 0.01


def test_disliked_title_similarity_is_a_penalty():
    disliked = [saved("bad", "Horror jumpscare prank", ["gaming"], rating=-1)]
    profile = build_profile(["gaming"], disliked)
    ranked = rank_content([
        ("gaming", [
            {"id": "aaaaaaaaaaa", "title": "Horror jumpscare prank explained", "channel": "A"},
            {"id": "bbbbbbbbbbb", "title": "Relaxing puzzle game guide", "channel": "B"},
        ])
    ], profile, disliked, {"bad"})
    assert ranked[0]["id"] == "bbbbbbbbbbb"
    assert ranked[1]["score_breakdown"]["disliked_similarity"] < 0


def test_duplicate_candidates_and_creator_variety():
    liked = [saved("liked", "Linux terminal tricks", ["linux"], rating=1,
                   channel="Dev School")]
    profile = build_profile(["linux", "gaming"], liked)
    batches = [
        ("linux", [
            {"id": "aaaaaaaaaaa", "title": "Linux shell one", "channel": "Dev School"},
            {"id": "bbbbbbbbbbb", "title": "Linux shell two", "channel": "Dev School"},
        ]),
        ("gaming", [
            {"id": "ccccccccccc", "title": "Gaming beginner guide", "channel": "Other"},
            {"id": "aaaaaaaaaaa", "title": "Linux shell one", "channel": "Dev School"},
        ]),
    ]
    ranked = rank_content(batches, profile, liked, {"liked"})
    assert len(ranked) == 3
    assert len({v["id"] for v in ranked}) == 3
    assert sorted(ranked[0]["tags"]) in (["linux"], ["gaming", "linux"])
    assert any(v["channel"] == "Other" for v in ranked[:2])
    assert all("score_breakdown" in v and "score" in v for v in ranked)


def test_api_can_compare_baseline_and_content_using_same_cached_candidates(
        tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "v07.db")
    calls = []

    def fake_search(query, limit=18):
        calls.append((query, limit))
        return [
            {"id": "aaaaaaaaaaa", "title": "Firmware reverse engineering lesson",
             "channel": "Lab", "duration": 100},
            {"id": "bbbbbbbbbbb", "title": "Security basics", "channel": "Other",
             "duration": 90},
        ]

    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        client.post("/api/topics", json={"topic": "security"})
        a = client.get("/api/feed")
        assert a.status_code == 200
        assert a.json()["ranker"] == "content"
        assert a.json()["queries_used"] == 1
        assert a.json()["candidates_considered"] == 2
        assert "score_breakdown" in a.json()["videos"][0]
        b = client.get("/api/feed?ranker=baseline")
        assert b.status_code == 200
        assert b.json()["ranker"] == "baseline"
        assert "score_breakdown" not in b.json()["videos"][0]
        assert {v["id"] for v in b.json()["videos"]} == {
            v["id"] for v in a.json()["videos"]
        }
        assert calls == [("security", 18)]  # Same cached candidates, no new search.
        assert client.get("/api/feed?ranker=oops").status_code == 422


def test_positive_feedback_adds_refined_queries_but_not_dislikes(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "queries.db")
    calls = []

    def fake_search(query, limit=18):
        calls.append(query)
        return [
            {"id": "aaaaaaaaaaa", "title": "New tutorial", "channel": "Other",
             "duration": 35},
        ]

    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        assert client.post("/api/topics", json={"topic": "electronics"}).status_code == 201
        assert client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ", "title": "Arduino circuit design basics",
            "tags": "electronics", "channel": "Circuit Lab"
        }).status_code == 201
        assert client.get("/api/feed").status_code == 200
        assert calls == ["electronics"]
        client.put("/api/videos/dQw4w9WgXcQ/feedback", json={"rating": 1})
        r = client.get("/api/feed")
        assert r.status_code == 200
        assert r.json()["queries_used"] == 3
        assert "arduino circuit design basics" in calls
        assert "circuit lab electronics" in calls
        assert len(calls) == 3
        # Opting out uses only the broad topic query; no new keyword requests.
        minimal = client.get("/api/feed?refine=false")
        assert minimal.status_code == 200
        assert minimal.json()["queries_used"] == 1
        assert len(calls) == 3
