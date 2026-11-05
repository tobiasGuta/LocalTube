"""v0.10 research branch: frozen-pool and multi-interest tests, all offline."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as localtube
import semantic_engine as semantic
from experiments.capture import snapshot_from_cache
from experiments.evaluate import evaluate_snapshot, main as evaluate_main
from experiments.v010 import (
    cosine, interest_groups, multi_interest_v010, reference_v09,
)
from recommender import build_profile

FIXTURE = (Path(__file__).resolve().parents[1] /
           "experiments" / "fixtures" / "mixed_interests.json")
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def scenario():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def vectors(snapshot):
    return {
        v["title"]: v["embedding"]
        for v in snapshot["library"] + [
            v for b in snapshot["batches"] for v in b["videos"]
        ]
    }


def batches(snapshot):
    return [(b["topic"], b["videos"]) for b in snapshot["batches"]]


def test_frozen_comparison_has_same_candidate_pool_and_no_live_search(monkeypatch):
    def deny_search(*args, **kwargs):
        raise AssertionError("Offline evaluation must not search YouTube")
    monkeypatch.setattr(localtube, "search_youtube", deny_search)
    result = evaluate_snapshot(scenario(), k=9)
    assert result["same_candidate_pool"] is True
    assert result["candidates"] == 9
    assert result["vector_source"] == "fixture"
    assert set(result["results"]) == {
        "v0.8_content", "v0.9_semantic_reference",
        "v0.10_multi_interest_trial",
    }
    pools = [
        {v["id"] for v in ranked["top"]}
        for ranked in result["results"].values()
    ]
    assert pools[0] == pools[1] == pools[2]
    assert all(v["metrics"]["all_judged"] for v in result["results"].values())
    assert all(0 <= v["metrics"]["ndcg_at_k"] <= 1
               for v in result["results"].values())
    assert all(0 <= v["metrics"]["precision_at_k"] <= 1
               for v in result["results"].values())


def test_real_v09_semantic_formula_matches_frozen_reference(tmp_path, monkeypatch):
    """Comparison code must match CURRENT v0.9; don't silently change baseline."""
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "research.db")
    localtube.init_db()
    mapping = {
        "How to think clearly": (1.0, 0.0, 0.0),
        "Gaming horror walkthrough": (0.0, 1.0, 0.0),
        "Knowledge Musona soccer": (0.0, 0.0, 1.0),
        "Better reasoning skills": (0.8, 0.0, 0.0),
        "Scary gaming clips": (0.0, 0.9, 0.0),
        "Soccer highlights": (0.0, 0.0, 0.95),
    }
    class FakeEncoder:
        def embed(self, texts):
            for title in texts:
                vec = mapping[title]
                yield list(vec) + [0.0] * (semantic.MODEL_DIM - len(vec))
    library = [
        {"id": "s0000000001", "title": "How to think clearly",
         "rating": 1, "watched_seconds": 120},
        {"id": "s0000000002", "title": "Gaming horror walkthrough",
         "rating": 1, "watched_seconds": 100},
        {"id": "s0000000003", "title": "Knowledge Musona soccer",
         "rating": -1, "watched_seconds": 0},
    ]
    pool = [("knowledge", [
        {"id": "aaaaaaaaaaa", "title": "Better reasoning skills"},
        {"id": "bbbbbbbbbbb", "title": "Scary gaming clips"},
        {"id": "ccccccccccc", "title": "Soccer highlights"},
    ])]
    real, _ = semantic.semantic_features(
        pool, library, localtube.connect, model=FakeEncoder())
    theoretical = reference_v09(
        pool, library, {
            title: list(vec) + [0.0] * (semantic.MODEL_DIM - len(vec))
            for title, vec in mapping.items()
        })
    assert real == theoretical


def test_multi_interest_groups_preserve_education_and_gaming():
    snap = scenario()
    features, groups = multi_interest_v010(
        batches(snap), snap["library"], vectors(snap), now=NOW)
    assert len(groups) >= 2
    members = {identifier for group in groups for identifier in group["members"]}
    assert {"s0000000001", "s0000000002", "s0000000003"}.issubset(members)
    assert any(group["support"] >= 2 for group in groups)
    assert features["aaaaaaaaaaa"]["semantic_positive"] > 0
    assert features["ffffffffff0"]["semantic_positive"] > 0
    assert features["bbbbbbbbbbb"]["semantic_negative"] < 0
    assert features["bbbbbbbbbbb"]["semantic_positive"] == 0


