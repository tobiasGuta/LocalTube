"""Optional LOCAL semantic retrieval for LocalTube.

The pretrained encoder is not fine-tuned. Instead, LocalTube learns a weighted
collection of your liked, watched and disliked video examples. Cosine similarity
between embeddings of video titles is a ranking feature, not a prediction of
watch probability. See docs/SEMANTIC_ENGINE.md for the full explanation.

Safety contract:
- Importing this module never downloads anything or imports fastembed.
- Only the explicit CLI command "python -m semantic_engine download" downloads.
- Running inference sets local_files_only=True; embeddings stay on disk in SQLite.
- No scripts, LLM calls or remote embedding endpoints are required.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import struct
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from discovery_plan import _title_keywords
from recommender import words

MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_DIM = 384
# Changing the model/feature representation requires a different cache namespace.
CACHE_REVISION = "v0.9-title-only:" + MODEL_ID
CACHE_DIR = Path(__file__).resolve().parent / "data" / "models"
READY_FILE = CACHE_DIR / ".localtube-ready.json"
MAX_POSITIVE_EXAMPLES = 24
MAX_NEGATIVE_EXAMPLES = 12
MAX_EMBED_BATCH = 32

_model = None
_model_lock = threading.RLock()


class ModelUnavailable(RuntimeError):
    """The optional local encoder is not installed or prepared."""


def status() -> dict:
    """Cheap health check, never imports a model or triggers downloads."""
    installed = importlib.util.find_spec("fastembed") is not None
    prepared = False
    if READY_FILE.is_file():
        try:
            prepared = json.loads(READY_FILE.read_text(encoding="utf-8")).get("model") == MODEL_ID
        except (OSError, ValueError):
            prepared = False
    ready = installed and prepared
    return {
        "ready": ready,
        "installed": installed,
        "prepared": prepared,
        "model": MODEL_ID,
        "dimension": MODEL_DIM,
        "message": (
            "Local semantic model ready; inference stays on Fedora."
            if ready else
            "Optional setup: pip install -r requirements-semantic.txt, then "
            "python -m semantic_engine download, and restart LocalTube."
        ),
    }


def _new_encoder(*, download: bool):
    """FastEmbed/ONNX on CPU. Offline by default even when HF is reachable."""
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise ModelUnavailable("Install requirements-semantic.txt first") from exc
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=MODEL_ID, cache_dir=str(CACHE_DIR),
        threads=2, local_files_only=not download,
        providers=["CPUExecutionProvider"],
    )


def download_model() -> None:
    """Explicit user action: may contact Hugging Face to obtain model files."""
    global _model
    print(f"Downloading {MODEL_ID} to {CACHE_DIR} (one-time, opt-in).")
    model = _new_encoder(download=True)
    vectors = list(model.embed(["LocalTube semantic model readiness check"]))
    if len(vectors) != 1 or len(vectors[0]) != MODEL_DIM:
        raise RuntimeError("Unexpected embedding dimensions; refusing to mark ready")
    READY_FILE.write_text(json.dumps({"model": MODEL_ID, "dimension": MODEL_DIM}),
                          encoding="utf-8")
    with _model_lock:
        _model = model
    print("Ready. Restart LocalTube, then select v0.9 Semantic in Ranking.")


def get_model():
    """Load only a locally prepared model. Never fetch models at feed time."""
    global _model
    if not status()["ready"]:
        raise ModelUnavailable(status()["message"])
    with _model_lock:
        if _model is None:
            try:
                _model = _new_encoder(download=False)
            except Exception as exc:
                raise ModelUnavailable(
                    "The local model cache could not be loaded; rerun "
                    "python -m semantic_engine download."
                ) from exc
        return _model


def _normalized(vector) -> tuple[float, ...]:
    floats = tuple(float(x) for x in vector)
    if len(floats) != MODEL_DIM or not all(math.isfinite(x) for x in floats):
        raise ValueError("Embedding dimension or values are invalid")
    magnitude = math.sqrt(sum(x * x for x in floats))
    return tuple(x / magnitude for x in floats) if magnitude else (0.0,) * MODEL_DIM


def _unpack(blob: bytes) -> tuple[float, ...]:
    if len(blob) != MODEL_DIM * 4:
        raise ValueError("Cached embedding length is invalid")
    return _normalized(struct.unpack(f"<{MODEL_DIM}f", blob))


def _key(text: str) -> str:
    return hashlib.sha256((CACHE_REVISION + "\0" + text).encode("utf-8")).hexdigest()


def vectors_for(texts: list[str], connect_db: Callable, *, model=None) -> list[tuple[float, ...]]:
    """Lazily embed unique titles and persist normalized float32 vectors in SQLite.

    Keys contain only hashes; original titles are not duplicated in this table.
    All embeddings are generated in the same local process with the cached model.
    """
    if not texts:
        return []
    encoder = model if model is not None else get_model()
    keys = [_key(text) for text in texts]
    by_key = {}
    with connect_db() as db:
        for key in dict.fromkeys(keys):
            row = db.execute(
                "SELECT vector FROM semantic_vectors WHERE cache_key=?", (key,)
            ).fetchone()
            if row:
                try:
                    by_key[key] = _unpack(row["vector"])
                except (ValueError, TypeError, struct.error):
                    pass

    missing = {}
    for key, text in zip(keys, texts):
        if key not in by_key:
            missing[key] = text
    items = list(missing.items())
    for offset in range(0, len(items), MAX_EMBED_BATCH):
        chunk = items[offset:offset + MAX_EMBED_BATCH]
        with _model_lock:
            created = list(encoder.embed([text for _, text in chunk]))
        if len(created) != len(chunk):
            raise ValueError("Local embedding model returned the wrong number of vectors")
        with connect_db() as db:
            for (key, _), raw in zip(chunk, created):
                vec = _normalized(raw)
                by_key[key] = vec
                packed = struct.pack(f"<{MODEL_DIM}f", *vec)
                db.execute(
                    """INSERT INTO semantic_vectors(cache_key, model, vector, updated_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        model=excluded.model, vector=excluded.vector,
                        updated_at=excluded.updated_at""",
                    (key, CACHE_REVISION, packed,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
    return [by_key[key] for key in keys]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def preference_weight(video: dict) -> float:
    """Signed behavior signal. A like is explicit; watch time is ambiguous.

    Repeated opens alone do not create a positive training example.
    Duration-normalized watch progress reduces bias toward long videos.
    """
    rating = int(video.get("rating") or 0)
    if rating == -1:
        return -1.0
    watched = max(0.0, float(video.get("watched_seconds") or 0.0))
    completions = max(0, min(3, int(video.get("completions") or 0)))
    if rating != 1 and watched < 45 and completions == 0:
        return 0.0
    duration = max(0.0, float(video.get("duration_seconds") or 0.0))
    watch_fraction = min(1.0, watched / duration) if duration > 0 else 0.0
    time_bonus = min(0.25, math.log1p(watched / 60.0) * 0.09)
    value = (1.0 if rating == 1 else 0.4) + 0.3 * watch_fraction + time_bonus
    value += min(0.3, completions * 0.15)
    return min(1.6, value)


def examples_from(library: list[dict]) -> list[tuple[dict, float]]:
    positives, negatives = [], []
    for video in library:
        if not str(video.get("title") or "").strip():
            continue
        strength = preference_weight(video)
        if strength > 0:
            positives.append((video, strength))
        elif strength < 0:
            negatives.append((video, strength))
    positives.sort(key=lambda pair: (-pair[1], str(pair[0].get("id", ""))))
    negatives.sort(key=lambda pair: str(pair[0].get("id", "")))
    return positives[:MAX_POSITIVE_EXAMPLES] + negatives[:MAX_NEGATIVE_EXAMPLES]


def _candidate_videos(batches: list[tuple[str, list[dict]]]) -> list[dict]:
    """Deduplicate candidates while preserving which search topic found each."""
    unique = {}
    for topic, results in batches:
        for video in results:
            if not isinstance(video.get("id"), str) or not isinstance(video.get("title"), str):
                continue
            if video["id"] not in unique:
                unique[video["id"]] = {**video, "_search_topics": [topic]}
            elif topic not in unique[video["id"]]["_search_topics"]:
                unique[video["id"]]["_search_topics"].append(topic)
    return list(unique.values())


def semantic_features(batches: list[tuple[str, list[dict]]],
                      library: list[dict], connect_db: Callable,
                      *, model=None) -> tuple[dict[str, dict[str, float]], int]:
    """Compare candidate meanings to weighted user examples; don't train the encoder.

    A MAX similarity across positive examples preserves distinct interests:
    liking games and learning doesn't average them into an incoherent one-vector.
    """
    seeds = examples_from(library)
    candidates = _candidate_videos(batches)
    if not candidates or not seeds:
        return {}, len(seeds)
    n = len(candidates)
    vectors = vectors_for(
        [v["title"] for v in candidates] + [v["title"] for v, _ in seeds],
        connect_db, model=model,
    )
    features = {}
    for index, candidate in enumerate(candidates):
        positive = negative = 0.0
        for j, (_saved, weight) in enumerate(seeds):
            similarity = max(0.0, _cosine(vectors[index], vectors[n + j]))
            if weight > 0:
                positive = max(positive, similarity * min(weight, 1.6))
            else:
                negative = max(negative, similarity)
        # A low resemblance isn't strong evidence of preference.
        features[candidate["id"]] = {
            "semantic_positive": round(3.6 * max(0.0, positive - 0.2), 3),
            "semantic_negative": round(-3.8 * max(0.0, negative - 0.15), 3),
        }
    return features, len(seeds)


def suggest_expansion(batches: list[tuple[str, list[dict]]],
                      library: list[dict], used_queries: set[str],
                      connect_db: Callable, *, model=None) -> tuple[str, str] | None:
    """Optional one-hop discovery from a semantically related public video title.

    Only after explicit refined-search opt-in. Searches still contact YouTube.
    The candidate title is public metadata; query is its first 4 salient words.
    No hard-coded category taxonomy, no LLM-generated searches.
    """
    positives = [(v, weight) for v, weight in examples_from(library) if weight > 0]
    candidates = _candidate_videos(batches)
    if not positives or not candidates:
        return None
    n = len(candidates)
    vectors = vectors_for(
        [v["title"] for v in candidates] + [v["title"] for v, _ in positives],
        connect_db, model=model,
    )
    best = None
    for i, candidate in enumerate(candidates):
        query = _title_keywords(candidate["title"])
        if (len(query.split()) < 2 or query.casefold() in used_queries):
            continue
        for j, (saved, weight) in enumerate(positives):
            seed_words = words(saved["title"])
            candidate_words = words(candidate["title"])
            union = seed_words | candidate_words
            lexical_overlap = len(seed_words & candidate_words) / len(union) if union else 0
            similarity = _cosine(vectors[i], vectors[n + j])
            # Require semantic relation without largely copying the same title.
            if similarity < 0.52 or lexical_overlap > 0.5:
                continue
            score = similarity * weight - lexical_overlap
            if best is None or score > best[0]:
                topic = next(
                    (t for t in candidate.get("_search_topics", [])
                     if isinstance(t, str)),
                    batches[0][0],
                )
                best = (score, topic, query)
    return (best[1], best[2]) if best else None


def clear_vector_cache(connect_db: Callable) -> int:
    """Remove all locally stored embedding vectors (not videos or watch history)."""
    with connect_db() as db:
        count = db.execute("SELECT COUNT(*) FROM semantic_vectors").fetchone()[0]
        db.execute("DELETE FROM semantic_vectors")
    return count


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["download"]:
        try:
            download_model()
            return 0
        except (ModelUnavailable, RuntimeError, ValueError) as exc:
            print(f"Model setup failed: {exc}", file=sys.stderr)
            return 1
    if args == ["status"]:
        print(json.dumps(status(), indent=2))
        return 0
    if args == ["clear-cache"]:
        from app import connect, init_db
        init_db()
        print(f"Removed {clear_vector_cache(connect)} stored vectors; saved videos untouched.")
        return 0
    print("Usage: python -m semantic_engine {download|status|clear-cache}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
