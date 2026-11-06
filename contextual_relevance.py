"""Opt-in semantic query disambiguation from LOCAL viewing evidence.

Public video search is lexical: an ambiguous phrase can describe unrelated
subjects. The user's positively rated/meaningfully watched videos bearing
that search tag provide *context*; local BGE embeddings then rerank candidates.

No named entities, synonyms, topic taxonomies or query-specific rules.
No API requests are made here. When context is too weak or all candidates
look alike, leave search order untouched. Never hide new interests solely
because they differ from a user's past watches.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Callable

from semantic_engine import _candidate_videos, _cosine, vectors_for


def _tag_key(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _descriptor(video: dict) -> str:
    """Contextual metadata from public title and creator, not a video transcript."""
    title = str(video.get("title") or "").strip()
    channel = str(video.get("channel") or "").strip()
    return (title + (". Creator: " + channel if channel else ""))[:360]


def contextual_scores(
    batches: list[tuple[str, list[dict]]],
    examples: list[tuple[dict, float]],
    connect_db: Callable,
    *,
    model=None,
) -> dict[str, dict[str, float]]:
    """Return optional, explainable intent alignment and negative contrast.

    Each query/topic is grounded in at least TWO distinct, positive examples
    tagged with that same query/topic. We compare candidates RELATIVE to their
    own result batch rather than treating BGE cosine as a calibrated probability.

    The feature is used only in experimental watch-based modes. Explicit search
    still returns all non-dismissed results, ordered more helpfully; an unknown
    query stays in YouTube's original order.
    """
    candidates = _candidate_videos(batches)
    if not candidates:
        return {}

    by_topic: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    for video, weight in examples:
        tags = {_tag_key(tag) for tag in video.get("tags", [])
                if isinstance(tag, str) and tag.strip()}
        for tag in tags:
            by_topic[tag].append((video, weight))

    supported = []
    for topic, videos in batches:
        anchors = [pair for pair in by_topic.get(_tag_key(topic), [])
                   if pair[1] > 0 and pair[0].get("title")]
        if len({v.get("id") for v, _ in anchors}) < 2:
            continue  # One example is not sufficient evidence of query meaning.
        unique = []
        seen = set()
        for video in videos:
            vid = video.get("id")
            if (not isinstance(vid, str) or vid in seen
                    or not isinstance(video.get("title"), str)):
                continue
            seen.add(vid)
            unique.append(video)
        if unique:
            negatives = [pair for pair in by_topic.get(_tag_key(topic), [])
                         if pair[1] < 0 and pair[0].get("title")]
            supported.append((unique, anchors[:24], negatives[:12]))
    if not supported:
        return {}

    texts = list(dict.fromkeys(
        _descriptor(video)
        for videos, anchors, negatives in supported
        for video in (
            videos + [item for item, _ in anchors + negatives]
        )
    ))
    vecs = vectors_for(texts, connect_db, model=model)
    lookup = dict(zip(texts, vecs))
    result: dict[str, dict[str, float]] = {}
    for videos, positives, negatives in supported:
        agreements = []
        for video in videos:
            target = lookup[_descriptor(video)]
            similarities = sorted(
                (max(0.0, _cosine(target, lookup[_descriptor(seed)]))
                 * min(1.0, max(0.25, weight))
                 for seed, weight in positives),
                reverse=True,
            )
            # Two-exemplar agreement avoids a stray liked video dominating.
            best_two = sum(similarities[:2]) / min(2, len(similarities))
            opposite = max(
                (max(0.0, _cosine(target, lookup[_descriptor(seed)]))
                 for seed, _ in negatives),
                default=0.0,
            )
            agreements.append((video["id"], best_two, opposite))
        values = [pos for _, pos, _ in agreements]
        spread = max(values) - min(values)
        median = statistics.median(values)
        scale = max(0.12, spread / 2)
        for vid, positive, negative in agreements:
            # If all candidates resemble the topic equally, keep their
            # order unchanged (but still honor explicit dislikes).
            alignment = (0.0 if spread < 0.06
                         else max(-3.0, min(
                             3.0, 2.6 * (positive - median) / scale)))
            contrast = -4.0 * max(0.0, negative - positive)
            old = result.get(vid)
            if old is None or alignment + contrast > sum(old.values()):
                result[vid] = {
                    "interest_alignment": round(alignment, 3),
                    "disinterest_contrast": round(contrast, 3),
                }
    return result
