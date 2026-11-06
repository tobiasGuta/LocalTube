"""Interpretable TF-IDF content ranking for LocalTube v0.7.

Dependency-free cosine similarity on local title tokens. Candidate metadata comes
from account-free YouTube search; no external embeddings or training requests.
The original recommender.rank_feed remains available as a baseline.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from recommender import words
from discovery import VIDEO_ID


def _tfidf(documents: list[set[str]]) -> list[dict[str, float]]:
    """L2-normalized log-IDF word vectors, corpus includes saved positive titles."""
    n = len(documents)
    if not n:
        return []
    document_frequency = Counter(token for doc in documents for token in doc)
    result = []
    for doc in documents:
        weighted = {
            token: 1.0 + math.log((n + 1.0) / (document_frequency[token] + 1.0))
            for token in doc
        }
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        result.append({token: value / norm for token, value in weighted.items()})
    return result


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def _seed_weight(video: dict) -> float:
    rating = int(video.get("rating") or 0)
    seconds = max(0.0, float(video.get("watched_seconds") or 0))
    completions = min(3, int(video.get("completions") or 0))
    if rating == -1:
        return -1.0
    if rating != 1 and seconds < 30 and completions == 0:
        return 0.0
    return min(1.4, (1.0 if rating == 1 else 0.4) +
               min(0.3, seconds / 900) + min(0.2, completions * 0.1))


def rank_content(
    batches: list[tuple[str, list[dict]]],
    profile: dict,
    library: list[dict],
    seen_ids: set[str],
    *,
    limit: int = 24,
    balance: int = 65,
    semantic_scores: dict[str, dict[str, float]] | None = None,
    context_scores: dict[str, dict[str, float]] | None = None,
    subscribed_channel_ids: set[str] | None = None,
) -> list[dict]:
    """Rank candidate videos using explainable bounded features and greedy variety.

    Features (not trained parameters): topic affinity, positive/negative title
    similarity, creator affinity, search position, corroborating queries, and
    diminishing topic/creator exposure. The balance parameter only scales
    reranking penalties; base relevance features and discovery remain unchanged.
    Scores are not probabilities.
    """
    if not isinstance(balance, int) or not 0 <= balance <= 100:
        raise ValueError("Feed balance must be an integer from 0 to 100")
    # At 65, preserve the original v0.7 penalties exactly.
    # Focus (0) strongly prefers relevance; Explore (100) increases variety.
    diversity_multiplier = (
        0.2 + 0.8 * balance / 65
        if balance <= 65 else
        1.0 + 0.8 * (balance - 65) / 35
    )
    pool: dict[str, dict] = {}
    for topic, results in batches:
        for position, video in enumerate(results):
            vid = video.get("id")
            if (not isinstance(vid, str) or vid in seen_ids
                    or not VIDEO_ID.fullmatch(vid)):
                continue
            if vid in pool:
                pool[vid]["_topics"].add(topic)
                if not pool[vid].get("channel") and video.get("channel"):
                    pool[vid]["channel"] = video["channel"]
                continue
            pool[vid] = {
                **video,
                "_topics": {topic},
                "_position": position,
                "_main_topic": topic,
            }
    if not pool:
        return []

    seeds = [(video, _seed_weight(video)) for video in library]
    positives = [(v, weight) for v, weight in seeds if weight > 0]
    # Preserve the negative sign: disliked titles must penalize, not reward, matches.
    negatives = [(v, weight) for v, weight in seeds if weight < 0]
    positives.sort(key=lambda item: -item[1])
    seeds = positives[:30] + negatives[:15]
    pool_ids = list(pool)
    document_tokens = [words(pool[vid].get("title", "")) for vid in pool_ids]
    document_tokens += [words(v.get("title", "")) for v, _ in seeds]
    vectors = _tfidf(document_tokens)

    max_topic = max((max(0.0, value) for value in profile["tags"].values()), default=1.0)
    max_topic = max(1.0, max_topic)
    creator_weights = profile.get("creators", {})
    max_creator = max((max(0.0, value) for value in creator_weights.values()), default=1.0)
    max_creator = max(1.0, max_creator)
    features: dict[str, dict] = {}

    for i, vid in enumerate(pool_ids):
        video = pool[vid]
        topic = max(
            (max(0.0, profile["tags"].get(t, 0)) for t in video["_topics"]),
            default=0.0,
        ) / max_topic
        channel_key = str(video.get("channel") or "").strip().casefold()
        creator = min(1.0, max(0.0, creator_weights.get(channel_key, 0)) / max_creator)
        positive = 0.0
        negative = 0.0
        for j, (_seed, weight) in enumerate(seeds):
            match = _cosine(vectors[i], vectors[len(pool_ids) + j])
            if weight > 0:
                positive = max(positive, match * weight)
            else:
                negative = max(negative, match)
        positive = min(1.0, positive)
        agreement = min(1.0, (len(video["_topics"]) - 1) / 2.0)
        discovery = max(0.0, 1.0 - video["_position"] / 18.0)
        # Scores are interpretable weighted contributions, not fitted weights.
        features[vid] = {
            "topic": round(4.0 * topic, 3),
            "title": round(3.5 * positive, 3),
            "creator": round(2.2 * creator, 3),
            "discovery": round(0.5 * discovery, 3),
            "agreement": round(0.3 * agreement, 3),
            "disliked_similarity": round(-2.0 * negative, 3),
        }
        if semantic_scores is not None:
            # Do not mutate the caller's feature dictionary.
            semantic = semantic_scores.get(vid, {})
            features[vid]["semantic_positive"] = round(
                float(semantic.get("semantic_positive", 0.0)), 3)
            features[vid]["semantic_negative"] = round(
                float(semantic.get("semantic_negative", 0.0)), 3)
        if context_scores is not None:
            # Research-only semantic disambiguation. Missing context is neutral
            # and never changes old v0.8/v0.9 ranking behavior.
            context = context_scores.get(vid, {})
            for key in ("interest_alignment", "disinterest_contrast"):
                if key in context:
                    features[vid][key] = round(float(context[key]), 3)
        if subscribed_channel_ids is not None:
            # v0.12: explicit preference for an exact stable channel ID.
            # This is an input feature, NOT a positive watch label: the
            # separate trainable ML ranker can learn its predictive value.
            features[vid]["subscription_prior"] = (
                1.6 if video.get("channel_id") in subscribed_channel_ids else 0.0)

    selected: list[dict] = []
    topic_count: dict[str, int] = defaultdict(int)
    channel_count: dict[str, int] = defaultdict(int)
    while pool and len(selected) < limit:
        best_id, best_score = None, -float("inf")
        for vid, video in pool.items():
            parts = features[vid]
            # The same topic and creator become less attractive as slots fill.
            diversity = 1.6 * diversity_multiplier * topic_count[video["_main_topic"]]
            creator_key = str(video.get("channel") or "").strip().casefold()
            if creator_key:
                diversity += 4.0 * diversity_multiplier * channel_count[creator_key]
            score = sum(parts.values()) - diversity
            if score > best_score:
                best_id, best_score = vid, score
        video = pool.pop(best_id)
        topic_count[video["_main_topic"]] += 1
        creator_key = str(video.get("channel") or "").strip().casefold()
        if creator_key:
            channel_count[creator_key] += 1
        parts = {**features[best_id], "diversity_penalty": round(
            best_score - sum(features[best_id].values()), 3)}
        # Explain the most specific evidence rather than always naming a broad
        # topic: the topic score can be largest even for an exact-title match.
        if parts.get("semantic_positive", 0) >= 1.2:
            reason = "Similar in meaning to videos you enjoyed"
        elif parts["title"] >= 1.4:
            reason = "Similar to videos you enjoyed"
        elif parts["creator"] >= 1.1:
            reason = "From a creator you've watched"
        elif parts["topic"] > 0:
            reason = "Based on your interest in " + video["_main_topic"]
        else:
            reason = "Explore a new video"
        sources = sorted(video.pop("_topics"))
        video.pop("_position")
        video.pop("_main_topic")
        video["tags"] = [tag[:30] for tag in sources[:8]]
        video["reason"] = reason
        video["score"] = round(best_score, 3)
        video["score_breakdown"] = parts
        selected.append(video)
    return selected
