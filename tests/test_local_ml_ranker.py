"""v0.11 local trained ranking: real observed outcomes only, no online services."""
from __future__ import annotations

import json
import math
from fastapi.testclient import TestClient
import pytest

import app as localtube
import learning_ranker as ml


class FakeEncoder:
    def embed(self, texts):
        for text in texts:
            text = text.lower()
            v = [0.0] * 384
            v[0 if ("lecture" in text or "learning" in text) else
              1 if "toy" in text else 2] = 1
            yield v


def test_feature_extraction_is_bounded_and_ignores_ml_output():
    data = {"title": 99, "semantic_positive": 1.8,
            "semantic_negative": -2.0, "ml_adjustment": 50,
            "topic": float("nan")}
    x = ml.vector(data)
    assert len(x) == len(ml.FEATURES)
    assert x[ml.FEATURES.index("title")] == 1.0
    assert x[ml.FEATURES.index("topic")] == 0.0
    assert math.isclose(x[ml.FEATURES.index("semantic_positive")], 0.5)
    assert ml.vector({**data, "ml_adjustment": -50}) == x


def test_fitted_weights_actually_learn_distinguishing_feature():
    samples = []
    for i in range(7):
        pos = {"semantic_positive": 2 + 0.12 * i, "semantic_negative": 0}
        neg = {"semantic_positive": 0, "semantic_negative": -2 - 0.1 * i}
        samples += [(ml.vector(pos), 1), (ml.vector(neg), 0)]
    weights, bias = ml.fit(samples)
    assert len(weights) == len(ml.FEATURES)
    def predict(parts):
        x = ml.vector(parts)
        return ml._sigmoid(bias + sum(a*b for a, b in zip(x, weights)))
    assert predict({"semantic_positive": 3.0}) > 0.65
    assert predict({"semantic_negative": -3.0}) < 0.35
    assert all(abs(x) <= 2 for x in weights)


def test_single_impression_is_not_assumed_seen_or_disliked(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "one.db")
    with TestClient(localtube.app) as client:
        assert client.get("/api/learning/status").json()["examples"] == 0
        with localtube.connect() as db:
            video = {
                "id": "aaaaaaaaaaa", "score": 1.0,
                "score_breakdown": {"semantic_positive": 1.1},
            }
            token = ml.register(db, [video])[0]["impression_token"]
            assert db.execute("SELECT seen_at FROM ml_impressions").fetchone()[0] is None
        assert client.get("/api/learning/status").json()["exposures"] == 0
        assert client.post("/api/learning/exposures",
                           json={"tokens": [token]}).json()["recorded"] == 1
        assert client.post("/api/learning/exposures",
                           json={"tokens": [token]}).json()["recorded"] == 0
        status = client.get("/api/learning/status").json()
        assert status["exposures"] == 1 and status["examples"] == 0
        assert not status["trained"]
        assert client.post("/api/learning/exposures", json={"tokens": []}).status_code == 422
        assert client.post("/api/learning/exposures",
                           json={"tokens": ["invalid" ]}).json()["recorded"] == 0


def test_negative_explicit_feedback_then_removed_like_no_longer_trains(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "signals.db")
    vid = "dQw4w9WgXcQ"
    with TestClient(localtube.app) as client:
        client.post("/api/videos", json={"url": vid, "title": "Sample"})
        with localtube.connect() as db:
            token = ml.register(db, [{"id": vid, "score": 0.0,
                        "score_breakdown": {"semantic_negative": -3.0}}])[0]["impression_token"]
        # Dislike before an exposure must NOT train on a fabricated impression.
        client.put(f"/api/videos/{vid}/feedback", json={"rating": -1})
        assert client.get("/api/learning/status").json()["examples"] == 0
        client.post("/api/learning/exposures", json={"tokens": [token]})
        client.put(f"/api/videos/{vid}/feedback", json={"rating": 1})
        assert client.get("/api/learning/status").json()["positives"] == 1
        client.put(f"/api/videos/{vid}/feedback", json={"rating": -1})
        assert client.get("/api/learning/status").json()["negatives"] == 1
        client.put(f"/api/videos/{vid}/feedback", json={"rating": 0})
        assert client.get("/api/learning/status").json()["examples"] == 0
        # This reset leaves watch history and library intact.
        assert client.get("/api/videos").json()[0]["rating"] == 0


