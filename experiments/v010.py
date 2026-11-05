"""Pure, experimental v0.10 semantic interest model.

Do not import this module into the production FastAPI feed: v0.9 stays intact.
All vectors are caller-supplied (either frozen fixture vectors or locally
computed model embeddings). This module never performs network I/O.

Two comparable methods:
- reference_v09: exact v0.9 max-example semantic score on provided vectors.
- multi_interest_v010: separate, recency-weighted semantic interest groups.

Coefficients and clustering threshold are RESEARCH HYPOTHESES, not trained
weights or validated production defaults. See docs/V010_RESEARCH.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Mapping, Sequence

from semantic_engine import examples_from, _candidate_videos

DEFAULT_THRESHOLD = 0.78
HALF_LIFE_DAYS = 45.0
MAX_GROUPS = 8


def normalized(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(x) for x in values)
    if not vector or not all(math.isfinite(x) for x in vector):
        raise ValueError("A vector must be nonempty and contain only finite numbers")
    size = math.sqrt(sum(x * x for x in vector))
    if size <= 0:
        raise ValueError("Zero-length vectors are invalid")
    return tuple(x / size for x in vector)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Compared vectors must have identical dimensions")
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def _vectors_for_titles(vectors: Mapping[str, Sequence[float]],
                        titles: list[str]) -> dict[str, tuple[float, ...]]:
    if not titles:
        return {}
    missing = [title for title in dict.fromkeys(titles) if title not in vectors]
    if missing:
        raise ValueError(f"Missing embedding for title: {missing[0]!r}")
    result = {title: normalized(vectors[title]) for title in dict.fromkeys(titles)}
    dimensions = {len(v) for v in result.values()}
    if len(dimensions) != 1:
        raise ValueError("All embeddings must use the same dimension")
    return result


def reference_v09(batches: list[tuple[str, list[dict]]], library: list[dict],
                  vectors: Mapping[str, Sequence[float]]) -> dict[str, dict[str, float]]:
    """Frozen v0.9 feature formula, using exactly the supplied candidate pool.

    Mirrors semantic_engine.semantic_features: only the strongest positive and
    strongest negative example affect a candidate, with the same coefficients.
    This reference is tested against the real v0.9 function using a fake encoder.
    """
    candidates = _candidate_videos(batches)
    seeds = examples_from(library)
    if not candidates or not seeds:
        return {}
    embeddings = _vectors_for_titles(
        vectors, [v["title"] for v in candidates] + [v["title"] for v, _ in seeds])
    features = {}
    for candidate in candidates:
        target = embeddings[candidate["title"]]
        positive = negative = 0.0
        for seed, weight in seeds:
            similarity = max(0.0, cosine(target, embeddings[seed["title"]]))
            if weight > 0:
                positive = max(positive, similarity * min(weight, 1.6))
            else:
                negative = max(negative, similarity)
        features[candidate["id"]] = {
            "semantic_positive": round(3.6 * max(0.0, positive - 0.2), 3),
            "semantic_negative": round(-3.8 * max(0.0, negative - 0.15), 3),
        }
    return features


def _freshness(video: dict, now: datetime) -> float:
    """Unknown timestamps are neutral; known older activity fades gently."""
    value = video.get("last_activity_at") or video.get("last_watched_at")
    if not value:
        return 1.0
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds() / 86400)
    except (ValueError, TypeError, OverflowError):
        return 1.0
    return 0.65 + 0.35 * 2 ** (-age_days / HALF_LIFE_DAYS)


def interest_groups(library: list[dict],
                    vectors: Mapping[str, Sequence[float]], *,
                    now: datetime | None = None, threshold: float = DEFAULT_THRESHOLD,
                    max_groups: int = MAX_GROUPS) -> list[dict]:
    """Greedy online clustering of positive examples in embedding space.

    Groups have an L2-normalized weighted centroid. No categories, hardcoded
    subjects, or fixed gaming/education rules. A single liked video can still
    form a group; multiple aligned examples strengthen its evidence modestly.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("Threshold must be within [0, 1]")
    if not isinstance(max_groups, int) or max_groups < 1:
        raise ValueError("max_groups must be a positive integer")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    positive = [(video, weight) for video, weight in examples_from(library) if weight > 0]
    if not positive:
        return []
    vecs = _vectors_for_titles(vectors, [v["title"] for v, _ in positive])
    ordered = sorted(
        [(video, weight * _freshness(video, now)) for video, weight in positive],
        key=lambda pair: (-pair[1], str(pair[0].get("id", ""))),
    )
    groups = []
    for video, weight in ordered:
        vec = vecs[video["title"]]
        match = max(
            enumerate(groups),
            key=lambda pair: cosine(vec, pair[1]["centroid"]),
            default=None,
        )
        if match is not None and cosine(vec, match[1]["centroid"]) >= threshold:
            group = groups[match[0]]
            group["members"].append(video["id"])
            group["total_weight"] += weight
            group["sum"] = [
                previous + weight * current
                for previous, current in zip(group["sum"], vec)
            ]
            group["centroid"] = normalized(group["sum"])
        elif len(groups) < max_groups:
            groups.append({
                "members": [video["id"]], "total_weight": weight,
                "sum": [weight * x for x in vec], "centroid": vec,
            })
        else:
            # Keep the top stronger groups but retain all examples only when
            # the vector falls inside a group; never force unrelated interests
            # to be merged just to reach MAX_GROUPS.
            continue
    return [
        {
            "centroid": group["centroid"],
            "members": tuple(group["members"]),
            "support": len(group["members"]),
            "weight": group["total_weight"] / len(group["members"]),
        }
        for group in groups
    ]


def multi_interest_v010(
    batches: list[tuple[str, list[dict]]],
    library: list[dict],
    vectors: Mapping[str, Sequence[float]],
    *,
    now: datetime | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[dict[str, dict[str, float]], list[dict]]:
    """Score candidates against multiple interest groups, keeping negatives.

    Includes a small support bonus for repeated evidence; recency factors
    temper older examples without erasing long-term interests. It reuses the
    existing v0.8 diversity reranker separately through rank_content.
    """
    candidates = _candidate_videos(batches)
    seeds = examples_from(library)
    if not candidates or not seeds:
        return {}, []
    embeddings = _vectors_for_titles(
        vectors, [v["title"] for v in candidates] + [v["title"] for v, _ in seeds])
    groups = interest_groups(library, embeddings, now=now, threshold=threshold)
    negatives = [embeddings[v["title"]] for v, weight in seeds if weight < 0]
    features = {}
    for candidate in candidates:
        vec = embeddings[candidate["title"]]
        positive = 0.0
        for group in groups:
            # Single examples count, but agreement among a few related likes
            # is more reliable evidence than one isolated liked title.
            support = 0.78 + 0.08 * min(3, group["support"])
            agreement = max(0.0, cosine(vec, group["centroid"]))
            positive = max(
                positive, agreement * min(1.6, group["weight"]) * support)
        negative = max(
            (max(0.0, cosine(vec, bad)) for bad in negatives), default=0.0)
        features[candidate["id"]] = {
            "semantic_positive": round(3.6 * max(0.0, positive - 0.2), 3),
            "semantic_negative": round(-3.8 * max(0.0, negative - 0.15), 3),
        }
    return features, groups
