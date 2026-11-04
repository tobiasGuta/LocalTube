"""Explainable, local-first content ranking for LocalTube.

No ML downloads or external services. Signals include likes, approximate
playing time, completions, repeat opens, creator affinity and title words.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone

TOKENS = re.compile(r"[a-z0-9]+(?:[+#-][a-z0-9]+)*")
STOPWORDS = frozenset("""
a an the and or but to of in on for with from by as at is are was were be been
being have has had do does did my your you me this that these those it its i we
he she they them their one two three four five new full video videos youtube
watch playing play game gameplay games how why when what where who all more very
best most really ever first last part not no vs about into up out get got
""".split())


def words(text: str) -> set[str]:
    """Ignore generic words so a like doesn't boost unrelated videos."""
    return {word for word in TOKENS.findall(str(text).lower())
            if len(word) > 2 and word not in STOPWORDS}


def _recent_factor(video: dict, now: datetime) -> float:
    value = video.get("last_activity_at") or video.get("created_at")
    if not isinstance(value, str):
        return 1.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - dt).total_seconds() / 86400)
        return max(0.3, 0.5 ** (days / 60.0))
    except ValueError:
        return 1.0


def build_profile(topics: list[str], library: list[dict], *, now: datetime | None = None) -> dict:
    """Create a lightweight preference profile from user-controlled local data."""
    now = now or datetime.now(timezone.utc)
    tag_weights: dict[str, float] = defaultdict(float)
    text_weights: dict[str, float] = defaultdict(float)
    creator_weights: dict[str, float] = defaultdict(float)
    liked: set[str] = set()
    watched: set[str] = set()
    played: set[str] = set()
    chosen = {topic.strip().lower() for topic in topics if topic.strip()}

    for topic in chosen:
        tag_weights[topic] += 10.0  # Explicit choices outrank weak inferred signals.

    for video in library:
        tags = [tag.strip().lower() for tag in video.get("tags", []) if tag.strip()]
        rating = int(video.get("rating", 0))
        plays = max(0, min(8, int(video.get("plays", 0))))
        watched_seconds = max(0.0, min(14400.0, float(video.get("watched_seconds") or 0)))
        completions = max(0, min(4, int(video.get("completions") or 0)))
        creator = str(video.get("channel") or "").strip().casefold()
        fresh = _recent_factor(video, now)
        if rating == -1:
            # A dislike should not suppress an entire topic on its own.
            for tag in tags:
                tag_weights[tag] -= 1.5 * fresh
            for term in words(video.get("title", "")):
                text_weights[term] -= 0.4 * fresh
            continue
        watch_signal = min(8.0, math.log1p(watched_seconds / 60.0) * 2.0)
        boost = fresh * (0.6 + math.log1p(plays) * 1.3 + watch_signal
                         + 2.5 * completions + (6.0 if rating == 1 else 0))
        if creator and (rating == 1 or watched_seconds >= 60):
            creator_weights[creator] += min(14.0, boost)
        for tag in tags:
            tag_weights[tag] += boost
            if rating == 1:
                liked.add(tag)
            if plays:
                played.add(tag)
            if watched_seconds > 0:
                watched.add(tag)
        if plays or rating == 1 or watched_seconds > 0:
            for term in words(video.get("title", "")):
                text_weights[term] += fresh * (0.35 * math.log1p(plays) + (1.6 if rating == 1 else 0))

    return {"tags": dict(tag_weights), "terms": dict(text_weights),
            "creators": dict(creator_weights), "chosen": chosen,
            "liked": liked, "played": played, "watched": watched}


def topics_to_search(profile: dict, limit: int = 3) -> list[str]:
    """Choose a few promising subjects; prefer real interests over dislikes."""
    ranking = [(tag, value) for tag, value in profile["tags"].items()
               if value >= 0.5 and 2 <= len(tag) <= 60]
    ranking.sort(key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranking[:limit]]


def _reason(tag: str, profile: dict) -> str:
    if tag in profile["liked"]:
        return f"Because you liked {tag} videos"
    if tag in profile.get("watched", set()):
        return f"Because you watched {tag} videos"
    if tag in profile["played"]:
        return f"Based on your {tag} play clicks"
    if tag in profile["chosen"]:
        return f"Because you chose {tag}"
    return f"Explore more {tag}"


def rank_feed(batches: list[tuple[str, list[dict]]], profile: dict,
              seen_ids: set[str], limit: int = 18) -> list[dict]:
    """Relevance with topic/channel variety and deduplication, stable ordering."""
    pool: dict[str, dict] = {}
    for topic, results in batches:
        for position, video in enumerate(results):
            vid = video.get("id")
            if not isinstance(vid, str) or vid in seen_ids or vid in pool:
                continue
            pool[vid] = {**video, "tags": [topic[:30]], "_position": position,
                         "_topic": topic}
    output: list[dict] = []
    topic_counts: dict[str, int] = defaultdict(int)
    channel_counts: dict[str, int] = defaultdict(int)

    while pool and len(output) < limit:
        best_id, best_score = None, -float("inf")
        for vid, item in pool.items():
            topic = item["_topic"]
            channel = (item.get("channel") or "").strip().casefold()
            interest = max(0.0, profile["tags"].get(topic, 0.0))
            # Strong favorites matter, but their value diminishes as the feed fills.
            interest /= 1.0 + 1.6 * topic_counts[topic]
            matches = sum(profile["terms"].get(w, 0.0)
                          for w in words(item.get("title", "")))
            matches = max(-6.0, min(10.0, matches))
            creator_affinity = min(7.0, profile.get("creators", {}).get(channel, 0.0))
            score = 1.9 * interest + matches + creator_affinity
            if channel:
                score -= 5.0 * channel_counts[channel]
            score -= item["_position"] * 0.025
            if score > best_score:
                best_id, best_score = vid, score
        chosen = pool.pop(best_id)
        topic = chosen.pop("_topic")
        chosen.pop("_position")
        channel = (chosen.get("channel") or "").strip().casefold()
        topic_counts[topic] += 1
        if channel:
            channel_counts[channel] += 1
        chosen["reason"] = _reason(topic, profile)
        output.append(chosen)
    return output
