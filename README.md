# LocalTube

**Your videos, your algorithm.** A privacy-minded, local-first YouTube viewer and video discovery tool that runs on Fedora.

> **Research branch notice:** The new **v0.12 Local Subscriptions + ML Creator Affinity**
> lives on `research/v0.12-local-subscriptions` (not `main` or earlier
> experimental branches). Subscribe to channels locally by stable YouTube
> channel ID, view their public uploads in a dedicated Subscriptions feed,
> and let v0.11's trainable model use subscription membership as a ranking
> feature. No Google account, channel-name hardcoding or invented labels.
>
> **[v0.12 implementation and privacy guide](docs/V012_SUBSCRIPTIONS.md)**.
> The previous **v0.11 Local ML (experimental)**
> mode lives on research/v0.11-online-ranker, not main or the frozen
> research/v0.10-evaluation branch. It trains a small local ranking model
> from genuine meaningful watch sessions and explicit Like/Not interested,
> after a recommendation is actually visible. Merely ignoring or not
> clicking a card never creates a negative label. No manually labeled
> JSON snapshots or hardcoded topic names are required.
>
> **[v0.11 local ML research guide](docs/V011_LOCAL_ML.md)** — setup, local
> training equations, recorded impressions, privacy, reset, regression
> tests and important limitations. Compatible Python 3.14 and an installed
> local BGE encoder are required for v0.11.
>
> Original v0.10 [Watch Learning](docs/WATCH_LEARNING.md), v0.10
> [contextual discovery](docs/CONTEXTUAL_DISCOVERY.md), the v0.9 semantic
> model, content ranking and baseline modes remain available.
>
> **Do not merge experimental recommendation changes into main until actual
> viewing data shows improvement.** v0.11 initially falls back to the
> identical v0.10 ordering until enough confirmed positive and negative
> examples exist. Trained weights are NOT validated recommendation quality.

## New in v0.9 — optional local Semantic Interest Engine

LocalTube now offers a **v0.9 Semantic (local)** ranking mode, alongside the unchanged **v0.8 Content** (default) and **v0.6 Baseline** algorithms. Semantic mode understands meaning beyond title keyword overlap. It uses a small open-source pretrained English embedding model through FastEmbed/ONNX **on your own computer** and builds an adaptive preference profile from videos you've actually liked, watched or disliked. It does **not** fine-tune the neural network or train ranking weights, and it won't infer interests accurately before meaningful examples exist.

**Installation is deliberately optional.** Normal LocalTube still needs only `requirements.txt`. To enable the local encoder, activate your virtual environment and run:

```bash
python -m pip install -r requirements-semantic.txt
python -m semantic_engine download
python -m semantic_engine status
```

The `download` command **contacts the model host**, and stores files under ignored `data/models/`. Restart LocalTube and choose **v0.9 Semantic (local)** in the Ranking dropdown. Model files are approximately 67 MB plus runtime dependencies. Local embedding inference and SQLite vector caching do **not** call a remote embeddings service. The normal v0.8 feed doesn't load the model.

Semantic mode can make **up to one additional, opt-in YouTube search** based on a related public candidate title discovered with local similarity. It does so only if the existing **Manage Interests → refined discovery** checkbox is enabled. Disabling that checkbox prevents the extra semantic query and all liked-title/creator queries, while preserving local semantic reranking. YouTube discovery, playback and thumbnails retain their earlier network privacy trade-offs.

**Learn how it works:** [docs/SEMANTIC_ENGINE.md](docs/SEMANTIC_ENGINE.md) explains the algorithm and equations, example weights, candidate discovery, API/SQLite schema, licensing, limitations, how to improve it, troubleshooting and private data management. It is written for developers and students reading the project.

**Why this video?** From the ⋮ menu of any v0.8/v0.9 recommended video, choose **Why this video?** to open a readable explanation dialog with the ranking score and individual topic, title, creator, semantic, dislike and diversity contributions. It works when you're scrolled down the feed, and doesn't contact YouTube or modify your preferences. A positive contribution increases the score; a negative one reduces it. Scores are heuristic, not probabilities.

