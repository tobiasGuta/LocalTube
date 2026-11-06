"""Local-only YouTube channel subscriptions. No OAuth or Google account.

A subscription is keyed ONLY by stable UC channel ID, never display names.
Subscription list and video cache share LocalTube's existing SQLite database.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
MAX_SUBSCRIPTIONS = 24
UPLOAD_TTL_SECONDS = 15 * 60


def initialize(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
        channel_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        subscribed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    )""")


def listing(db) -> list[dict]:
    return [dict(row) for row in db.execute(
        """SELECT channel_id,name,subscribed_at FROM subscriptions
           ORDER BY name COLLATE NOCASE, channel_id"""
    )]


def add(db, channel_id: str, name: str) -> dict:
    if not CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("A stable YouTube channel ID is required")
    name = name.strip()[:100] or "YouTube channel"
    existing = db.execute(
        "SELECT channel_id FROM subscriptions WHERE channel_id=?", (channel_id,)
    ).fetchone()
    if existing is None:
        count = db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        if count >= MAX_SUBSCRIPTIONS:
            raise ValueError("Maximum local subscriptions reached (24)")
    db.execute("""INSERT INTO subscriptions(channel_id,name) VALUES(?,?)
        ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name""",
        (channel_id, name))
    return dict(db.execute(
        "SELECT channel_id,name,subscribed_at FROM subscriptions WHERE channel_id=?",
        (channel_id,)
    ).fetchone())


def remove(db, channel_id: str) -> bool:
    if not CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("Invalid channel ID")
    return bool(db.execute(
        "DELETE FROM subscriptions WHERE channel_id=?", (channel_id,)
    ).rowcount)


def cached_uploads(
    connect_db: Callable, channel_id: str, fetcher: Callable,
    *,
    force: bool = False, limit: int = 6,
) -> list[dict]:
    """Read or refresh public channel uploads; stale cache on network failure.

    Cache is keyed by validated channel ID, not by a mutable channel name.
    A failed fetch does not get cached forever.
    """
    if not CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("Invalid channel ID")
    key = "channel-uploads:" + channel_id
    now = int(time.time())
    with connect_db() as db:
        row = db.execute(
            "SELECT payload,fetched_at FROM discovery_cache WHERE query=?",
            (key,)
        ).fetchone()
    old = None
    if row:
        try:
            old = json.loads(row["payload"])
            if not isinstance(old, list):
                old = None
        except (TypeError, json.JSONDecodeError):
            old = None
    if old is not None and not force and now - row["fetched_at"] < UPLOAD_TTL_SECONDS:
        return old[:limit]
    try:
        discovered = fetcher(channel_id, limit=limit)
        videos = []
        seen = set()
        for item in discovered:
            if not isinstance(item, dict):
                continue
            vid = item.get("id")
            if not isinstance(vid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            if vid in seen:
                continue
            seen.add(vid)
            videos.append({**item, "channel_id": channel_id})
        with connect_db() as db:
            db.execute("""INSERT INTO discovery_cache(query,payload,fetched_at)
                VALUES(?,?,?) ON CONFLICT(query) DO UPDATE SET
                payload=excluded.payload, fetched_at=excluded.fetched_at""",
                (key, json.dumps(videos), now))
        return videos[:limit]
    except Exception:
        if old is not None:
            return old[:limit]
        raise
