# v0.10 Research — Watch-Driven Learning

**Experimental branch only:** research/v0.10-evaluation. Not merged into
main. The regular v0.9 Semantic option and its scoring formula remain
available and unchanged.

## Why

LocalTube could already remember aggregate watch seconds and likes, but could
not distinguish an intentional *return visit* from one long viewing session.
And a play click is not a reliable endorsement. The prior offline evaluation
asked a human to rate candidate titles, which is unsuitable for regular use.

This experiment takes a different approach: **ordinary playback automatically
supplies preference evidence**. It never asks for a rating or a labeled JSON
file. It makes no additional model downloads or external analytics requests.

The existing locally downloaded BGE-small English encoder remains frozen.
We *learn* by changing the positive example weights and their multiple semantic
interest centroids; **we do NOT train neural-network weights or a two-tower
network**. The research model is an adaptive personal representation.

## Try it on Fedora

    git fetch origin
    git switch research/v0.10-evaluation
    git pull --ff-only
    source .venv/bin/activate
    python -m pytest -q
    python -m uvicorn app:app --host 127.0.0.1 --port 8000

If you don't have BGE set up, you can still use the original content/baseline
feed. For the optional BGE model, follow docs/SEMANTIC_ENGINE.md. The model
must be explicitly downloaded once, with the existing v0.9 command.

Open Home, choose **v0.10 Watch Learning (experimental)** in the Ranking
dropdown, and watch videos normally. This mode uses the same local model
files as v0.9, and the usual *refined discovery* switch still determines
whether certain title/creator searches go to YouTube.

**You do not need to rate any videos manually.** If you explicitly like or
dislike something, that remains valuable direct evidence, but it is optional.

Use **Manage Interests -> Watch Learning** to see locally stored session
counts and how many positive examples are currently available.

## What changed in the player

File: static/app.js and static/player.js.

- Each Play action creates one random-looking **session_id**; it is a dedup
  identifier, NOT an authentication token.
- The normal IFrame Player API sends bounded watched-time deltas every
  approximately five seconds, including the session ID and increasing
  sequence number.
- The backend checks the session belongs to the video and rejects
  unknown/mismatched sessions. Replayed or out-of-order sequence numbers are
  **idempotent**, so a duplicated progress request cannot double-count.
- As before, the browser ignores seek jumps, pauses, buffering and long
  inactive gaps when measuring playing seconds.
- A completion event contributes to the *new* session record only when
  the session also accumulated meaningful watch time; seeking straight to
  the end does not earn a completion in new sessions.
- Browser/YouTube API failures may prevent tracking. Playback via fallback
  iframe still works without it.

Existing /api/videos/{video_id}/play and /progress requests from older clients
without a session ID remain supported, but do not create session history.

## Database

A non-destructive SQLite startup migration adds:

    watch_sessions(
      session_id TEXT PRIMARY KEY,
      video_id TEXT,
      started_at TEXT,
      updated_at TEXT,
      watched_seconds REAL,
      duration_seconds REAL,
      completed INTEGER,
      last_sequence INTEGER
    )

All rows stay in the existing ignored local database, never in browser
localStorage or a remote service. At most 3,000 recent sessions are retained
to limit growth. A video deletion deletes its associated session rows.

The previous aggregate watch history in the videos table is preserved.
Older video watches and likes continue to contribute when no new session
records exist. Your old history is **not** converted into fabricated sessions.

## Signals and thresholds

File: watch_learning.py.

A video can be an implicit positive example when a single session has:

    meaningful_seconds = min(180, max(25, duration_seconds * 0.10))

Examples:
- A 100-second video requires 25 seconds of real watching.
- A 5-minute video requires 30 seconds.
- A 2-hour video requires 3 minutes; watching 10 minutes is positive even
  though the completion fraction is small.

Those thresholds are **experimental design choices** to validate over time.
Unknown duration uses 25 seconds. Short exits and clicks alone are neutral,
NOT automatic dislikes. An explicit dislike is the only negative signal.

