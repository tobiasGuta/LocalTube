# v0.12 Local Subscriptions + Learned Creator Affinity (research)

**Branch:** research/v0.12-local-subscriptions  
**Parent:** v0.11 research branch at commit edee449a55059d311e95c107d222c692a203372a  
**Status:** Experimental; neither v0.11 nor v0.10 nor main is modified.

This adds two independently useful features:

1. Subscribe or unsubscribe from public YouTube channels **inside
   LocalTube only**, without Google login, YouTube OAuth, an API key, or
   touching your YouTube account.
2. Feed **exact subscribed-creator identity** into the v0.11 trainable
   local recommendation model, without hardcoding any creator names.

## Try it on Fedora

Use the working Python 3.14 environment established for v0.11; the
experimental FastEmbed/ONNX semantic engine could not be installed under
your Python 3.15 virtual environment.

From the LocalTube repository:

    git fetch origin
    git switch --track origin/research/v0.12-local-subscriptions
    source "$HOME/.virtualenvs/localtube-314/bin/activate"
    python --version
    python -m pytest -q
    python -m semantic_engine status
    python -m uvicorn app:app --host 127.0.0.1 --port 8000

If your Python 3.14 environment has missing packages:

    python -m pip install -r requirements.txt -r requirements-semantic.txt pytest

Open http://127.0.0.1:8000, then hard-refresh your browser (Ctrl+Shift+R).

On any video card, select **Subscribe** or use the "Subscribe to creator"
menu action. The player also has its own Subscribe button. You'll now
find **Subscriptions** in the sidebar, showing your followed channels
and the public videos retrieved from their YouTube *Videos* tabs.
The list has an **Unsubscribe** action for each channel.

## Exact channel identity matters

YouTube display names and handles are mutable and may collide. LocalTube
stores only canonical YouTube channel IDs in the form:

    UC + 22 YouTube identifier characters

When yt-dlp public video-search metadata includes a channel ID or
canonical channel URL, LocalTube carries that ID through the frontend.
A Subscribe click uses it directly.

For old locally saved videos or search entries with no stable channel
ID, **only on your explicit Subscribe click** LocalTube requests public
video metadata from YouTube and resolves the video's real channel ID.
If a stable ID cannot be verified, the subscription is NOT stored;
the UI shows a message. We do not guess based on the channel name.

Channel names are just display labels; two channels with the same
name remain separate subscriptions. No manually curated names,
category rules or channel hardcoding appear in source.

## Local API

    GET    /api/subscriptions
    POST   /api/subscriptions
    DELETE /api/subscriptions/{UC_channel_ID}
    GET    /api/subscriptions/feed
    GET    /api/subscriptions/feed?refresh=true

POST request:

    {
      "video_id": "11-character-id",
      "channel_id": "UC...24-characters...",   // optional if unavailable
      "channel_name": "Public display label"
    }

The server validates the exact channel-ID format. Missing IDs trigger
on-demand verification; no login or cookie import.

SQLite migration creates:

    subscriptions(
      channel_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      subscribed_at TEXT NOT NULL
    )

The existing saved-videos table also receives a nullable-in-practice
empty-default **channel_id** column. Existing history, likes, negative
feedback, watch sessions, semantic vectors, ML training examples and
model weights are preserved.

**Limits:** Up to 24 local subscriptions; up to 6 public recent video
entries retrieved per channel; a 15-minute cache per channel in the
existing local discovery_cache SQLite table.

## Dedicated Subscriptions vs personalized Home

**Subscriptions page:**
- Shows uploads from subscribed channels, even if the ML algorithm
  predicts low engagement. Your subscription explicitly controls this.
- Retrieves public metadata from the exact channel's YouTube Videos tab.
  It does NOT text-search using the channel name (which could mix
  unrelated creators).
- Orders by public upload timestamp/date when available; when flat
  metadata lacks dates, order is approximate and retains original
  per-channel retrieval positions. This is not a guaranteed
  chronological YouTube API subscription feed.