def test_recency_lifts_recent_examples_but_does_not_forget_old():
    library = [
        {"id": "s0000000001", "title": "Older interest", "rating": 1,
         "watched_seconds": 80, "last_activity_at": "2024-01-01T00:00:00Z"},
        {"id": "s0000000002", "title": "Current interest", "rating": 1,
         "watched_seconds": 80, "last_activity_at": "2026-10-30T00:00:00Z"},
    ]
    groups = interest_groups(
        library, {"Older interest": [1, 0], "Current interest": [0, 1]},
        now=NOW, threshold=0.8)
    assert len(groups) == 2
    weights = {group["members"][0]: group["weight"] for group in groups}
    assert weights["s0000000002"] > weights["s0000000001"] > 0


def test_unjudged_is_not_automatically_negative():
    fixture = scenario()
    fixture["batches"][0]["videos"][0]["label"] = None
    report = evaluate_snapshot(fixture, k=5)
    assert all(result["metrics"]["judged_candidates"] == 8
               for result in report["results"].values())
    assert all(result["metrics"]["ndcg_at_k"] is None
               and result["metrics"]["precision_at_k"] is None
               for result in report["results"].values())
    # Unjudged does not alter ANY ranking; labels are never ranking inputs.
    judged = scenario()
    previous = evaluate_snapshot(judged, k=5)
    for name in previous["results"]:
        assert [x["id"] for x in previous["results"][name]["top"]] == [
            x["id"] for x in report["results"][name]["top"]
        ]


def test_zero_relevance_means_zero_not_division_by_zero():
    fixture = scenario()
    for batch in fixture["batches"]:
        for video in batch["videos"]:
            video["label"] = 0
    report = evaluate_snapshot(fixture, k=5)
    for data in report["results"].values():
        assert data["metrics"]["ndcg_at_k"] == 0.0
        assert data["metrics"]["precision_at_k"] == 0.0


def test_fixture_rejects_invalid_labels_dimensions_and_cutoffs():
    with pytest.raises(ValueError, match="k"):
        evaluate_snapshot(scenario(), k=100)
    with pytest.raises(ValueError, match="Threshold"):
        evaluate_snapshot(scenario(), threshold=-0.1)
    malformed = scenario()
    malformed["batches"][0]["videos"][0]["label"] = "disliked"
    with pytest.raises(ValueError, match="labels"):
        evaluate_snapshot(malformed)
    mismatch = scenario()
    mismatch["batches"][0]["videos"][0]["embedding"] = [0.3, 0.4]
    with pytest.raises(ValueError, match="dimension"):
        evaluate_snapshot(mismatch)
    with pytest.raises(ValueError, match="dimensions"):
        cosine([1, 0], [1, 0, 0])


def test_experiment_cli_prints_json_without_optional_model(capsys):
    code = evaluate_main([str(FIXTURE), "--json", "--k", "5"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["candidates"] == 9
    assert data["vector_source"] == "fixture"
    assert "v0.10_multi_interest_trial" in data["results"]
    assert "handcrafted" in data["warning"].lower()


def test_local_cache_export_does_not_call_youtube(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "offline.db")
    def fake_search(query, limit=18):
        start = "k" if query == "knowledge" else "g"
        return [
            {"id": f"{start}{i:010d}", "title": f"{query} candidate {i}",
             "channel": f"Channel {i}", "duration": 123}
            for i in range(6)
        ]
    monkeypatch.setattr(localtube, "search_youtube", fake_search)
    with TestClient(localtube.app) as client:
        for topic in ("knowledge", "gaming"):
            assert client.post("/api/topics", json={"topic": topic}).status_code == 201
        response = client.get("/api/feed?ranker=content&refine=false")
        assert response.status_code == 200
    def deny_search(*args, **kwargs):
        raise AssertionError("Capture must never search YouTube")
    monkeypatch.setattr(localtube, "search_youtube", deny_search)
    snapshot = snapshot_from_cache(max_candidates=5, refine=False)
    assert len(snapshot["batches"]) == 2
    assert sum(len(b["videos"]) for b in snapshot["batches"]) == 5
    assert all(v["label"] is None for b in snapshot["batches"] for v in b["videos"])
    assert {b["topic"] for b in snapshot["batches"]} == {"knowledge", "gaming"}
    assert all("embedding" not in v for b in snapshot["batches"] for v in b["videos"])
    # No automatic quality score for an unlabeled exported snapshot.
    fake_vectors = {
        video["title"]: [1, 0, 0, 0]
        for b in snapshot["batches"] for video in b["videos"]
    }
    for b in snapshot["batches"]:
        for video in b["videos"]:
            video["embedding"] = fake_vectors[video["title"]]
    report = evaluate_snapshot(snapshot, k=5)
    assert report["results"]["v0.9_semantic_reference"]["metrics"]["ndcg_at_k"] is None
