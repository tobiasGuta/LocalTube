"""v0.9 deterministic semantic-engine tests. No model weights or Internet needed.

FakeEncoder maps conceptually similar phrases to identical synthetic 384-D
vectors; tests exercise caching, behavior signals, query expansion and APIs.
"""
from __future__ import annotations

import math
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as localtube
import semantic_engine as semantic
from content_ranker import rank_content
from recommender import build_profile


class FakeEncoder:
    def __init__(self):
        self.calls = 0
        self.texts = []

    def embed(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        for text in texts:
            text = text.lower()
            # These synthetic vectors are TEST DATA, not production categories.
            if any(term in text for term in ("thinking", "think clearly", "reasoning",
                                              "decisions", "critical")):
                axis = 0
            elif any(term in text for term in ("musona", "soccer", "goals")):
                axis = 1
            elif any(term in text for term in ("horror", "jumpscare", "gameplay")):
                axis = 2
            else:
                axis = 3
            vector = [0.0] * semantic.MODEL_DIM
            vector[axis] = 1.0
            yield vector


def watch(vid, title, *, rating=0, seconds=0, duration=600, plays=0, completions=0):
    return {
        "id": vid, "title": title, "tags": ["knowledge"],
        "rating": rating, "watched_seconds": seconds,
        "duration_seconds": duration, "plays": plays,
        "completions": completions, "channel": "Education Creator",
    }


def test_learning_examples_are_behavior_driven_not_hardcoded_categories():
    silent_save = watch("a", "How to think clearly")
    clicking = watch("b", "How to think clearly", plays=10)
    liked = watch("c", "How to think clearly", rating=1)
    watched = watch("d", "How to think clearly", seconds=90)
    disliked = watch("e", "How to think clearly", rating=-1, seconds=1000)
    assert semantic.preference_weight(silent_save) == 0
    assert semantic.preference_weight(clicking) == 0
    assert semantic.preference_weight(liked) > semantic.preference_weight(watched) > 0
    assert semantic.preference_weight(disliked) < 0
    assert [v["id"] for v, _ in semantic.examples_from(
        [silent_save, clicking, liked, watched, disliked])] == ["c", "d", "e"]


def test_cache_is_local_and_reused_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "local.db")
    localtube.init_db()
    encoder = FakeEncoder()
    texts = ["How to think clearly", "Improve reasoning skills",
             "How to think clearly"]
    first = semantic.vectors_for(texts, localtube.connect, model=encoder)
    assert len(first) == 3 and first[0] == first[2]
    assert math.isclose(sum(value * value for value in first[0]), 1)
    assert encoder.calls == 1
    assert len(encoder.texts) == 2  # One vector per unique title.
    second = semantic.vectors_for(texts, localtube.connect, model=encoder)
    assert second == first
    assert encoder.calls == 1
    with localtube.connect() as db:
        rows = db.execute("SELECT cache_key,vector FROM semantic_vectors").fetchall()
    assert len(rows) == 2
    assert len(rows[0]["cache_key"]) == 64
    assert len(rows[0]["vector"]) == semantic.MODEL_DIM * 4
    assert semantic.clear_vector_cache(localtube.connect) == 2
    assert semantic.clear_vector_cache(localtube.connect) == 0


def test_semantically_related_education_beats_unrelated_keyword_match(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "semantic.db")
    localtube.init_db()
    encoder = FakeEncoder()
    library = [watch("dQw4w9WgXcQ", "How to think clearly", rating=1, seconds=90)]
    batches = [("knowledge", [
        {"id": "aaaaaaaaaaa", "title": "Knowledge Musona soccer goals", "channel": "Sports"},
        {"id": "bbbbbbbbbbb", "title": "Improve reasoning skills every day", "channel": "School"},
    ])]
    features, count = semantic.semantic_features(batches, library, localtube.connect,
                                                 model=encoder)
    assert count == 1
    assert features["bbbbbbbbbbb"]["semantic_positive"] > 2
    assert features["aaaaaaaaaaa"]["semantic_positive"] == 0
    profile = build_profile(["knowledge"], library)
    ordered = rank_content(batches, profile, library, {library[0]["id"]},
                           semantic_scores=features)
    assert ordered[0]["id"] == "bbbbbbbbbbb"
    assert "Similar in meaning" in ordered[0]["reason"]
    assert "semantic_positive" in ordered[0]["score_breakdown"]
    assert abs(ordered[0]["score"] -
               sum(ordered[0]["score_breakdown"].values())) < 0.02
    # Unmodified v0.8 does not gain any semantic-only score features.
    old = rank_content(batches, profile, library, {library[0]["id"]})
    assert all("semantic_positive" not in v["score_breakdown"] for v in old)