- Omits videos explicitly marked "Not interested" by exact ID. Already
  watched videos stay available for replay.
- If one channel fails due to YouTube restrictions, other channels
  remain visible. Cached uploads are used when a recent request fails.
- A manual Reload bypasses the 15-minute cache; repeated normal visits
  reuse cached results.

**Home / Explore with v0.11 Local ML selected:**
- Candidate retrieval expands the normal discovery batches with
  subscribed creator uploads. Up to 6 subscribed channels participate
  per request; the selection rotates when you refresh the feed.
- A matching candidate receives an explicit creator-affinity feature
  named **subscription_prior**, with value 1.6, and other candidates
  get 0. The match is strictly channel_id membership, NOT title words,
  semantic similarity, channel name equality or hand-coded interests.
- This feature becomes a normalized ML training input
  (subscription_prior / 1.6, clipped to [-1,1]). As you meaningfully
  watch or explicitly dismiss displayed v0.11 recommendations, the
  local logistic ranker learns whether subscription membership is
  predictive of your engagement.
- The fixed 1.6 prior means subscriptions can improve relevance even
  with very little labeled data, but **not guarantee top placement**.
  A disliked video still gets a negative penalty; the model can learn
  to de-emphasize subscription affinity as real outcomes accumulate.
- The Home feed continues hiding exact saved recommendations to avoid
  repeatedly suggesting watched content. Subscriptions show those
  videos because that is an explicit, replayable channel collection.
- Direct searches and older v0.10/v0.9/v0.8/v0.6 ranking modes do not
  suddenly gain an ML subscription boost. Their previous behavior
  remains a comparison baseline.

A subscription **is not itself a positive label** in the ML training
dataset; the model still trains only from **meaningful watches, Likes
and Not interested** on previously visible v0.11 candidate cards.
Ignoring or merely displaying an upload remains neutral. Watching in
the Subscriptions section still records normal local watch history,
but does not fabricate a new v0.11 training exposure.

## Backward-compatible learned weights

The v0.12 model has one additional numeric feature compared with v0.11.
On startup, if existing trained v0.11 weights have the older length,
LocalTube automatically refits the same local logistic model from your
preserved, original labeled v0.11 impression snapshots. The new
subscription feature is zero for old snapshots where it was absent.
This avoids silently discarding learned preferences or manufacturing
past subscription signals. No manual re-rating is required.

Check normal model progress with:

    curl http://127.0.0.1:8000/api/learning/status

You can still reset only v0.11+v0.12 training with:

    curl -X DELETE http://127.0.0.1:8000/api/learning/data

The reset does NOT delete subscriptions, saved videos or historical
watch sessions. To remove a subscription, use the Unsubscribe action.

## Privacy and limitations

- No Google login, OAuth, YouTube account changes, imported cookies,
  cloud model training or subscription-sync endpoint.
- YouTube sees ordinary public discovery, public channel metadata
  lookups, thumbs/iframe playback (when enabled), and channel upload
  checks. LocalTube is **not offline YouTube**.
- This is a research UI and recommender, not a full official YouTube
  subscriptions integration. Certain channels, live streams, Shorts
  or region-restricted uploads may be missing or block yt-dlp.
- The new trained feature is *hypothesized* to improve recommendations;
  v0.12's tests verify behavior but do not prove better real-life
  engagement. Compare normal future usage to v0.11 before merging.
- The server is unauthenticated: keep it on localhost or a trusted
  private network, never expose it directly to the public internet.

## Test coverage

    python -m pytest -q

Tests cover canonical channel-ID validation and collision avoidance,
idempotent subscriptions, legacy-video identity resolution, failure
handling without silently guessing a creator, subscription feed cache
and refresh, partial YouTube outage, exact disliked-video filtering,
subscribed-only ML candidate discovery, positive prior without
fabricated labels, existing-weights migration, bounded subscription
count, frontend wiring and previous v0.11 behavior.