**Developers:** Try `/api/feed?ranker=semantic&refine=false&balance=65`, compare `/api/feed?ranker=content` on the same candidates, and inspect per-video `score_breakdown`. Use `python -m semantic_engine clear-cache` to delete locally cached *vectors* without deleting your videos.

---
## New in v0.8 — Feed Balance

- **Focused ↔ Explore** slider beside the ranking selector, with a 0–100 range and 5-point increments. Default **Balanced · 65** preserves the v0.7 content ranker's existing behavior.
- Focused reduces topic/creator repeat penalties to favor videos most relevant to your strongest interests; Explore increases those penalties to include more distinct interests and creators among the top picks.
- The slider changes **only reranking** of the existing candidate pool. Adjusting it does not change the number or content of YouTube searches, does not cycle discovery searches, and does not require any ML training.
- Stored as browser-local preference `localtube-feed-balance`, with no database migration or third-party analytics.
- **v0.6 Baseline** remains selectable and ignores Feed Balance; the slider is hidden while using that ranker.
- The API accepts `/api/feed?ranker=content&balance=0` through `balance=100` (default 65). Invalid values are rejected. The feed response returns the effective balance setting; baseline returns `null`.
- The v0.7 content scoring breakdown remains available under **Why this video?**. The score's diversity-penalty component changes with the slider.

**Design note:** This is a reranker with adjustable diversity penalties, *not* a mathematically calibrated slider that guarantees a fixed percentage of gaming or knowledge videos. It cannot invent videos absent from the discovery pool.
## New in v0.7 — better candidates and transparent content ranking

- **More focused candidate discovery.** LocalTube searches up to three preferred topics, plus at most one short keyword query from a liked or meaningfully watched video's title, and at most one query using its creator name. **Maximum five queries per feed**; results are locally cached for an hour per query.
- **Privacy control:** Manage Interests includes a switch to disable extra liked-title and creator-based searches. The switch is on initially for v0.7 discovery and is stored in the browser only. When off, LocalTube searches general topics only. All YouTube searches still disclose the search terms and Fedora IP to Google; no local files or complete saved titles are uploaded.
- **Local text similarity.** A dependency-free TF-IDF vectorizer computes cosine similarity between candidate titles and liked/watched titles, with a penalty for similarity to disliked titles.
- **Interpretable features.** Ranking uses bounded topic, title, creator, discovery-position and multi-query agreement scores, followed by topic/creator repetition penalties. These coefficients are **manual heuristics**, not trained ML weights or calibrated probabilities.
- **A fair baseline comparison.** Choose **v0.7 Content** or **v0.6 Baseline** from the ranking selector. Both evaluate the *same cached candidates*; the former is the default. The three-dot menu on content-ranked videos has **Why this video?** to show score contributions.
- **Developer API:** `/api/feed?ranker=content` or `/api/feed?ranker=baseline` (append `&refine=false` for topic-only queries) returns ranked videos and candidate/query counts. The content ranker exposes a per-video score breakdown for inspection.
- **Nothing new to install:** the content ranker uses Python's standard library, and the existing SQLite watch history is preserved. No external AI API or automatic background jobs.

**Limitations:** More candidates and a mathematical score do not automatically guarantee better recommendations. We need feedback and offline/online evaluation before claiming an improvement. Our next phase should log impressions, track interaction outcomes, and measure ranking quality (without assuming unclicked videos are dislikes). YouTube search can still block automated discovery.
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
- The recommender uses local heuristics, **not** YouTube's proprietary model. LocalTube records approximate player activity, not perfect measures of attention.
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
2. Account-free discovery — v0.2.
3. Responsive video-first UI — v0.3.
4. Explainable heuristics and watch signals — v0.4–v0.6.
5. TF-IDF candidate expansion and comparable rankers — v0.7.
6. Adjustable feed relevance/diversity balance — v0.8.
7. Optional local semantic preference engine — **v0.9**.
8. Evaluate ranking with impression/feedback data, then consider learned weights.
9. Channel subscriptions and more diverse discovery.
10. Authentication and secure LAN/Tailscale access, then home server migration.

MIT licensed.
