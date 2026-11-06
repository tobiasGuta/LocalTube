"""LocalTube: local-first video library and topic recommender."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from discovery import search_youtube, resolve_video_channel, channel_uploads
from recommender import build_profile, topics_to_search, rank_feed
from discovery_plan import build_queries
from content_ranker import rank_content
from contextual_relevance import contextual_scores
import learning_ranker as ml
import subscriptions as subs
from watch_learning import (
    meaningful_threshold, meaningful_sessions, watch_features,
    watch_profile_library, session_examples,
)
from semantic_engine import (
    ModelUnavailable, examples_from, get_model,
    semantic_features, status as semantic_status, suggest_expansion,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("LOCALTUBE_DB", str(ROOT / "data" / "localtube.db")))
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, tags TEXT NOT NULL,
            created_at TEXT NOT NULL, plays INTEGER NOT NULL DEFAULT 0,
            rating INTEGER NOT NULL DEFAULT 0 CHECK (rating IN (-1, 0, 1))
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS topics (
            topic TEXT PRIMARY KEY
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS discovery_cache (
            query TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL
        )""")
        # Non-destructive migration for databases created with v0.1/v0.2/v0.3.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(videos)")}
        if "last_activity_at" not in columns:
            db.execute("ALTER TABLE videos ADD COLUMN last_activity_at TEXT")

        additions = {
            "channel": "TEXT NOT NULL DEFAULT ''",
            "duration_seconds": "REAL NOT NULL DEFAULT 0",
            "watched_seconds": "REAL NOT NULL DEFAULT 0",
            "resume_position": "REAL NOT NULL DEFAULT 0",
            "completions": "INTEGER NOT NULL DEFAULT 0",
            "last_watched_at": "TEXT",
            "channel_id": "TEXT NOT NULL DEFAULT ''",
        }
        for column, sql_type in additions.items():
            if column not in columns:
                db.execute(f"ALTER TABLE videos ADD COLUMN {column} {sql_type}")
        db.execute("""CREATE TABLE IF NOT EXISTS feed_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            refresh_cursor INTEGER NOT NULL DEFAULT 0,
            last_refresh_at INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""INSERT OR IGNORE INTO feed_settings(id) VALUES(1)""")
        # v0.9: vectors are generated locally on-demand, never during migration.
        # Cache keys hash the text and include the model namespace.
        db.execute("""CREATE TABLE IF NOT EXISTS semantic_vectors (
            cache_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            vector BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        # v0.10 research only: local playback sessions; historical totals untouched.
        db.execute("""CREATE TABLE IF NOT EXISTS watch_sessions (
            session_id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
            started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            watched_seconds REAL NOT NULL DEFAULT 0,
            duration_seconds REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            last_sequence INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS watch_sessions_by_video
            ON watch_sessions(video_id, updated_at)""")
        ml.initialize(db)
        subs.initialize(db)



def parse_id(raw):
    raw = raw.strip()
    if VIDEO_ID.fullmatch(raw):
        return raw
    p = urlparse(raw)
    if p.scheme != "https" or p.username or p.password:
        raise ValueError("Use an HTTPS YouTube URL or a video ID")
    host = (p.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        value = p.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com",
                  "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"):
        parts = p.path.strip("/").split("/")
        if p.path == "/watch":
            value = parse_qs(p.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
            value = parts[1]
        else:
            value = ""
    else:
        value = ""
    if not VIDEO_ID.fullmatch(value):
        raise ValueError("This is not a supported YouTube video URL")
    return value


def as_video(row):
    return {
        "id": row["id"], "title": row["title"], "tags": json.loads(row["tags"]),
        "created_at": row["created_at"], "plays": row["plays"], "rating": row["rating"],
        "last_activity_at": row["last_activity_at"],
        "channel": row["channel"], "channel_id": row["channel_id"],
        "duration_seconds": row["duration_seconds"],
        "watched_seconds": row["watched_seconds"], "resume_position": row["resume_position"],
        "completions": row["completions"], "last_watched_at": row["last_watched_at"]
    }


class NewVideo(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=160)
    tags: str = Field(default="", max_length=300)
    channel: str = Field(default="", max_length=100)
    channel_id: str = Field(default="", pattern=r"^(?:|UC[A-Za-z0-9_-]{22})$")
    duration_seconds: float = Field(default=0.0, ge=0, le=86400)


class SubscribeRequest(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    channel_id: str | None = Field(
        default=None, pattern=r"^UC[A-Za-z0-9_-]{22}$")
    channel_name: str = Field(default="", max_length=100)


class PlayStart(BaseModel):
    session_id: str = Field(
        min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]{16,64}$")


class WatchProgress(BaseModel):
    session_id: str | None = Field(
        default=None, min_length=16, max_length=64,
        pattern=r"^[A-Za-z0-9_-]{16,64}$")
    sequence: int | None = Field(default=None, ge=1, le=10000000)

    position_seconds: float = Field(ge=0, le=86400)
    duration_seconds: float = Field(ge=0, le=86400)
    watched_delta: float = Field(ge=0, le=15)
    ended: bool = False

    @model_validator(mode="after")
    def valid_progress(self):
        if not all(math.isfinite(v) for v in
                   (self.position_seconds, self.duration_seconds, self.watched_delta)):
            raise ValueError("Progress values must be finite")
        if (self.session_id is None) != (self.sequence is None):
            raise ValueError("Session ID and sequence must be supplied together")
        if self.duration_seconds > 0 and self.position_seconds > self.duration_seconds + 3:
            raise ValueError("Position exceeds duration")
        return self


class Feedback(BaseModel):
    rating: int = Field(ge=-1, le=1)


class Exposures(BaseModel):
    tokens: list[str] = Field(min_length=1, max_length=24)


class NewTopic(BaseModel):
    topic: str = Field(min_length=1, max_length=30)


@asynccontextmanager
async def lifespan(_):
    init_db()
    yield


app = FastAPI(title="LocalTube", version="0.9.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/videos")
def videos():
    with connect() as db:
        rows = db.execute("SELECT * FROM videos ORDER BY created_at DESC, id").fetchall()
    return [as_video(row) for row in rows]


@app.post("/api/videos", status_code=201)
def add_video(video: NewVideo):
    try:
        video_id = parse_id(video.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    title = video.title.strip()
    if not title:
        raise HTTPException(400, "Please enter a title")
    tags = list(dict.fromkeys(t.strip().lower() for t in video.tags.split(",") if t.strip()))
    if len(tags) > 8 or any(len(t) > 30 for t in tags):
        raise HTTPException(400, "Use at most eight tags of up to 30 characters")
    with connect() as db:
        try:
            db.execute("INSERT INTO videos(id,title,tags,created_at,channel,channel_id,duration_seconds) VALUES(?,?,?,?,?,?,?)", (
                video_id, title, json.dumps(tags),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                video.channel.strip(), video.channel_id, video.duration_seconds
            ))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "This video is already saved") from exc
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.post("/api/videos/{video_id}/play")
def play(video_id: str, start: PlayStart | None = None):
    """Legacy play counter plus an optional idempotent playback-session start."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as db:
        if not db.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone():
            raise HTTPException(404, "Video not found")
        first_open = True
        if start is not None:
            old = db.execute(
                "SELECT video_id FROM watch_sessions WHERE session_id=?",
                (start.session_id,),
            ).fetchone()
            if old and old["video_id"] != video_id:
                raise HTTPException(409, "Playback session belongs to another video")
            if old:
                first_open = False
            else:
                db.execute(
                    """INSERT INTO watch_sessions(
                        session_id,video_id,started_at,updated_at)
                        VALUES(?,?,?,?)""",
                    (start.session_id, video_id, stamp, stamp),
                )
                # Bound retained session data without deleting v0.9 watch history.
                db.execute("""DELETE FROM watch_sessions WHERE session_id NOT IN (
                    SELECT session_id FROM watch_sessions
                    ORDER BY updated_at DESC, session_id DESC LIMIT 3000
                )""")
        if first_open:
            db.execute(
                "UPDATE videos SET plays=plays+1, last_activity_at=? WHERE id=?",
                (stamp, video_id),
            )
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.put("/api/videos/{video_id}/feedback")
def feedback(video_id: str, value: Feedback):
    with connect() as db:
        changed = db.execute(
            "UPDATE videos SET rating=?, last_activity_at=? WHERE id=?",
            (value.rating, datetime.now(timezone.utc).isoformat(timespec="seconds"), video_id),
        )
        if not changed.rowcount:
            raise HTTPException(404, "Video not found")
        ml.record_outcome(
            db, video_id, 1 if value.rating == 1 else (
                0 if value.rating == -1 else None),
            "like" if value.rating == 1 else (
                "dislike" if value.rating == -1 else "unrate"),
        )
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())




@app.put("/api/videos/{video_id}/progress")
def update_progress(video_id: str, progress: WatchProgress):
    """Record playing-time deltas, not seeking or elapsed wall-clock time.

    The browser sends a heartbeat while the official IFrame API says PLAYING.
    The backend validates bounds. No video data is sent to other services.
    """
    with connect() as db:
        old = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Video not found")
        duration = progress.duration_seconds or old["duration_seconds"]
        if progress.ended and duration <= 0:
            raise HTTPException(422, "Cannot complete video without known duration")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        completed = bool(progress.ended)
        if progress.session_id is not None:
            session = db.execute(
                "SELECT * FROM watch_sessions WHERE session_id=?",
                (progress.session_id,),
            ).fetchone()
            if session is None:
                raise HTTPException(404, "Unknown playback session")
            if session["video_id"] != video_id:
                raise HTTPException(409, "Playback session belongs to another video")
            if progress.sequence <= session["last_sequence"]:
                # Retried/out-of-order heartbeat must never double count seconds.
                return as_video(old)
            total = session["watched_seconds"] + progress.watched_delta
            completed = bool(progress.ended and
                             total >= meaningful_threshold(duration) and
                             not session["completed"])
            db.execute(
                """UPDATE watch_sessions SET
                    watched_seconds=watched_seconds+?,
                    duration_seconds=?, completed=MAX(completed, ?),
                    updated_at=?, last_sequence=? WHERE session_id=?""",
                (progress.watched_delta, duration, int(completed), stamp,
                 progress.sequence, progress.session_id),
            )
            if total >= meaningful_threshold(duration) and old["rating"] != -1:
                # Confirmed session-level watch, not a play click or seek.
                ml.record_outcome(db, video_id, 1, "watch")
        position = 0.0 if completed else progress.position_seconds
        db.execute(
            """UPDATE videos
               SET duration_seconds=?, resume_position=?, watched_seconds=watched_seconds+?,
                   completions=completions+?, last_watched_at=?, last_activity_at=?
               WHERE id=?""",
            (duration, position, progress.watched_delta, int(completed),
             stamp, stamp, video_id),
        )
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.get("/api/history")
def watch_history():
    with connect() as db:
        rows = db.execute(
            """SELECT * FROM videos WHERE last_watched_at IS NOT NULL
               ORDER BY last_watched_at DESC, watched_seconds DESC, id"""
        ).fetchall()
    return [as_video(row) for row in rows]


@app.get("/api/stats")
def watch_stats():
    """Aggregated watch seconds from progress heartbeats, all stored locally."""
    saved = videos()
    topics, channels = {}, {}
    for video in saved:
        seconds = video["watched_seconds"]
        if seconds <= 0:
            continue
        for tag in video["tags"]:
            topics[tag] = topics.get(tag, 0) + seconds
        if video["channel"]:
            channels[video["channel"]] = channels.get(video["channel"], 0) + seconds
    def summarize(data):
        return [{"name": name, "watched_seconds": round(seconds)}
                for name, seconds in sorted(data.items(), key=lambda x: (-x[1], x[0]))[:8]]
    return {
        "total_watched_seconds": round(sum(v["watched_seconds"] for v in saved)),
        "total_completions": sum(v["completions"] for v in saved),
        "topics": summarize(topics),
        "creators": summarize(channels),
        "note": "Approximate playback time; seeking and buffering aren't counted as watches.",
    }

@app.delete("/api/videos/{video_id}", status_code=204)
def delete(video_id: str):
    with connect() as db:
        if not db.execute("DELETE FROM videos WHERE id=?", (video_id,)).rowcount:
            raise HTTPException(404, "Video not found")
        db.execute("DELETE FROM watch_sessions WHERE video_id=?", (video_id,))
        ml.forget_video(db, video_id)


def _watch_sessions() -> list[dict]:
    with connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM watch_sessions ORDER BY updated_at DESC LIMIT 3000"
        )]


