"""Experimental v0.10 watch-driven semantic learning (LOCAL and opt-in).

The pretrained BGE encoder stays frozen. Learning consists of updating
multiple weighted interest prototypes from meaningful *viewing sessions*,
explicit likes and explicit dislikes. No manual candidate labels, training
server, cloud embeddings or hardcoded topical dictionaries.

This module is deliberately separate from the unchanged v0.9 scorer.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from experiments.v010 import cosine, normalized, _freshness
from semantic_engine import _candidate_videos, preference_weight, vectors_for

MAX_POSITIVES = 32
MAX_NEGATIVES = 12
MAX_GROUPS = 8
CLUSTER_THRESHOLD = 0.78  # Research hypothesis, not BGE-calibrated.
MIN_MEANINGFUL_SECONDS = 25.0


def meaningful_threshold(duration: float) -> float:
    """Short videos need a substantial fraction; long ones need real minutes."""
    seconds = max(0.0, float(duration or 0.0))
    return min(180.0, max(MIN_MEANINGFUL_SECONDS, seconds * 0.10))


def _valid_session(session: dict) -> bool:
    seconds = float(session.get("watched_seconds") or 0.0)
    duration = float(session.get("duration_seconds") or 0.0)
    return (math.isfinite(seconds) and 0 <= seconds <= 86400
            and math.isfinite(duration) and 0 <= duration <= 86400)


def meaningful_sessions(sessions: list[dict]) -> list[dict]:
    return [
        session for session in sessions if _valid_session(session) and
        float(session.get("watched_seconds") or 0) >= meaningful_threshold(
            float(session.get("duration_seconds") or 0))
    ]


def watch_weight(video: dict, sessions: list[dict]) -> float:
    """Signed confidence from actual sessions; NEVER punish a brief exit.

    - A single meaningful session can be positive without clicking Like.
    - Two distinct meaningful visits add a modest confirmation bonus.
    - An explicit dislike always overrides passive watching.
    - Old v0.9 aggregate watch history remains usable as a fallback.
    """
    rating = int(video.get("rating") or 0)
    if rating == -1:
        return -1.0
    worthwhile = meaningful_sessions(sessions)
    implicit = 0.0
    if worthwhile:
        best = sorted(worthwhile, key=lambda s: -float(s["watched_seconds"]))[:3]
        strengths = []
        for session in best:
            watched = float(session["watched_seconds"])
            duration = float(session.get("duration_seconds") or 0)
            fraction = min(1.0, watched / duration) if duration > 0 else 0.0
            # Absolute viewing time counts even when a video is very long;
            # fraction and completion add confidence without deciding alone.
            value = 0.45 + 0.28 * min(1.0, watched / 300.0)
            value += 0.25 * fraction
            if bool(session.get("completed")) and watched >= meaningful_threshold(duration):
                value += 0.12
            strengths.append(value)
        implicit = max(strengths) + 0.12 * min(2, len(worthwhile) - 1)
    elif not sessions:
        # Backward compatibility for histories recorded before sessions existed.
        # A play click alone is never positive; v0.9 enforces >=45s or a like.
        implicit = max(0.0, preference_weight(video)) if rating != 1 else 0.0
    return round(min(1.6, max(1.05 if rating == 1 else 0.0, implicit)), 4)


def session_examples(library: list[dict], sessions: list[dict],
                     *, now: datetime | None = None) -> list[tuple[dict, float]]:
    now = now or datetime.now(timezone.utc)
    by_video = defaultdict(list)
    for session in sessions:
        if isinstance(session.get("video_id"), str):
            by_video[session["video_id"]].append(session)
    positives, negatives = [], []
    for video in library:
        if not str(video.get("title") or "").strip():
            continue
        weight = watch_weight(video, by_video.get(video["id"], []))
        if weight > 0:
            positives.append((video, weight * _freshness(video, now)))
        elif weight < 0:
            negatives.append((video, weight))
    positives.sort(key=lambda item: (-item[1], str(item[0]["id"])))
    negatives.sort(key=lambda item: str(item[0]["id"]))
    return positives[:MAX_POSITIVES] + negatives[:MAX_NEGATIVES]


def watch_profile_library(library: list[dict], sessions: list[dict]) -> list[dict]:
    """Remove weak click-only/saved-only evidence from v0.8's base profile.

    Keep explicitly rated and meaningfully watched videos. This is a
    transient copy: stored videos and old rankings are never modified.
    """
    by_video = defaultdict(list)
    for session in sessions:
        if isinstance(session.get("video_id"), str):
            by_video[session["video_id"]].append(session)
    result = []
    for video in library:
        clean = dict(video)
        weight = watch_weight(video, by_video.get(video["id"], []))
        clean["plays"] = 0  # Plays alone are never strong interest evidence.
        if weight == 0:
            clean.update(tags=[], title="", watched_seconds=0, completions=0)
        # Keep genuine watched history for TF-IDF and topic affinity; dislikes
        # retain their own negative weights in build_profile.
        result.append(clean)
    return result


def watch_features(
    batches: list[tuple[str, list[dict]]],
    library: list[dict],
    sessions: list[dict],
    connect_db: Callable,
    *,
    model=None,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, float]], dict]:
    """Generate per-candidate semantic features from session-learned interests."""
    candidates = _candidate_videos(batches)
    seeds = session_examples(library, sessions, now=now)
    summary = {
        "examples": len(seeds),
        "positive_examples": sum(weight > 0 for _, weight in seeds),
        "negative_examples": sum(weight < 0 for _, weight in seeds),
        "groups": 0,
        "meaningful_sessions": sum(
            bool(meaningful_sessions([session])) for session in sessions),
    }
    if not seeds or not candidates:
        return {}, summary
    n = len(candidates)
    vectors = vectors_for(
        [video["title"] for video in candidates] +
        [video["title"] for video, _ in seeds],
        connect_db, model=model,
    )
    groups = []
    negatives = []
    for offset, (video, weight) in enumerate(seeds):
        vector = vectors[n + offset]
        if weight < 0:
            negatives.append(vector)
            continue
        nearest = max(
            enumerate(groups),
            key=lambda pair: cosine(vector, pair[1]["centroid"]),
            default=None,
        )
        if (nearest is not None and
                cosine(vector, nearest[1]["centroid"]) >= CLUSTER_THRESHOLD):
            group = groups[nearest[0]]
            group["sum"] = [
                current + weight * coordinate
                for current, coordinate in zip(group["sum"], vector)
            ]
            group["total_weight"] += weight
            group["count"] += 1
            group["centroid"] = normalized(group["sum"])
        elif len(groups) < MAX_GROUPS:
            groups.append({
                "sum": [weight * x for x in vector],
                "centroid": vector,
                "count": 1,
                "total_weight": weight,
            })
        # Do not forcibly merge unrelated interests when at capacity.
    summary["groups"] = len(groups)
    result = {}
    for index, video in enumerate(candidates):
        target = vectors[index]
        positive = 0.0
        for group in groups:
            support = 0.78 + 0.08 * min(3, group["count"])
            affinity = max(0.0, cosine(target, group["centroid"]))
            positive = max(
                positive, affinity *
                min(1.6, group["total_weight"] / group["count"]) * support)
        negative = max(
            (max(0.0, cosine(target, vector)) for vector in negatives),
            default=0.0)
        result[video["id"]] = {
            "semantic_positive": round(3.6 * max(0.0, positive - 0.2), 3),
            "semantic_negative": round(-3.8 * max(0.0, negative - 0.15), 3),
        }
    return result, summary
