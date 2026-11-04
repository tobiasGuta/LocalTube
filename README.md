# LocalTube

**Your videos, your algorithm.** A local-first, account-free YouTube discovery and recommendation app for Fedora.

## Run on Fedora

Python 3.10+ required. New installation:

```bash
git clone https://github.com/tobiasGuta/LocalTube.git
cd LocalTube
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Stop with Ctrl+C.

Already installed? Update using:

```bash
cd LocalTube
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
# Stop the old server with Ctrl+C, then restart:
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## What works in v0.2

- Discover unseen YouTube videos based on chosen interests and your saved video tags, likes, and play clicks.
- Search YouTube without logging into Google. The Fedora backend uses yt-dlp to retrieve public metadata.
- Play discovered videos (automatically saving them), save without playing, or mark not interested.
- Browse the original saved library and its local, explainable recommendations.
- Cache search results in SQLite for one hour; on upstream failure, older cached results can be shown.
- Optional video thumbnails are off by default to avoid automatic requests to Google's image servers.

## Privacy and limitations

**No Google account required.** LocalTube stores saved videos, selected topics, metadata caches, play clicks and ratings in data/localtube.db on your Fedora computer. No LocalTube analytics.

However, this is **not Google-free video hosting**. yt-dlp search requests go directly to YouTube from Fedora's IP. Playback loads YouTube/Google in your browser. Enabling thumbnails sends thumbnail requests to Google's image servers. Google may still observe the IP, timing, and other network signals.

Recommendations are simple topic-based ranking, not YouTube's proprietary algorithm. Playback tracks button clicks, not completed watches. yt-dlp relies on YouTube's changing public interface; searches can fail or be blocked. Try updating it with: python -m pip install -U yt-dlp. The library works even when discovery is unavailable.

**Security warning:** This version has no authentication. Keep it bound to 127.0.0.1; do not expose it to other devices, Tailscale, the LAN, or the internet until authentication, origin protections, and secure deployment are added.

## Tests

```bash
python -m pip install pytest httpx
python -m pytest -q
```

Tests mock YouTube discovery, so they require no external services.

## Roadmap

1. Local library and recommendation MVP — done.
2. Video discovery, custom topics, search and cached results — v0.2.
3. Better related videos, subscription import, and relevance ranking.
4. Authentication and secure cross-device access, then Tailscale.
5. Home server migration and backup/export.

MIT licensed.
