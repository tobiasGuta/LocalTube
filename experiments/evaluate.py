"""Offline evaluation of frozen LocalTube candidate pools.

Usage:
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json --json
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json --local-model

This command NEVER searches YouTube, writes playback history, uploads data or
trains a model. The optional --local-model uses an already downloaded FastEmbed
model and a temporary vector cache. Incomplete judgments yield null relevance
metrics instead of treating missing labels as negative feedback.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from content_ranker import rank_content
from recommender import build_profile
from semantic_engine import examples_from
from experiments.v010 import (
    DEFAULT_THRESHOLD, cosine, multi_interest_v010,
    normalized, reference_v09,
)

LABEL_MIN = 0
LABEL_MAX = 3


def _prepared(snapshot: dict) -> tuple[list[dict], list[tuple[str, list[dict]]]]:
    library = snapshot.get("library")
    batch_rows = snapshot.get("batches")
    if not isinstance(library, list) or not isinstance(batch_rows, list) or not batch_rows:
        raise ValueError("Snapshot requires library[] and a nonempty batches[]")
    if any(not isinstance(v, dict) for v in library):
        raise ValueError("Each library record must be an object")
    batches = []
    for batch in batch_rows:
        if not isinstance(batch, dict) or not isinstance(batch.get("topic"), str):
            raise ValueError("Every batch needs a topic string")
        videos = batch.get("videos")
        if not isinstance(videos, list):
            raise ValueError("Every batch needs videos[]")
        for video in videos:
            if (not isinstance(video, dict)
                    or not isinstance(video.get("id"), str)
                    or not isinstance(video.get("title"), str)):
                raise ValueError("Every candidate needs id/title strings")
        batches.append((batch["topic"], videos))
    return library, batches


def _embedding_inputs(library: list[dict], batches: list[tuple[str, list[dict]]]
                      ) -> list[dict]:
    return library + [v for _, videos in batches for v in videos]


def fixture_vectors(library: list[dict],
                    batches: list[tuple[str, list[dict]]]) -> dict[str, tuple[float, ...]]:
    vectors = {}
    for video in _embedding_inputs(library, batches):
        title = video.get("title")
        embedding = video.get("embedding")
        if not isinstance(title, str) or not isinstance(embedding, list):
            raise ValueError("Every fixture video must contain title and embedding[]")
        vec = normalized(embedding)
        if title in vectors and vectors[title] != vec:
            raise ValueError(f"Conflicting embeddings for title {title!r}")
        vectors[title] = vec
    if len({len(v) for v in vectors.values()}) > 1:
        raise ValueError("Fixture embeddings must have consistent dimensions")
    return vectors


def local_model_vectors(library: list[dict],
                        batches: list[tuple[str, list[dict]]]) -> dict[str, tuple[float, ...]]:
    """Optional real-model scoring; never downloads or changes the library DB."""
    from semantic_engine import get_model, vectors_for
    titles = list(dict.fromkeys(
        v["title"] for v in _embedding_inputs(library, batches)
        if isinstance(v.get("title"), str)))
    model = get_model()
    with tempfile.TemporaryDirectory(prefix="localtube-research-") as temp:
        path = Path(temp) / "vectors.db"
        def connect():
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            return con
        with connect() as db:
            db.execute("""CREATE TABLE semantic_vectors (
                cache_key TEXT PRIMARY KEY, model TEXT NOT NULL,
                vector BLOB NOT NULL, updated_at TEXT NOT NULL
            )""")
        calculated = vectors_for(titles, connect, model=model)
    return dict(zip(titles, calculated))


def _labels_for(batches: list[tuple[str, list[dict]]], seen: set[str]
                ) -> dict[str, int | None]:
    labels = {}
    for _topic, videos in batches:
        for video in videos:
            if video["id"] in seen:
                continue
            label = video.get("label", None)
            if label is not None and (type(label) is not int or not LABEL_MIN <= label <= LABEL_MAX):
                raise ValueError("Human relevance labels must be integers 0..3 or null")
            if video["id"] in labels and labels[video["id"]] != label:
                raise ValueError("Duplicate candidate IDs have conflicting labels")
            labels[video["id"]] = label
    return labels


def score_metrics(ranked: list[dict], labels: dict[str, int | None],
                  vectors: dict[str, tuple[float, ...]], k: int) -> dict:
    """Return metrics for one run; never infer dislikes from missing judgments."""
    displayed = ranked[:k]
    missing = sum(value is None for value in labels.values())
    judged = len(labels) - missing
    titles = [v["title"] for v in displayed]
    similarity = [
        cosine(vectors[titles[i]], vectors[titles[j]])
        for i in range(len(titles)) for j in range(i + 1, len(titles))
    ]
    creators = {str(v.get("channel") or "").strip().casefold()
                for v in displayed if str(v.get("channel") or "").strip()}
    tags = {str(tag).strip().casefold()
            for v in displayed for tag in v.get("tags", [])
            if str(tag).strip()}
    result = {
        "ranked": len(displayed),
        "total_candidates": len(labels),
        "judged_candidates": judged,
        "all_judged": missing == 0 and bool(labels),
        "distinct_creators_at_k": len(creators),
        "distinct_search_topics_at_k": len(tags),
        "semantic_diversity_at_k": round(
            sum(1.0 - max(0.0, value) for value in similarity) / len(similarity), 4)
        if similarity else None,
        "precision_at_k": None,
        "ndcg_at_k": None,
    }
    if missing or not labels:
        return result
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(position + 2)
                   for position, value in enumerate(values))
    values = [labels[v["id"]] for v in displayed]
    ideal = dcg(sorted(labels.values(), reverse=True)[:k])
    result["ndcg_at_k"] = round(dcg(values) / ideal, 4) if ideal else 0.0
    result["precision_at_k"] = round(sum(value >= 2 for value in values) / k, 4)
    return result


def evaluate_snapshot(snapshot: dict, *, k: int = 5,
                      threshold: float = DEFAULT_THRESHOLD,
                      use_model: bool = False) -> dict:
    if not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    library, batches = _prepared(snapshot)
    seen = {v.get("id") for v in library}
    labels = _labels_for(batches, seen)
    vectors = (local_model_vectors(library, batches) if use_model
               else fixture_vectors(library, batches))
    if not labels:
        raise ValueError("Snapshot has no eligible unseen candidates")
    if k > len(labels):
        raise ValueError(f"k={k} exceeds {len(labels)} distinct unseen candidates")
    profile = build_profile(snapshot.get("topics", []), library)
    time_str = snapshot.get("evaluation_time", "2026-01-01T00:00:00+00:00")
    now = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    baseline = reference_v09(batches, library, vectors)
    trial, groups = multi_interest_v010(
        batches, library, vectors, now=now, threshold=threshold)
    # All variants use the same base ranker, same candidates, same profiles and
    # same greedy topic/creator diversity penalties. Only semantic features
    # differ between v0.9 and the v0.10 hypothesis.
    limit = len(labels)
    variants = {
        "v0.8_content": None,
        "v0.9_semantic_reference": baseline,
        "v0.10_multi_interest_trial": trial,
    }
    results = {}
    for name, features in variants.items():
        ranked = rank_content(
            batches, profile, library, seen,
            limit=limit, balance=snapshot.get("balance", 65),
            semantic_scores=features,
        )
        results[name] = {
            "metrics": score_metrics(ranked, labels, vectors, k),
            "top": [
                {
                    "id": v["id"], "title": v["title"],
                    "score": v["score"], "reason": v["reason"],
                }
                for v in ranked[:k]
            ],
        }
    return {
        "name": snapshot.get("name", "Untitled frozen snapshot"),
        "k": k,
        "vector_source": "local_prepared_model" if use_model else "fixture",
        "same_candidate_pool": True,
        "candidates": len(labels),
        "preference_examples": len(examples_from(library)),
        "experimental_groups": [
            {"members": list(group["members"]), "support": group["support"],
             "weight": round(group["weight"], 3)}
            for group in groups
        ],
        "experimental_threshold": threshold,
        "results": results,
        "warning": (
            "Fixture vectors are handcrafted test concepts, not measured ML quality."
            if not use_model else
            "Real local embeddings; results still require representative human labels."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Local JSON snapshot; never uploaded")
    parser.add_argument("--k", type=int, default=5, help="Ranking cutoff (default: 5)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="Experimental cosine clustering threshold")
    parser.add_argument("--local-model", action="store_true",
                        help="Use already downloaded local FastEmbed model instead of fixture vectors")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = evaluate_snapshot(snapshot, k=args.k, threshold=args.threshold,
                                   use_model=args.local_model)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['name']} — {report['candidates']} frozen candidates, "
              f"{report['preference_examples']} examples, top {report['k']}")
        print(f"Semantic source: {report['vector_source']}; "
              f"trial groups: {len(report['experimental_groups'])}\n")
        for name, value in report["results"].items():
            metrics = value["metrics"]
            print(name)
            print("  NDCG@k:", metrics["ndcg_at_k"],
                  "Precision@k:", metrics["precision_at_k"],
                  "Semantic diversity:", metrics["semantic_diversity_at_k"])
            print("  Human labels:", metrics["judged_candidates"],
                  "/", metrics["total_candidates"])
            for i, video in enumerate(value["top"], 1):
                print(f"   {i}. {video['title']}")
            print()
        print(report["warning"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
