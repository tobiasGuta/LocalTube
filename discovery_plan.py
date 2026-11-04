"""Build bounded, account-free YouTube search queries from local preferences.

Privacy: queries reach YouTube via yt-dlp. Avoid transmitting whole saved titles:
we only use a few meaningful keywords from a positively rated/actually watched
video, or an already-public creator name. This is heuristic candidate generation,
not a machine-learning model.
"""
from __future__ import annotations

import re

from recommender import STOPWORDS, TOKENS, topics_to_search

MAX_QUERIES = 5
MAX_QUERY_LENGTH = 80
VARIANTS = ("", " latest", " highlights", " 2026", " tutorial")


def _clean_query(raw: str) -> str:
    return " ".join(str(raw).split())[:MAX_QUERY_LENGTH].strip()


def _seed_strength(video: dict) -> float:
    if video.get("rating") == -1:
        return -1
    rating = int(video.get("rating") or 0)
    watched = max(0.0, float(video.get("watched_seconds") or 0))
    completions = min(3, int(video.get("completions") or 0))
    # Merely saving a link should not trigger a private-title search.
    if rating != 1 and watched < 60 and completions == 0:
        return 0
    return (5.0 if rating == 1 else 0) + min(4, watched / 180) + completions


def _title_keywords(title: str) -> str:
    """Use at most four discriminative words, not the original full title."""
    terms = [
        token for token in TOKENS.findall(title.lower())
        if len(token) >= 3 and token not in STOPWORDS
    ]
    return _clean_query(" ".join(dict.fromkeys(terms[:4])))


def build_queries(profile: dict, library: list[dict], cursor: int, *,
                  include_refined: bool = True) -> list[tuple[str, str]]:
    """Return (associated-topic, actual-search-query), capped and deduplicated.

    Prioritize selected/learned topics, then one liked-video keyword query and
    one creator query. No background or scheduled YouTube requests.
    """
    topics = topics_to_search(profile, limit=3)
    if not topics:
        return []

    result: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(topic: str, query: str) -> None:
        cleaned = _clean_query(query)
        key = cleaned.casefold()
        if (2 <= len(cleaned) <= MAX_QUERY_LENGTH and key not in seen
                and len(result) < MAX_QUERIES):
            seen.add(key)
            result.append((topic, cleaned))

    suffix = VARIANTS[cursor % len(VARIANTS)]
    for topic in topics:
        add(topic, topic + suffix)

    if not include_refined:
        return result

    seeds = sorted(
        (video for video in library if _seed_strength(video) > 0),
        key=lambda video: (-_seed_strength(video),
                           str(video.get("id", ""))),
    )
    for video in seeds:
        keywords = _title_keywords(str(video.get("title") or ""))
        if len(keywords.split()) >= 2:
            tags = video.get("tags") or []
            topic = next((t for t in tags if t in topics), topics[0])
            add(topic, keywords)
            break

    for video in seeds:
        creator = re.sub(r"[^a-zA-Z0-9 _-]+", " ", str(video.get("channel") or ""))
        creator = " ".join(creator.split())[:45]
        if len(creator) >= 3 and creator.lower() != "unknown creator":
            tags = video.get("tags") or []
            topic = next((t for t in tags if t in topics), topics[0])
            add(topic, creator + " " + topic)
            break

    return result