def test_disliked_meaning_is_penalized_even_with_different_words(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "neg.db")
    localtube.init_db()
    encoder = FakeEncoder()
    library = [watch("dQw4w9WgXcQ", "Horror jumpscare prank", rating=-1)]
    batches = [("knowledge", [
        {"id": "aaaaaaaaaaa", "title": "Scary horror gameplay", "channel": "A"},
        {"id": "bbbbbbbbbbb", "title": "Improve reasoning skills", "channel": "B"},
    ])]
    features, count = semantic.semantic_features(batches, library, localtube.connect,
                                                 model=encoder)
    assert count == 1
    assert features["aaaaaaaaaaa"]["semantic_negative"] < 0
    assert features["bbbbbbbbbbb"]["semantic_negative"] == 0
    assert all(v["semantic_positive"] == 0 for v in features.values())


def test_one_hop_discovery_uses_semantic_signal_only_if_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "expand.db")
    localtube.init_db()
    encoder = FakeEncoder()
    library = [watch("dQw4w9WgXcQ", "How to think clearly", rating=1)]
    batches = [("knowledge", [
        {"id": "aaaaaaaaaaa", "title": "Knowledge Musona soccer highlights",
         "channel": "Sports"},
        {"id": "bbbbbbbbbbb", "title": "Improve reasoning in everyday decisions",
         "channel": "School"},
    ])]
    hint = semantic.suggest_expansion(batches, library, {"knowledge"},
                                     localtube.connect, model=encoder)
    assert hint == ("knowledge", "improve reasoning everyday decisions")
    # Do not issue a repeated search for the same terms.
    assert semantic.suggest_expansion(batches, library,
        {"knowledge", hint[1]}, localtube.connect, model=encoder) is None
    assert semantic.suggest_expansion(batches,
        [watch("dQw4w9WgXcQ", "How to think clearly")], set(),
        localtube.connect, model=encoder) is None


def test_missing_model_is_explicit_not_a_fake_semantic_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "unavailable.db")
    calls = []
    monkeypatch.setattr(localtube, "search_youtube", lambda query, limit=18:
                        calls.append(query) or [])
    def unavailable():
        raise semantic.ModelUnavailable("Model not prepared locally")
    monkeypatch.setattr(localtube, "get_model", unavailable)
    with TestClient(localtube.app) as client:
        assert client.post("/api/topics", json={"topic": "knowledge"}).status_code == 201
        semantic_feed = client.get("/api/feed?ranker=semantic")
        assert semantic_feed.status_code == 409
        assert "Model not prepared" in semantic_feed.json()["detail"]
        assert calls == []  # No wasted Google searches.
        assert client.get("/api/semantic/status").status_code == 200


def test_semantic_and_content_use_cached_candidates_without_external_ml(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "api.db")
    model = FakeEncoder()
    monkeypatch.setattr(localtube, "get_model", lambda: model)
    queries = []
    def fake_search(query, limit=18):
        queries.append(query)
        if "reasoning" in query:
            return [{"id": "ccccccccccc", "title": "Better decisions explained",
                     "channel": "School"}]
        return [
            {"id": "aaaaaaaaaaa", "title": "Knowledge Musona soccer goals",
             "channel": "Sports"},
            {"id": "bbbbbbbbbbb", "title": "Improve reasoning skills every day",
             "channel": "School"},
        ]
    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        assert client.post("/api/topics", json={"topic": "knowledge"}).status_code == 201
        created = client.post("/api/videos", json={
            "url": "dQw4w9WgXcQ", "title": "How to think clearly",
            "tags": "knowledge", "duration_seconds": 600
        })
        assert created.status_code == 201
        assert client.put("/api/videos/dQw4w9WgXcQ/feedback",
                          json={"rating": 1}).status_code == 200

        baseline = client.get("/api/feed?ranker=content&refine=false")
        assert baseline.status_code == 200
        assert model.calls == 0

        newer = client.get("/api/feed?ranker=semantic&refine=false")
        assert newer.status_code == 200
        assert newer.json()["ranker"] == "semantic"
        assert newer.json()["semantic_examples"] == 1
        assert newer.json()["semantic_expanded"] is False
        assert newer.json()["queries_used"] == baseline.json()["queries_used"] == 1
        assert newer.json()["videos"][0]["id"] == "bbbbbbbbbbb"
        assert "semantic_positive" in newer.json()["videos"][0]["score_breakdown"]
        before = model.calls
        cached = client.get("/api/feed?ranker=semantic&refine=false")
        assert cached.status_code == 200
        assert model.calls == before  # Vectors persisted in SQLite.
        assert len(queries) == 1

        expanded = client.get("/api/feed?ranker=semantic&refine=true")
        assert expanded.status_code == 200
        assert expanded.json()["semantic_expanded"] is True
        assert expanded.json()["queries_used"] >= 2
        assert any("reasoning" in q for q in queries)
        assert {video["id"] for video in newer.json()["videos"]}.issubset(
            {video["id"] for video in expanded.json()["videos"]})


def test_status_and_import_do_not_require_fastembed(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "READY_FILE", tmp_path / "missing-marker")
    data = semantic.status()
    assert data["ready"] is False
    assert data["model"] == semantic.MODEL_ID
    assert "python -m semantic_engine download" in data["message"]
    # Model-specific imports are lazy: the default test process works without it.
    assert callable(semantic.get_model)