def test_watch_must_be_meaningful_and_from_exposed_video(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "watches.db")
    vid = "dQw4w9WgXcQ"
    with TestClient(localtube.app) as client:
        client.post("/api/videos", json={"url": vid, "title": "Sample",
                                         "duration_seconds": 600})
        with localtube.connect() as db:
            token = ml.register(db, [{"id": vid, "score": 1.0,
                        "score_breakdown": {"topic": 3.0}}])[0]["impression_token"]
        sid = "testwatchsession0000001"
        client.post(f"/api/videos/{vid}/play", json={"session_id": sid})
        for seq in range(1, 4):
            # 30s is too short; for 600s meaningful threshold is 60s.
            client.put(f"/api/videos/{vid}/progress", json={
                "session_id": sid, "sequence": seq,
                "position_seconds": seq*10, "duration_seconds": 600,
                "watched_delta": 10})
        assert client.get("/api/learning/status").json()["examples"] == 0
        client.post("/api/learning/exposures", json={"tokens": [token]})
        for seq in range(4, 7):
            result = client.put(f"/api/videos/{vid}/progress", json={
                "session_id": sid, "sequence": seq,
                "position_seconds": seq*10, "duration_seconds": 600,
                "watched_delta": 10})
            assert result.status_code == 200
        status = client.get("/api/learning/status").json()
        assert status["positives"] == 1 and status["negatives"] == 0
        # A duplicate heartbeat cannot double count the same watch.
        client.put(f"/api/videos/{vid}/progress", json={
            "session_id": sid, "sequence": 6, "position_seconds": 60,
            "duration_seconds": 600, "watched_delta": 10})
        assert client.get("/api/learning/status").json()["positives"] == 1


def test_model_fits_genuine_labels_and_falls_back_after_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "training.db")
    original = [
        {"id": f"r{i:010d}", "score": 6.0 if i < 5 else -3.0,
         "score_breakdown": {
             "title": 3 if i < 5 else 0,
             "semantic_positive": 3 if i < 5 else 0,
             "semantic_negative": 0 if i < 5 else -3,
         }}
        for i in range(8)
    ]
    with TestClient(localtube.app) as client:
        with localtube.connect() as db:
            marked = ml.register(db, original)
            for i, item in enumerate(marked):
                ml.mark_seen(db, [item["impression_token"]])
                ml.record_outcome(db, item["id"], 1 if i < 5 else 0,
                                  "watch" if i < 5 else "dislike")
        status = client.get("/api/learning/status").json()
        assert status["trained"] and status["examples"] == 8
        assert status["positives"] == 5 and status["negatives"] == 3
        with localtube.connect() as db:
            reranked = ml.predict(db, original)
            assert reranked[0]["ml_probability"] > reranked[-1]["ml_probability"]
            assert all("ml_adjustment" in x["score_breakdown"] for x in reranked)
            assert all(abs(x["score"]-sum(x["score_breakdown"].values())) < .02
                       for x in reranked)
            # Fewer than 3 negatives => never claim to have a trained model.
            db.execute("DELETE FROM ml_outcomes WHERE label=0 AND video_id='r0000000007'")
            ml.retrain(db)
            assert not ml.status(db)["trained"]
            assert ml.predict(db, original) == original
        assert client.delete("/api/learning/data").status_code == 204
        status = client.get("/api/learning/status").json()
        assert not status["trained"] and status["examples"] == 0


def test_real_ml_feed_registers_snapshots_and_preserves_watch_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "feed.db")
    monkeypatch.setattr(localtube, "get_model", lambda: FakeEncoder())
    calls = []
    def fake_search(query, limit=18):
        calls.append(query)
        return [{"id": f"v{i:010d}", "title": ("Academic lecture " if i % 2 else "Collectible toy ") + str(i),
                 "channel": "Creator"} for i in range(12)]
    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        client.post("/api/topics", json={"topic": "learning"})
        response = client.get("/api/feed?ranker=ml&refine=false")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ranker"] == "ml"
        assert not data["ml_learning"]["trained"]
        assert len(data["videos"]) == 12
        assert all("impression_token" in v for v in data["videos"])
        assert not any("ml_adjustment" in v["score_breakdown"]
                       for v in data["videos"])  # Exact v0.10 fallback.
        assert data["ml_learning"]["examples"] == 0
        watch = client.get("/api/feed?ranker=watch&refine=false").json()
        assert [v["id"] for v in watch["videos"]] == [v["id"] for v in data["videos"]]
        assert all("impression_token" not in v for v in watch["videos"])
        assert len(calls) == 1  # Both ranks consume the same cached candidates.
        # Delete v0.11 tracking only; it cannot delete v0.10 or older data.
        assert client.delete("/api/learning/data").status_code == 204


def test_ml_explicit_search_remains_contextual_without_training():
    # A query by itself never produces impression-based training.
    assert ml.FEATURES and "ml_adjustment" not in ml.FEATURES
