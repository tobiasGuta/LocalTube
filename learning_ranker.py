"""v0.11 experimental *trained* local ranking model.

Use only an observed recommendation's PRE-INTERACTION feature snapshot and
subsequent confirmed watch/like/dislike. Never train on impressions without
outcomes, clicks alone, short exits, or videos that were not shown.

No network, model downloads, sklearn or hardcoded topic/creator names.
A tiny regularized logistic regression is refit deterministically whenever
a genuine outcome changes; the saved weights then affect subsequent ML feeds.
This is not a two-tower model or an unbiased estimate of user satisfaction.
"""
from __future__ import annotations

import json
import math
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

FEATURE_SCALES = {
    "topic": 4.0,
    "title": 3.5,
    "creator": 2.2,
    "discovery": 0.5,
    "agreement": 0.3,
    "disliked_similarity": 2.0,
    "semantic_positive": 3.6,
    "semantic_negative": 3.8,
    "interest_alignment": 3.0,
    "disinterest_contrast": 4.0,
    "subscription_prior": 1.6,
}
FEATURES = tuple(FEATURE_SCALES)
MIN_POSITIVES = 3
MIN_NEGATIVES = 3
MIN_LABELS = 8
MAX_TRAINING = 400
MAX_IMPRESSIONS = 2000
MAX_IMPRESSION_AGE = timedelta(days=14)
MAX_DELTA = 1.25


def initialize(db: sqlite3.Connection) -> None:
    """Non-destructive, idempotent migration. Does not touch older tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS ml_impressions(
        token TEXT PRIMARY KEY, video_id TEXT NOT NULL,
        created_at TEXT NOT NULL, seen_at TEXT,
        features_json TEXT NOT NULL
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS ml_impressions_video
        ON ml_impressions(video_id, seen_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ml_outcomes(
        video_id TEXT PRIMARY KEY, token TEXT NOT NULL,
        label INTEGER NOT NULL CHECK(label IN (0,1)),
        source TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS ml_model(
        id INTEGER PRIMARY KEY CHECK(id=1),
        weights_json TEXT NOT NULL, bias REAL NOT NULL,
        positives INTEGER NOT NULL, negatives INTEGER NOT NULL,
        trained INTEGER NOT NULL, updated_at TEXT NOT NULL
    )""")
    # v0.12 extends the frozen feature vector. Preserve existing real
    # outcomes and refit using zero for unavailable historical features,
    # instead of silently dropping all learned coefficients.
    row = db.execute(
        "SELECT weights_json FROM ml_model WHERE id=1").fetchone()
    if row:
        try:
            weights = json.loads(row["weights_json"])
        except (ValueError, TypeError):
            weights = []
        if not isinstance(weights, list) or len(weights) != len(FEATURES):
            retrain(db)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def vector(parts: dict) -> tuple[float, ...]:
    """Freeze bounded features with no downstream labels or learned scores."""
    values = []
    for key, scale in FEATURE_SCALES.items():
        try:
            raw = float(parts.get(key, 0.0))
        except (ValueError, TypeError):
            raw = 0.0
        if not math.isfinite(raw):
            raw = 0.0
        values.append(max(-1.0, min(1.0, raw / scale)))
    return tuple(values)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(50.0, x)))
    e = math.exp(max(-50.0, x))
    return e / (1.0 + e)


def fit(samples: list[tuple[tuple[float, ...], int]]) -> tuple[list[float], float]:
    """Deterministic SGD with L2 shrinkage; no dependence on external packages."""
    weights = [0.0] * len(FEATURES)
    bias = 0.0
    for epoch in range(30):
        rate = 0.12 / math.sqrt(1.0 + epoch * 0.35)
        for features, label in samples:
            err = _sigmoid(bias + sum(w * x for w, x in zip(weights, features))) - label
            for i, x in enumerate(features):
                weights[i] = max(-2.0, min(
                    2.0, weights[i] - rate * (err * x + 0.04 * weights[i])))
            bias = max(-2.0, min(2.0, bias - rate * err * 0.5))
    return weights, bias