@app.get("/api/watch-learning/stats")
def watch_learning_stats():
    """Aggregate local-only progress; does not load an ML model or contact YouTube."""
    sessions = _watch_sessions()
    samples = session_examples(videos(), sessions)
    return {
        "sessions": len(sessions),
        "meaningful_sessions": len(meaningful_sessions(sessions)),
        "positive_examples": sum(weight > 0 for _, weight in samples),
        "negative_examples": sum(weight < 0 for _, weight in samples),
        "note": "Short exits are neutral. Existing v0.9 watch totals are retained.",
    }


@app.delete("/api/watch-learning/sessions", status_code=204)
def clear_watch_learning_sessions():
    """Explicitly clear session learning, never the pre-existing watch library."""
    with connect() as db:
        db.execute("DELETE FROM watch_sessions")


@app.get("/api/learning/status")
def learning_status():
    """Aggregate training diagnostics, entirely on-device."""
    with connect() as db:
        return ml.status(db)


@app.post("/api/learning/exposures")
def learning_exposures(value: Exposures):
    """Only confirm visible candidate tokens; unseen is not a dislike."""
    with connect() as db:
        return {"recorded": ml.mark_seen(db, value.tokens)}


@app.delete("/api/learning/data", status_code=204)
def clear_learning_data():
    """Reset only v0.11 model/candidate/labels, not past watch history."""
    with connect() as db:
        ml.reset(db)


