"""LocalTube: local-first video library and topic recommender."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
        "created_at": row["created_at"], "plays": row["plays"], "rating": row["rating"]
    }


class NewVideo(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=160)
    tags: str = Field(default="", max_length=300)


class Feedback(BaseModel):
    rating: int = Field(ge=-1, le=1)


@asynccontextmanager
async def lifespan(_):
    init_db()
    yield


app = FastAPI(title="LocalTube", version="0.1.0", lifespan=lifespan)
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
            db.execute("INSERT INTO videos(id,title,tags,created_at) VALUES(?,?,?,?)", (
                video_id, title, json.dumps(tags),
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            ))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "This video is already saved") from exc
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.post("/api/videos/{video_id}/play")
def play(video_id: str):
    with connect() as db:
        changed = db.execute("UPDATE videos SET plays=plays+1 WHERE id=?", (video_id,))
        if not changed.rowcount:
            raise HTTPException(404, "Video not found")
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.put("/api/videos/{video_id}/feedback")
def feedback(video_id: str, value: Feedback):
    with connect() as db:
        changed = db.execute("UPDATE videos SET rating=? WHERE id=?", (value.rating, video_id))
        if not changed.rowcount:
            raise HTTPException(404, "Video not found")
        return as_video(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


@app.delete("/api/videos/{video_id}", status_code=204)
def delete(video_id: str):
    with connect() as db:
        if not db.execute("DELETE FROM videos WHERE id=?", (video_id,)).rowcount:
            raise HTTPException(404, "Video not found")


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