def _samples(db: sqlite3.Connection) -> list[tuple[tuple[float, ...], int]]:
    rows = db.execute("""
        SELECT o.label, i.features_json
        FROM ml_outcomes o JOIN ml_impressions i ON o.token=i.token
        WHERE i.seen_at IS NOT NULL
        ORDER BY o.updated_at DESC, o.video_id DESC
        LIMIT ?
    """, (MAX_TRAINING,)).fetchall()
    samples = []
    for row in rows:
        try:
            raw = json.loads(row["features_json"])
            if not isinstance(raw, dict):
                continue
            samples.append((vector(raw), int(row["label"])))
        except (ValueError, TypeError):
            continue
    samples.reverse()  # Stable order; oldest first.
    return samples


def retrain(db: sqlite3.Connection) -> dict:
    samples = _samples(db)
    positives = sum(label == 1 for _, label in samples)
    negatives = len(samples) - positives
    ready = (len(samples) >= MIN_LABELS and
             positives >= MIN_POSITIVES and negatives >= MIN_NEGATIVES)
    weights, bias = fit(samples) if ready else ([0.0] * len(FEATURES), 0.0)
    db.execute("""INSERT INTO ml_model(
        id,weights_json,bias,positives,negatives,trained,updated_at)
        VALUES(1,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            weights_json=excluded.weights_json, bias=excluded.bias,
            positives=excluded.positives, negatives=excluded.negatives,
            trained=excluded.trained, updated_at=excluded.updated_at
    """, (json.dumps(weights), bias, positives, negatives,
          int(ready), _timestamp(_now())))
    return {"trained": ready, "positives": positives, "negatives": negatives,
            "examples": len(samples)}