def _channel_uploads_cached(channel_id: str, *, refresh: bool = False,
                            limit: int = 6) -> list[dict]:
    return subs.cached_uploads(
        connect, channel_id, channel_uploads, force=refresh, limit=limit)


@app.get("/api/subscriptions")
def subscriptions_list():
    with connect() as db:
        return subs.listing(db)


@app.post("/api/subscriptions")
def subscribe_creator(body: SubscribeRequest):
    """Local subscription, keyed by stable YouTube UC channel ID.

    No account/OAuth. If older search metadata lacks a channel ID, resolve
    the video's canonical creator on explicit user request only.
    """
    channel_id = body.channel_id
    name = body.channel_name.strip()
    if not channel_id:
        try:
            info = resolve_video_channel(body.video_id)
        except Exception as exc:
            raise HTTPException(
                502, "Could not verify this video's YouTube channel ID. "
                     "Try again later; no subscription was saved."
            ) from exc
        channel_id = info["channel_id"]
        name = info["name"]
    try:
        with connect() as db:
            record = subs.add(db, channel_id, name)
            # Attach the verified stable ID to this saved video if present;
            # don't turn subscribing into a play or implicit positive label.
            db.execute("UPDATE videos SET channel_id=? WHERE id=?",
                       (channel_id, body.video_id))
            return record
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/subscriptions/{channel_id}", status_code=204)
def unsubscribe_creator(channel_id: str):
    try:
        with connect() as db:
            if not subs.remove(db, channel_id):
                raise HTTPException(404, "Channel is not subscribed")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/subscriptions/feed")
