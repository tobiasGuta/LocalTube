"""Public YouTube metadata discovery without a Google account or video downloads."""
from __future__ import annotations
import re

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def search_youtube(query: str, limit: int = 8) -> list[dict]:
    """Search with yt-dlp. YouTube sees requests from the server IP."""
    from yt_dlp import YoutubeDL

    limit = max(1, min(int(limit), 20))
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "noplaylist": True, "ignoreerrors": True,
        "socket_timeout": 10, "retries": 1, "extractor_retries": 1,
        "playlistend": limit,
    }
    with YoutubeDL(options) as client:
        info = client.extract_info(f"ytsearch{limit}:{query}", download=False)

    results = []
    seen = set()
    for entry in (info or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id")
        title = entry.get("title")
        if (not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid)
                or vid in seen or not isinstance(title, str) or not title.strip()):
            continue
        seen.add(vid)
        channel = entry.get("channel") or entry.get("uploader") or "Unknown creator"
        duration = entry.get("duration")
        results.append({
            "id": vid, "title": title.strip()[:160], "channel": str(channel)[:100],
            "duration": duration if isinstance(duration, (float, int)) else None,
        })
    return results
