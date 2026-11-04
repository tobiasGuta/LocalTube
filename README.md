# LocalTube

**Your videos, your algorithm.** A privacy-minded, local-first YouTube viewer and video discovery tool that runs on Fedora.

## New in v0.6 — watch progress and a fresher feed

- **Real playback progress:** Official YouTube IFrame API loads only when you click Play. While it reports PLAYING, LocalTube samples every five seconds, ignoring jumps from seeking and buffering. Watch seconds remain approximate.
- **Resume videos:** Last position is saved in SQLite, with progress bars on saved thumbnails.
- **Watch history:** A new sidebar view shows videos with playback progress and repeat opens.
- **Completions:** The official player ENDED event increments completion counts; seeking to the end is not the same as finishing.
- **Local viewing stats:** Manage Interests shows your top watched topics, creators, and aggregate watch time. Previously saved manual links may not include channel metadata.
- **Richer discovery:** Up to 18 videos per topic, across three topics. Clicking Refresh rotates search queries (at most every 30 seconds). Each query is cached locally for one hour.
- **Better ranking:** Approximate playing time, repeat opens, completions, and creator affinity join likes and manually chosen topics.
- **Privacy:** Video playback loads a YouTube embed and the remote YouTube IFrame API after you click Play. Position and preferences stay on Fedora. No remote scripts load on the homepage.
- **Fallback:** If the official player API cannot load, embedded playback is available but tracking is disabled for that session.
- **SQLite migration:** Your existing database and saved videos are preserved. Still back up data/localtube.db before major updates.

**Limitations:** Playback tracking is approximate. It can miss seconds if the browser closes abruptly, and it cannot tell whether you're actively paying attention to a video playing in the background. Livestream durations may differ from recorded videos.

## Previous releases
## What's new in v0.5: one-click likes

- Tap the **thumbs-up icon** beside any video card's three-dot menu to like it instantly.
- The video is **saved to your local library automatically** as part of liking it — no separate Save step needed.
- **Like / Liked** is also available **inside the video player**, without interrupting playback.
- The icon reflects your saved local like state. Tap again to unlike. Like/unlike works from search results, Home, Explore, and your Library.
- Multiple rapid clicks on the same video are ignored until its previous update finishes, to prevent duplicate saves and inconsistent feedback.
- The video stays visible while you like it. Click **Refresh** to get a newly ranked feed; the discovery feed still omits videos already saved, and likes remain available in **Liked videos**.
- Likes are **LocalTube-only**, not public YouTube likes or actions performed on your Google account.

## What's new in v0.4: smarter, explainable recommendations

- **Likes count more than play clicks**, with a capped signal from repeated clicks. A click is not a confirmed watch.
- **Recent activity matters more**, gradually fading older engagement rather than permanently locking your feed to an old interest.
- **Title similarity** boosts videos sharing meaningful words with videos you liked or clicked; common words like "video" or "youtube" are ignored.
- **Topic and channel variety:** a local greedy reranker reduces repetition while still prioritizing your stronger interests.
- **Three topic searches instead of two** can help broaden the recommendation pool. Search metadata is still cached for one hour.
- **Transparent interests:** Manage interests → "What LocalTube has learned" displays your strongest topic signals from a local API.
- **Preserves existing data.** A non-destructive SQLite migration adds an optional last-activity timestamp; saved videos and feedback stay intact.
- **No remote AI service, embeddings API, telemetry, or extra Python packages.** This is a lightweight heuristics-based improvement, not an AI model or YouTube's algorithm.

## Previous release: v0.3

- A YouTube-inspired (but LocalTube-branded) layout: fixed top search, collapsible sidebar, and a responsive grid of widescreen video cards.
- Home recommendations, Explore, Your library, and Liked videos views.
- Video playback by clicking a thumbnail or title, and a three-dot menu for Save, Like, Not interested, and Remove.
- Topic filter chips, an Add video dialog, and Manage interests dialog.
- Optional real thumbnails, **off by default**. The checkbox is in the top bar; enabling it contacts Google's thumbnail servers and saves only that browser preference in localStorage.
- Existing SQLite library, tags, play clicks, favorites, and interests continue to work without any migration.

## Run on Fedora

Python 3.10+ required. For a new install:

```bash
git clone https://github.com/tobiasGuta/LocalTube.git
cd LocalTube
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000**.

To update an existing installation (stop the running server with Ctrl+C first):

```bash
cd LocalTube
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Refresh the browser with Ctrl+Shift+R after updating to clear old JS and CSS.

## Use

1. Click **Manage interests** to set topics, or use the top search bar for account-free YouTube search.
2. Click a video thumbnail or title to play it in an embedded YouTube player.
3. Use the **⋮** menu on a card to save, like, dismiss, or remove a video.
4. Visit **Your library** to find your saved videos; your likes are under **Liked videos**.
5. Turn on **Thumbnails** in the header for a photo-based YouTube-style grid. This makes image requests to Google.

The local library remains available even when YouTube discovery is temporarily blocked or offline.

## Privacy, security, and trade-offs

- No Google account needed. Your library, topic tags, likes, play clicks, and metadata cache are stored locally in `data/localtube.db` (ignored by Git).
- Fedora uses yt-dlp for public YouTube search metadata. **Those requests still reach YouTube from Fedora's IP address**; no login does not mean no Google contact.
- Embedded playback contacts Google directly from your browser. Optional thumbnails contact `i.ytimg.com` from your browser when enabled.
- Thumbnail preference uses browser localStorage. LocalTube itself has no analytics.
- The recommender uses local heuristics, **not** YouTube's proprietary model or actual watch-duration tracking.
- `yt-dlp` may stop working when YouTube changes its interface. If search breaks, update it with `python -m pip install -U yt-dlp`.
- **Keep the app bound to 127.0.0.1.** Authentication and deployment security are not yet implemented. Do not expose it via a LAN binding, port forward, or Tailscale until those protections are added.

## Tests

```bash
python -m pip install pytest httpx
python -m pytest -q
node --check static/app.js  # optional JavaScript syntax check, requires Node
```

Tests use mocked YouTube results and need no Google account or live YouTube access. The frontend has static contract tests; interactive UI should also be smoke-tested in Firefox/Safari.

## Roadmap

1. Local library and recommendations — v0.1.
2. Video discovery, custom interests, and search — v0.2.
3. Video-first responsive interface — **v0.3**.
4. Better recommendations, subscriptions, and optional related video discovery.
5. Authentication, protected cross-device access, then private Tailscale.
6. Dedicated server migration and backup/export.

MIT licensed.