def subscription_uploads(refresh: bool = False):
    """Chronological-ish creator feed, NOT gated by an ML prediction.

    Public YouTube upload timestamps may be absent, so order is best-effort.
    Any failed channel remains subscribed; no video is synthesized.
    """
    with connect() as db:
        channels = subs.listing(db)
        dismissed = {row[0] for row in db.execute(
            "SELECT id FROM videos WHERE rating=-1")}
    if not channels:
        return {"channels": [], "videos": [], "failed_channels": []}

    # Cap parallel YouTube work and reuse the same 15-minute SQLite cache
    # that supplies the v0.11 personalized feed.
    def fetch(channel):
        try:
            return channel, _channel_uploads_cached(
                channel["channel_id"], refresh=refresh, limit=6), False
        except Exception:
            return channel, [], True

    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(fetch, channels))
    failed = []
    videos_found = {}
    for channel, items, error in records:
        if error:
            failed.append(channel["channel_id"])
        for position, item in enumerate(items):
            vid = item.get("id")
            if vid in dismissed or vid in videos_found:
                continue
            videos_found[vid] = {
                **item,
                "channel_id": channel["channel_id"],
                "channel": channel["name"],
                "tags": ["subscriptions"],
                "reason": "New from your subscribed creator",
                "_position": position,
            }
    ranked = sorted(videos_found.values(), key=lambda item: (
        str(item.get("upload_date") or ""),
        int(item.get("upload_timestamp") or 0),
        -item["_position"],
    ), reverse=True)
    for item in ranked:
        item.pop("_position")
    return {"channels": channels, "videos": ranked[:120],
            "failed_channels": failed}


