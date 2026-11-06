"""Public YouTube metadata discovery without a Google account or video downloads."""
from __future__ import annotations
import re

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def verified_channel_id(entry: dict) -> str | None:
    """Only stable YouTube UC channel IDs, never ambiguous handles/names."""
    for key in ("channel_id", "uploader_id"):
        candidate = entry.get(key)
        if isinstance(candidate, str) and CHANNEL_ID.fullmatch(candidate):
            return candidate
    url = entry.get("channel_url") or entry.get("uploader_url")
    if isinstance(url, str):
        found = re.search(r"(?:^|/)channel/(UC[A-Za-z0-9_-]{22})(?:[/?#]|$)", url)
        if found:
            return found.group(1)
    return None


def public_video(entry: dict, *, channel_id: str | None = None,
                 channel_name: str | None = None) -> dict | None:
    """Normalize yt-dlp flat video metadata without downloading content."""
    vid, title = entry.get("id"), entry.get("title")
    if (not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid)
            or not isinstance(title, str) or not title.strip()):
        return None
    duration = entry.get("duration")
    date = entry.get("upload_date")
    stamp = entry.get("release_timestamp") or entry.get("timestamp")
    if not isinstance(stamp, (float, int)) or isinstance(stamp, bool):
        stamp = None
    return {
        "id": vid, "title": title.strip()[:160],
        "channel": str(channel_name or entry.get("channel")
                       or entry.get("uploader") or "Unknown creator")[:100],
        "channel_id": channel_id or verified_channel_id(entry) or "",
        "duration": duration if isinstance(duration, (float, int))
                    and not isinstance(duration, bool) else None,
        "upload_date": date if isinstance(date, str)
                       and re.fullmatch(r"\d{8}", date) else None,
        "upload_timestamp": int(stamp) if stamp is not None and stamp > 0 else None,
    }


def resolve_video_channel(video_id: str) -> dict:
    """Resolve exact creator identity only when the user presses Subscribe."""
    from yt_dlp import YoutubeDL

    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid video ID")
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "ignoreerrors": False, "socket_timeout": 8,
        "retries": 1, "extractor_retries": 1,
    }
    with YoutubeDL(options) as client:
        info = client.extract_info(
            "https://www.youtube.com/watch?v=" + video_id,
            download=False,
        )
    if not isinstance(info, dict):
        raise ValueError("Could not identify the video's creator")
    channel_id = verified_channel_id(info)
    if not channel_id:
        raise ValueError("This video does not expose a stable YouTube channel ID")
    return {
        "channel_id": channel_id,
        "name": str(info.get("channel") or info.get("uploader")
                    or "YouTube channel")[:100],
    }


def channel_uploads(channel_id: str, limit: int = 6) -> list[dict]:
    """Fetch a subscribed channel's public Videos tab, without sign-in.

    This uses the public channel page (not text search by channel name).
    YouTube may throttle, restrict, or change its extractor output.
    """
    from yt_dlp import YoutubeDL

    if not CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("Invalid channel ID")
    limit = max(1, min(int(limit), 12))
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "ignoreerrors": True, "socket_timeout": 8,
        "retries": 1, "extractor_retries": 1,
        "playlistend": limit, "lazy_playlist": False,
    }
    with YoutubeDL(options) as client:
        info = client.extract_info(
            "https://www.youtube.com/channel/" + channel_id + "/videos",
            download=False,
        )
    if not isinstance(info, dict):
        raise RuntimeError("Channel uploads are unavailable")
    results = []
    seen = set()
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video = public_video(entry, channel_id=channel_id)
        if video and video["id"] not in seen:
            seen.add(video["id"])
            results.append(video)
        if len(results) >= limit:
            break
    return results



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
        video = public_video(entry)
        if video is not None:
            results.append(video)
    return results