Per meaningful session, confidence is computed as

    strength = 0.45 + 0.28 * min(1, seconds/300)
                     + 0.25 * min(1, seconds/duration)
                     + (0.12 if completed else 0)

A second/third meaningful visit adds 0.12/0.24, capped in total.
An explicit like contributes at least 1.05 confidence.
Older pre-session watch behavior uses the existing v0.9 preference formula
as a fallback where the video has no session history.

The learned representation includes up to 32 positive and 12 explicit
negative examples, ordered deterministically. Positives have gentle
timestamp-based recency weighting from the v0.10 prototype:

    freshness(age_days) = 0.65 + 0.35 * 2 ** (-age_days / 45)

Thus long-term interests never vanish solely due to age.

## Semantic interest groups

Use the already cached 384-dimensional BGE title embeddings locally:

1. Sort positive examples by watch-driven strength and freshness.
2. Greedily assign each to the nearest weighted centroid when cosine
   similarity is at least **0.78**; otherwise form a separate group, up to
   eight groups.
3. A group gets an L2-normalized, watch-strength-weighted centroid.
4. Each candidate gets its strongest positive group similarity (including a
   small support bonus for groups with multiple matching examples).
5. Explicit disliked examples retain v0.9's separate negative similarity.
6. Feed Balance still reranks on topic/creator diversity.

No hardcoded interest categories, custom model downloads, fine-tuning, remote
inference, or secret personalization API.

The threshold 0.78 has **not been calibrated for real BGE videos**; it is
a research starting point, not a claim of optimal performance.

The existing v0.9 baseline scores are unchanged, and v0.10 Watch Learning
is a separate ranking mode. See docs/V010_RESEARCH.md for the offline
frozen-pool comparison tools, which are still optional.

## Current passive diagnostics and limitations

The research branch exposes a local summary:

    curl http://127.0.0.1:8000/api/watch-learning/stats

It returns how many playback sessions are stored, how many were meaningful,
and how many current positive/negative examples the model could use. These
are **activity diagnostics, NOT evidence of superior recommendations**.
We have not yet added impression-based unbiased online A/B measurement or
a learned ranking-weight model.

Session quality has limitations: playback may not be tracked if YouTube's
Player API is blocked, sessions can be interrupted, and watch time is not the
same as satisfaction. A video clicked but never watched is not a negative.

For future experiments we can add an explicit local-only exposure log and
temporal evaluation, then train tiny ranking weights if enough trustworthy
interactions accumulate. Those features are NOT part of this release.

### Clearing only the new research session records

    curl -X DELETE http://127.0.0.1:8000/api/watch-learning/sessions

This deletes session records but NOT the original saved videos, their
aggregate watched_seconds, explicit likes/dislikes or their older semantic
examples. We avoid destructive operations by default. The file
data/localtube.db is your actual local viewing history; back it up before
any manual database modification.

## Security and privacy

There is **no authentication**, and a session ID is not an access control.
Only run on localhost or temporarily on a **trusted** home network.
Never publicly expose an unauthenticated LocalTube server.

Semantic inference and session tracking do not contact Google. Normal
searches, optional thumbnails, and YouTube iframe playback still do.
The optional refined search feature sends short public-title/creator queries
to YouTube when enabled.

## Tests and files

- tests/test_watch_learning.py: short exits, meaningful sessions, very long
  videos, duplicate sequence safety, session collision, explicit dislikes,
  separate semantic groups, legacy playback compatibility and local-only feed.
- static/player.js: per-session numbered playback heartbeat.
- static/app.js: session start, new Ranking choice and local diagnostics.
- app.py: non-destructive migration, session updates, local reset/stat API.
- watch_learning.py: all pure interest signal and prototype math.
- docs/V010_RESEARCH.md: older manual-label offline evaluation (OPTIONAL).