@app.get("/api/recommendations")
def recommendations():
    library = videos()
    interest = {}
    for video in library:
        # A click to play is an interest signal, not confirmed watch completion.
        weight = 0 if video["rating"] == -1 else (
            min(5, video["plays"]) + (3 if video["rating"] == 1 else 0)
        )
        for tag in video["tags"]:
            interest[tag] = interest.get(tag, 0) + weight

    ranked = []
    for video in library:
        if video["rating"] == -1:
            continue
        match = sum(interest.get(t, 0) for t in video["tags"])
        strongest = sorted(video["tags"], key=lambda t: (-interest.get(t, 0), t))
        reason = ("Because you explored " + strongest[0]
                  if strongest and interest.get(strongest[0], 0) else "New to your library")
        ranked.append({**video, "score": match + (2 if video["rating"] == 1 else 0),
                       "reason": reason})
    ranked.sort(key=lambda v: (v["plays"] > 0, -v["score"], v["title"].lower()))
    return ranked


# Search metadata is cached for one hour in the private local database.
DISCOVERY_CACHE_SECONDS = 3600


def _search_cached(query: str) -> list[dict]:
    query = " ".join(query.strip().split()).lower()
    if not 2 <= len(query) <= 80:
        raise HTTPException(422, "Search must be 2 to 80 characters")
    now = int(time.time())
    with connect() as db:
        cached = db.execute(
            "SELECT payload, fetched_at FROM discovery_cache WHERE query=?", (query,)
        ).fetchone()
    if cached and now - cached["fetched_at"] < DISCOVERY_CACHE_SECONDS:
        return json.loads(cached["payload"])
    try:
        results = search_youtube(query, limit=18)
    except Exception as exc:
        if cached:
            return json.loads(cached["payload"])
        raise HTTPException(
            502, "YouTube discovery is unavailable right now. Try again later."
        ) from exc
    with connect() as db:
        db.execute(
            """INSERT INTO discovery_cache(query,payload,fetched_at) VALUES(?,?,?)
            ON CONFLICT(query) DO UPDATE SET
            payload=excluded.payload, fetched_at=excluded.fetched_at""",
            (query, json.dumps(results), now),
        )
    return results


@app.get("/api/topics")
def get_topics():
    with connect() as db:
        return [row["topic"] for row in db.execute("SELECT topic FROM topics ORDER BY topic")]


@app.post("/api/topics", status_code=201)
def add_topic(item: NewTopic):
    topic = item.topic.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 +#._-]{0,29}", topic):
        raise HTTPException(400, "Use letters, numbers, spaces, +, #, . or -")
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        exists = db.execute("SELECT 1 FROM topics WHERE topic=?", (topic,)).fetchone()
        if count >= 12 and not exists:
            raise HTTPException(400, "Maximum of 12 interests")
        db.execute("INSERT OR IGNORE INTO topics(topic) VALUES(?)", (topic,))
    return {"topic": topic}