def status(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT * FROM ml_model WHERE id=1").fetchone()
    if row is None:
        data = {"trained": False, "positives": 0, "negatives": 0, "examples": 0}
    else:
        data = {
            "trained": bool(row["trained"]),
            "positives": row["positives"],
            "negatives": row["negatives"],
            "examples": row["positives"] + row["negatives"],
        }
    data["exposures"] = db.execute(
        "SELECT COUNT(*) FROM ml_impressions WHERE seen_at IS NOT NULL"
    ).fetchone()[0]
    data["minimum_positive"] = MIN_POSITIVES
    data["minimum_negative"] = MIN_NEGATIVES
    data["minimum_total"] = MIN_LABELS
    data["message"] = (
        "Learned weights active; trained from local confirmed outcomes."
        if data["trained"] else
        "Collect real meaningful watches/likes and explicit Not interested "
        "feedback in v0.11. Unclicked impressions never count as negative."
    )
    return data


def predict(db: sqlite3.Connection, ranked: list[dict]) -> list[dict]:
    """Rank AFTER v0.10 semantic reranking. No weights => identical order."""
    row = db.execute("SELECT * FROM ml_model WHERE id=1").fetchone()
    if row is None or not row["trained"]:
        return ranked
    weights = json.loads(row["weights_json"])
    if (len(weights) != len(FEATURES) or
            not all(isinstance(w, (int, float)) and math.isfinite(w) for w in weights)):
        return ranked
    adjusted = []
    for order, item in enumerate(ranked):
        features = vector(item.get("score_breakdown", {}))
        probability = _sigmoid(float(row["bias"]) + sum(
            x * w for x, w in zip(features, weights)))
        delta = round(max(-MAX_DELTA, min(
            MAX_DELTA, 2.5 * (probability - 0.5))), 3)
        copy = dict(item)
        copy["score_breakdown"] = {
            **item.get("score_breakdown", {}), "ml_adjustment": delta,
        }
        copy["score"] = round(float(item["score"]) + delta, 3)
        copy["ml_probability"] = round(probability, 3)
        adjusted.append((order, copy))
    # The already-scored v0.10 diversity penalty remains in the score;
    # do not invent a new topic/creator mapping for the ML reranker.
    adjusted.sort(key=lambda pair: (-pair[1]["score"], pair[0]))
    return [item for _, item in adjusted]


def register(db: sqlite3.Connection, ranked: list[dict]) -> list[dict]:
    """Issue unguessable candidate tokens. Not an exposure until browser sees it."""
    timestamp = _timestamp(_now())
    result = []
    for item in ranked:
        token = secrets.token_urlsafe(18)
        db.execute(
            """INSERT INTO ml_impressions(
               token,video_id,created_at,features_json) VALUES(?,?,?,?)""",
            (token, item["id"], timestamp,
             json.dumps({k: item.get("score_breakdown", {}).get(k, 0)
                         for k in FEATURES}, separators=(",", ":"))),
        )
        result.append({**item, "impression_token": token})
    # Prune only after tokens have been assigned. Exposed but unlabeled
    # impressions are retained temporarily, never treated as dislikes.
    db.execute("""DELETE FROM ml_impressions WHERE token NOT IN (
        SELECT token FROM ml_impressions
        ORDER BY created_at DESC, token DESC LIMIT ?
    )""", (MAX_IMPRESSIONS,))
    removed = db.execute("""DELETE FROM ml_outcomes
        WHERE token NOT IN (SELECT token FROM ml_impressions)""")
    if removed.rowcount:
        retrain(db)  # Old pruned labels must never leave stale trained weights.
    return result


def mark_seen(db: sqlite3.Connection, tokens: list[str]) -> int:
    """Idempotent viewport/client exposure. This is NEVER a training label."""
    if not tokens:
        return 0
    stamp = _timestamp(_now())
    count = 0
    for token in set(tokens):
        if not isinstance(token, str) or not 16 <= len(token) <= 64:
            continue
        updated = db.execute(
            """UPDATE ml_impressions SET seen_at=?
               WHERE token=? AND seen_at IS NULL""",
            (stamp, token),
        )
        count += updated.rowcount
    return count


def record_outcome(db: sqlite3.Connection, video_id: str, label: int | None,
                   source: str) -> bool:
    """Associate real feedback with latest *previously seen* feature snapshot.

    Explicit feedback overrides passive watches. A removed explicit rating
    clears the corresponding label; it cannot silently remain negative.
    """
    prev = db.execute(
        "SELECT label, source, token FROM ml_outcomes WHERE video_id=?",
        (video_id,),
    ).fetchone()
    if label is None:
        if prev is None or prev["source"] == "watch":
            return False
        db.execute("DELETE FROM ml_outcomes WHERE video_id=?", (video_id,))
        retrain(db)
        return True
    if label not in (0, 1):
        raise ValueError("label must be 0 or 1")
    if source == "watch" and prev and prev["source"] in ("like", "dislike"):
        return False
    cutoff = _timestamp(_now() - MAX_IMPRESSION_AGE)
    sample = db.execute("""
        SELECT token FROM ml_impressions
        WHERE video_id=? AND seen_at IS NOT NULL AND seen_at>=?
        ORDER BY seen_at DESC, token DESC LIMIT 1
    """, (video_id, cutoff)).fetchone()
    if sample is None:
        return False
    if (prev and prev["token"] == sample["token"]
            and prev["label"] == label and prev["source"] == source):
        return False
    db.execute("""INSERT INTO ml_outcomes(
        video_id,token,label,source,updated_at) VALUES(?,?,?,?,?)
        ON CONFLICT(video_id) DO UPDATE SET
            token=excluded.token, label=excluded.label, source=excluded.source,
            updated_at=excluded.updated_at
    """, (video_id, sample["token"], label, source, _timestamp(_now())))
    retrain(db)
    return True


def forget_video(db: sqlite3.Connection, video_id: str) -> None:
    db.execute("DELETE FROM ml_outcomes WHERE video_id=?", (video_id,))
    db.execute("DELETE FROM ml_impressions WHERE video_id=?", (video_id,))
    retrain(db)


def reset(db: sqlite3.Connection) -> None:
    """Clear ONLY the v0.11 training history and learned weights."""
    db.execute("DELETE FROM ml_outcomes")
    db.execute("DELETE FROM ml_impressions")
    db.execute("DELETE FROM ml_model")
