# v0.11 Local Learning Ranker — research prototype

**Branch:** research/v0.11-online-ranker
**Base:** research/v0.10-evaluation, at commit d9b74216401971df2d51337d31e8cf17f25649ee
**Status:** experimental, not merged to main or v0.10.

This is an actual *trained* recommendation component. The existing local
pretrained BGE encoder remains frozen (no fine-tuning or cloud inference).
v0.11 learns a small **regularized logistic regression** from your own
verified reactions to recommendations, using only Python's standard library.

No named categories, creators, TED/movie/toy-specific rules, labels to edit,
account setup or additional Python ML packages are needed.

## Set up / run on Fedora

Follow the existing setup in docs/SEMANTIC_ENGINE.md first. **FastEmbed
and ONNX Runtime require a compatible Python**, and your previous 3.15
interpreter could not install the ONNX dependency. Your working path was to
install Python 3.14, then create a NEW 3.14 virtual environment rather than
upgrading the broken 3.15 venv in-place.

From the repository:

    git fetch origin
    git switch --track origin/research/v0.11-online-ranker

If already on that branch:

    git pull --ff-only

Use an activated Python 3.14 environment, such as:

    source "$HOME/.virtualenvs/localtube-314/bin/activate"
    python --version
    python -m pip install -r requirements.txt -r requirements-semantic.txt pytest
    python -m pytest -q
    python -m semantic_engine status
    python -m uvicorn app:app --host 127.0.0.1 --port 8000

**Do not delete your data/ directory or old venv to try this.** If the model
cache is already ready, no download should be needed.

Choose **v0.11 Local ML (experimental)** in Ranking and watch normally.
This is a separate opt-in choice: v0.6, v0.8, v0.9 and v0.10 still exist.
If you don't have the BGE model, v0.11 stays disabled.

## What actually trains

The ML features are frozen *at the time each recommendation is served*:

    topic / 4.0
    title / 3.5
    creator / 2.2
    discovery / 0.5
    agreement / 0.3
    disliked_similarity / 2.0
    semantic_positive / 3.6
    semantic_negative / 3.8
    interest_alignment / 3.0
    disinterest_contrast / 4.0

Each feature is clipped to [-1, 1]. The features are semantic/ranking
signals learned from user history by v0.10 and public metadata, not any
hardcoded topic words or categories. The extra v0.11 model score NEVER enters
its own training inputs. Frozen snapshots avoid leakage from subsequent
likes/dislikes when the user's profile changes.

For a feature vector x, v0.11 trains a binary logistic predictor:

    probability(positive | x) = sigmoid(bias + sum(weight_i * x_i))

30 short SGD passes over a small, bounded sample of **real** outcomes,
L2 regularization, finite coefficient bounds, and deterministic ordering are
implemented in learning_ranker.py. Each changed label refits the tiny model
from the current labeled examples, so corrections and deletions take effect
immediately. This is *local, repeated online adaptation*, not incremental
neural-network fine-tuning, and not an industrial two-tower recommender.

The ML feed reranks the v0.10 candidate list with a deliberately bounded
adjustment:

    ml_adjustment = clamp(2.5 * (prediction - 0.5), -1.25, +1.25)
    final_score = v0.10_score + ml_adjustment

The v0.10 diversity penalty is retained; why-this-video explanations
include the learned ML adjustment when it is active. The model isn't
a calibrated satisfaction probability, and more data doesn't guarantee
better predictions.

## Which events can train?

ONLY events tied to a **previously recorded, visible** v0.11 feed card:

1. **Meaningful watch**: positive (1), after genuine per-session watched
   seconds cross v0.10's duration-aware threshold.
2. **Like**: positive (1).
3. **Not interested**: negative (0).

That is the entire label policy.

- Being returned by YouTube does not count as seeing a card.
- A card entering the viewport is a **recorded impression only**,
  not negative feedback. The browser uses IntersectionObserver with
  >=60% visibility; clicking the card also confirms it.