@app.delete("/api/topics/{topic}", status_code=204)
def remove_topic(topic: str):
    with connect() as db:
        if not db.execute("DELETE FROM topics WHERE topic=?", (topic,)).rowcount:
            raise HTTPException(404, "Topic not found")


@app.get("/api/discover")
def discover(
    q: str = Query(min_length=2, max_length=80),
    ranker: str = Query(default="content", pattern="^(content|baseline|semantic|watch|ml)$"),
):
    """Explicit search, with optional LOCAL context from the query's own topic."""
    term = " ".join(q.split())
    found = _search_cached(term)  # Exactly the user-requested search.
    library = videos()
    dismissed = {v["id"] for v in library if v["rating"] == -1}
    results = [v for v in found if v.get("id") not in dismissed]
    if ranker not in ("watch", "ml") or len(results) < 2:
        return [{**v, "tags": [term[:30].lower()], "reason": "Search result"}
                for v in results]
    sessions = _watch_sessions()
    examples = session_examples(library, sessions)
    try:
        context = contextual_scores(
            [(term, results)], examples, connect, model=get_model())
    except ModelUnavailable as exc:
        raise HTTPException(409, str(exc) + " Select another ranking mode.") from exc
    except Exception as exc:
        raise HTTPException(503, "Local search ranking failed; try v0.8 Content.") from exc
    if not context:
        # Insufficient local knowledge of this particular search: do not
        # impose unrelated past interests on something new.
        return [{**v, "tags": [term[:30].lower()], "reason": "Search result"}
                for v in results]
    focused_library = [
        v for v in watch_profile_library(library, sessions)
        if term.casefold() in {str(t).casefold() for t in v.get("tags", [])}
    ]
    profile = build_profile([term], focused_library)
    return rank_content(
        [(term, results)], profile, focused_library, dismissed,
        limit=len(results), context_scores=context,
    )


def _profile():
    """Build an explainable profile from on-device user preferences and clicks."""
    with connect() as db:
        selected = [row["topic"] for row in db.execute("SELECT topic FROM topics")]
    return build_profile(selected, videos())


@app.get("/api/profile")
def profile():
    """Local transparency endpoint: what topics currently influence rankings."""
    interests = _profile()
    top = sorted(interests["tags"].items(), key=lambda item: (-item[1], item[0]))
    return {
        "selected": sorted(interests["chosen"]),
        "interests": [{"topic": tag, "weight": round(weight, 2)}
                      for tag, weight in top if weight > 0][:12],
        "note": "Play clicks, approximate watch time, likes and completions influence ranking.",
    }


@app.get("/api/semantic/status")
def get_semantic_status():
    """Cheap optional-model status check; never triggers a download."""
    return semantic_status()


