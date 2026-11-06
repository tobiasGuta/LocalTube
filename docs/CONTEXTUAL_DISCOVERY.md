# v0.10 Research — Contextual Discovery and Reliable Feedback

**Experimental only:** branch research/v0.10-evaluation. The public main
branch and v0.9 Semantic ranking formula remain unchanged.

This patch addresses three user-observed problems without hardcoding any
topic, celebrity, movie, brand, toy, or creator name.

1. **Ambiguous searches.** Public YouTube text search can return unrelated
   meanings of the same word. The previous /api/discover endpoint simply
   returned YouTube's original results; no learned interests were considered.
2. **Previously clicked videos lingering in Home.** The backend already
   excluded saved videos on a new feed, but old browser cards could remain
   until the next refresh.
3. **Not interested must be remembered and influence similar items.** The
   video itself was saved as rating=-1, but the v0.10 experiment limited
   negative examples in video-ID order, potentially dropping newer
   dislikes when the history grew.

## What works now

On **v0.10 Watch Learning (experimental)**:

- LocalTube remembers your selected ranking mode across page reloads using
  browser localStorage; if the optional semantic model isn't available, it
  safely falls back to v0.8 Content.
- Home/Explore immediately remove cards once you have *played* the video,
  even if the existing grid is not refetched. A saved, unplayed suggestion is
  not automatically treated as watched.
- A fresh /api/feed still excludes all saved videos, preserving v0.9's
  existing recommendation deduplication behavior.
- Search sends the original, literal user query unchanged. When LocalTube
  has at least two meaningful or liked local videos tagged with the same
  search topic, the experimental search reranks YouTube's returned metadata
  using those examples.
- If LocalTube lacks sufficient context for this specific query, search
  results retain YouTube's original order. We do NOT overwrite exploration
  with unrelated historical interests.
- Exact videos marked **Not interested** are excluded from both personal
  feeds and subsequent direct searches. The new watch-learning scorer also
  compares similar candidates against the **latest** explicit dislikes.
- Search results remain replayable even if you've watched them before:
  typing a specific search is an intentional request, unlike Home discovery.

No manual labels, JSON ratings or settings changes are needed.

## Why this is NOT a hardcoded TED filter

There are no built-in synonyms or rules such as:

    if keyword == "X":
        ignore_video_containing("Y")

The function contextual_relevance.contextual_scores extracts example
videos from YOUR OWN locally saved tags and their watches/likes. Search
term and tag must match case-insensitively; a query with no known examples
does not guess an interpretation.

The model receives only public metadata already available to LocalTube:

    "Video title. Creator: Public channel name"

We reuse the optional local BGE model and its local SQLite vector cache.
Creating these descriptors does not trigger new internet requests.

A candidate's meaning is compared to:
- Positive videos associated with that same topic in your local history.
- Explicitly dismissed videos associated with that topic.
- Other candidate videos returned by that *same* YouTube search batch.

It is possible for the same text query to represent several different
interests. Our algorithm is empirical, not a universal entity resolver; it
could still return irrelevant videos if the positive examples are noisy.

## Exact research scoring formula

For a candidate video c and local positive examples p1...pn tagged to the
current query:

    similarity(c, p) = max(0, cosine(embed(c), embed(p)))
    weighted_similarity(c, p) = similarity(c, p)
                                * min(1, max(0.25, preference_weight(p)))
    positive(c) = mean(top_2(weighted_similarity(c, p)))

We require at least **two distinct positive example IDs**. This makes it
less likely that one accidentally liked title determines an entire query.

Next, relative to all the candidates of that search batch:

    center = median(positive(candidate_scores))
    spread = max(positive_scores) - min(positive_scores)
    scale = max(0.12, spread / 2)

    interest_alignment(c) =
        0                                 if spread < 0.06
        clamp(2.6*(positive(c)-center)/scale, -3, +3) otherwise

No universal cosine >= 0.52 threshold is used, since absolute BGE cosine
values are not calibrated probabilities.

For explicit dislikes tagged with the same search query:

    disliked_similarity(c) = max(
        max(0, cosine(embed(c), embed(disliked_video))))
    disinterest_contrast(c) = -4.0 * max(
        0, disliked_similarity(c) - positive(c))

Dislike contrast is applied even when the positive scores barely vary.
It penalizes candidates more like discarded titles than your positive
examples, rather than suppressing an entire topic after one dislike.

These values are additive research-only scoring features in
content_ranker.rank_content. **Why this video?** shows both
"Search-context alignment" and "Not-interested contrast" when present.
All coefficients and minimum-evidence rules are hypotheses, not trained
weights, and should be evaluated with real usage before merging to main.

## Search vs. Home: intentional differences

When Ranking is set to v0.10 Watch Learning:

- **Home / Explore:** current default search planning, existing local watch
  engine, plus contextual alignment features for topics with enough examples.
  Exact saved videos remain excluded as before.
- **Direct search:** use only the literal phrase you typed; apply contextual
  ordering to its available videos if supported by local evidence; exclude
  previously dismissed IDs but allow already watched IDs so you can replay
  something you intentionally searched for.
- **New or ambiguous search without enough evidence:** return normal results.
  We favor **soft reranking rather than hard filtering** to avoid hiding
  relevant videos with unfamiliar titles. A mismatched result may still
  appear lower in search results; the model is not a guaranteed relevance
  classifier.
- **v0.9 Semantic, v0.8 Content and v0.6 Baseline:** their original
  recommendation scoring behavior is unchanged. Their explicit searches
  keep YouTube order, except for exclusion of exactly dismissed IDs.

## Negative feedback and capacity

Not interested continues to save rating=-1 and update
last_activity_at in SQLite. It removes the visible card immediately.

The watch model uses up to 12 disliked examples. Previously this was the
first 12 sorted by video ID, so a new dislike could be ignored if enough
older ones existed. They now sort by **recent feedback activity** and ID
before selection. Each explicit dislike is negative regardless of how long
its original video was watched.

We do NOT generate negative training examples from short exits, failures to
click or missing impressions. No unseen video is automatically a dislike.

## Verification

Run on Fedora:

    source .venv/bin/activate
    python -m pytest -q

New tests cover arbitrary ambiguous words, topic-specific learning,
unseen search queries, disliked-exact-ID filtering, similar negative
evidence, single YouTube query per search, clicked-feed deduplication, and
the browser's persistent mode and stale-card protection.

You can compare raw vs context-aware search for the *same term*:

    curl 'http://127.0.0.1:8000/api/discover?q=your+term&ranker=content'
    curl 'http://127.0.0.1:8000/api/discover?q=your+term&ranker=watch'

The latter requires a previously prepared local BGE model, just as v0.10
Watch Learning already does. Watch for the
interest_alignment/disinterest_contrast values under score_breakdown
when relevant evidence exists.

To test from Firefox, update the research branch, restart Uvicorn, hard
refresh with Ctrl+Shift+R, then choose v0.10 Watch Learning (experimental).
The ranking selection should now survive a browser refresh.

## Privacy, maintenance, and limitations

- This approach uses no extra YouTube queries or cloud inference. The
  normal user-requested searches, optional refined discovery searches,
  thumbnails and iframe playback have their existing network behavior.
- Search-mode contextual scoring creates additional local embedding cache
  entries for title+channel descriptors, identified by hashes and stored in
  the existing local SQLite database. It may add local inference latency.
- One search result may be a false positive/negative. In the absence of
  complete real-world evaluation, these improvements are **experiments**.
- The unauthenticated development server must remain on localhost or a
  temporary trusted LAN, never exposed to the public internet.