- Not clicking, quick exits, buffering, seeking, pauses, unviewed results
  and missing interactions are **never negative training examples**.
- No manual relevance ratings are required. Dislikes are optional,
  but the ML model cannot responsibly learn a binary distinction until it
  has both genuine positive and negative examples.
- Repeated heartbeat submissions are already sequence-deduplicated by
  the session tracker. The same video contributes at most one current
  labeled sample, even across repeated recommendations and watches.
- The most recent explicit Like/Not interested overrides an implicit
  positive watch. Removing an explicit rating removes that direct
  training label; v0.10's independent watch history is not erased.
- Existing old likes and watch totals still influence v0.10's semantic
  feature generation. They are *not* converted into fabricated v0.11
  exposures or training examples.

**Cold start:** v0.11 produces exactly the v0.10 ranked feed until it
has at least **8 different videos** with valid labeled exposures, of which
at least **3 are positive** and **3 are negative**. This is a conservative
experiment threshold, not a scientifically calibrated optimal requirement.

## Local persistence & privacy

Idempotent SQLite migration adds:

    ml_impressions(token PRIMARY KEY, video_id, created_at,
                   seen_at NULLABLE, features_json)
    ml_outcomes(video_id PRIMARY KEY, token, label {0,1},
                source, updated_at)
    ml_model(id=1, weights_json, bias, positives,
             negatives, trained, updated_at)

The server issues random per-card impression tokens; the browser sends back
a token only when that card is actually seen or interacted with.
It does NOT send training weights or features from the browser.

At most **2,000 recent impressions** and **400 most recent labeled
outcomes** are used/retained by the research model. Pruned outcomes
force retraining. A video deletion drops its v0.11 outcomes as well.
The underlying saved video, sessions, ratings and old embeddings remain
in their original tables and are unaffected by a v0.11 reset.

Normal YouTube discovery, thumbnails and embedded video playback retain
their existing external network behavior. All feature snapshots, model
training, weights, feedback joins and inference run locally, with no
telemetry or third-party training endpoints.

The unauthenticated development server is **only safe on localhost or a
trusted LAN**; never port-forward it onto the public internet.

## Inspect the learner without exposing private training files

    curl 'http://127.0.0.1:8000/api/learning/status'

Returns exposed-card count, positive/negative label counts, and whether
the trained weights are active. Those counts are **training diagnostics,
NOT proof of recommendation quality**.

To delete ONLY v0.11's snapshots, outcomes and trained weights:

    curl -X DELETE 'http://127.0.0.1:8000/api/learning/data'

This does NOT delete the original watch history, saved library, v0.10
session data, topics or embeddings.

## Evaluation, safety & honest limitations

**This branch does not establish that v0.11 beats v0.10.** We have
implementation regression tests, but recommendation quality requires
time-ordered data collected after the update. In particular, impressions
are not randomized exposures: watched content is selected by the old
ranker, so simple training accuracy is not a fair A/B measurement.
Do not mistake the model's raw predicted positive probability for a
well-calibrated probability of liking.

We can next add a local-only forward-in-time holdout (with a strict
data boundary and sufficient examples) or controlled A/B exposure to
measure improvement, without manual labels.

Search under v0.11 retains v0.10's contextual query disambiguation,
but the trainable component applies to personalized Home/Explore ranking
only. Direct user searches do NOT silently create training impressions.
The model does not download video transcripts or claim to understand
the video's actual frames/audio.

All coefficients, feature choices, cutoff thresholds and minimum counts
are research hypotheses, not proven tuned values. Main and v0.10 remain
stable baselines. Use this branch for experimentation only.

## Tests

    python -m pytest -q

New tests use entirely synthetic video metadata and fake local encoders:
feature normalization, true weight learning, unseen/visible distinctions,
duplicate exposure handling, short-watch neutrality, session-triggered
positive labels, Not interested overrides and unlikes, sparse cold start,
train-once-per-video, reset/delete, bounded score integrity, and exact
v0.10 fallback/candidate-pool comparison.