@app.get("/api/feed")
def feed(refresh: bool = False,
         refine: bool = True,
         balance: int = Query(default=65, ge=0, le=100),
         ranker: str = Query(default="content", pattern="^(content|baseline|semantic|watch|ml)$")):
    """Generate better candidates, keep baseline and new ranker comparable."""
    # Explicit opt-in: the default content/baseline paths never load fastembed.
    model = None
    if ranker in ("semantic", "watch", "ml"):
        try:
            model = get_model()
        except ModelUnavailable as exc:
            raise HTTPException(
                409, str(exc) + " Choose v0.8 Content until setup is complete."
            ) from exc
    library = videos()
    sessions = _watch_sessions() if ranker in ("watch", "ml") else []
    ranking_library = (watch_profile_library(library, sessions)
                       if ranker in ("watch", "ml") else library)
    if ranker in ("watch", "ml"):
        with connect() as db:
            chosen_topics = [row["topic"] for row in db.execute("SELECT topic FROM topics")]
        interests = build_profile(chosen_topics, ranking_library)
    else:
        interests = _profile()
    topics = topics_to_search(interests, limit=3)
    subscribed = []
    if ranker == "ml":
        with connect() as db:
            subscribed = subs.listing(db)
    if not topics and not subscribed:
        return {"topics": [], "videos": []}
    if not topics and subscribed:
        topics = ["subscribed creators"]

    with connect() as db:
        row = db.execute(
            "SELECT refresh_cursor,last_refresh_at FROM feed_settings WHERE id=1"
        ).fetchone()
        cursor = row["refresh_cursor"]
        now = int(time.time())
        if refresh and now - row["last_refresh_at"] >= 30:
            cursor += 1
            db.execute(
                "UPDATE feed_settings SET refresh_cursor=?,last_refresh_at=? WHERE id=1",
                (cursor, now),
            )

    plan = build_queries(interests, library, cursor, include_refined=refine)
    batches = []
    for topic, query in plan:
        try:
            batches.append((topic, _search_cached(query)))
        except HTTPException:
            continue
    # v0.12: subscribed channel uploads are candidates in the opt-in ML
    # feed. Neither channel display names nor arbitrary topic strings are
    # used as identities. The subscription tab itself stays unranked.
    if ranker == "ml" and subscribed:
        for channel in subscribed[cursor % len(subscribed):] + subscribed[:cursor % len(subscribed)]:
            if len([topic for topic, _ in batches
                    if topic == "subscribed creators"]) >= 6:
                break
            try:
                uploads = _channel_uploads_cached(channel["channel_id"], limit=6)
            except Exception:
                continue
            if uploads:
                batches.append(("subscribed creators", [
                    {**video, "channel_id": channel["channel_id"],
                     "channel": channel["name"]}
                    for video in uploads
                ]))
    if not batches:
        raise HTTPException(
            502, "YouTube discovery is unavailable right now. Your saved library still works."
        )

    extra_query = None
    learned_examples = 0
    semantic_scores = None
    watch_summary = None
    context_scores = None
    if ranker in ("semantic", "watch", "ml"):
        try:
            learned_examples = len(examples_from(library))
            # Optional expansion only when the user allows refined YouTube
            # searches. A public result's concise title keywords seed one extra
            # query, guided by semantic similarity to actual watched examples.
            if refine and learned_examples:
                suggestion = suggest_expansion(
                    batches, library, {query.casefold() for _, query in plan},
                    connect, model=model,
                )
                if suggestion is not None:
                    topic, query = suggestion
                    try:
                        found = _search_cached(query)
                        batches.append((topic, found))
                        extra_query = query
                    except HTTPException:
                        pass
            if ranker in ("watch", "ml"):
                semantic_scores, watch_summary = watch_features(
                    batches, library, sessions, connect, model=model)
                context_scores = contextual_scores(
                    batches, session_examples(library, sessions),
                    connect, model=model)
                learned_examples = watch_summary["examples"]
            else:
                semantic_scores, learned_examples = semantic_features(
                    batches, library, connect, model=model,
                )
        except Exception as exc:
            # Never silently switch rankers when the local model fails.
            raise HTTPException(
                503, "Local semantic ranking failed; try v0.8 Content. "
                     "Check the FastEmbed installation and local model cache."
            ) from exc

    seen = {video["id"] for video in library}
    if ranker == "baseline":
        ranked = rank_feed(batches, interests, seen, limit=24)
    else:
        ranked = rank_content(
            batches, interests, ranking_library, seen, limit=24, balance=balance,
            semantic_scores=semantic_scores,
            context_scores=context_scores,
            subscribed_channel_ids={s["channel_id"] for s in subscribed}
            if ranker == "ml" else None,
        )
    learning_stats = None
    if ranker == "ml":
        with connect() as db:
            ranked = ml.predict(db, ranked)
            ranked = ml.register(db, ranked)
            learning_stats = ml.status(db)
    return {
        "topics": topics,
        "videos": ranked,
        "cursor": cursor,
        "ranker": ranker,
        "balance": balance if ranker != "baseline" else None,
        "semantic_examples": learned_examples if ranker in ("semantic", "watch", "ml") else None,
        "watch_learning": watch_summary,
        "ml_learning": learning_stats,
        "semantic_expanded": extra_query is not None,
        "queries_used": len(batches),
        "candidates_considered": len({
            video["id"] for _, found in batches for video in found
            if isinstance(video.get("id"), str) and video["id"] not in seen
        }),
    }
