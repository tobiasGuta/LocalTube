# LocalTube v0.9: Semantic Interest Engine

This is the developer-oriented explanation of how LocalTube's optional semantic
recommendations work. **You can read or change the algorithm without installing the
model.** All normal LocalTube features continue to work without FastEmbed.

## The problem we're addressing

Words are not meanings. A search for "knowledge" can find educational videos,
but it can also find a football player whose name includes "Knowledge".

LocalTube already has two good ranking modes:

- **v0.6 Baseline:** selected interests, likes, watching activity, title terms,
  and creator/topic diversity.
- **v0.8 Content:** TF-IDF title cosine similarity + weighted features + the
  Focused/Explore slider. This is the default and requires no model.

**v0.9 Semantic** adds a pretrained language encoder for comparing the
*meaning* of titles. It also updates your personal preference examples as
you like, dislike or meaningfully watch videos. Nothing is trained by a
cloud service and no hardcoded map of "knowledge -> psychology" is needed.

> A critical distinction: this is **pretrained language understanding plus
> an adaptive local preference profile**, not training or fine-tuning the
> neural network on your viewing history. We can experiment with learned
> ranking coefficients in a later version.

## Getting started on Fedora

The basic app still needs only requirements.txt and Python 3.10+.
For semantic mode, use **Python 3.11+** (FastEmbed 0.9.x supports 3.14).

From the LocalTube directory, using your existing Python virtual environment:

    source .venv/bin/activate
    python -m pip install -r requirements-semantic.txt
    python -m semantic_engine status
    python -m semantic_engine download
    python -m semantic_engine status
    python -m pytest -q
    python -m uvicorn app:app --host 127.0.0.1 --port 8000

**The download command makes external requests to get model files.** The model
is stored in data/models/ (ignored by Git). No download occurs when starting
FastAPI, opening the homepage, using v0.8, or importing semantic_engine.py.

Restart the FastAPI server and hard-refresh the browser after setup.
Under Ranking, choose **v0.9 Semantic (local)**. If the encoder is absent,
the semantic choice is disabled; v0.8 and v0.6 remain available.
To turn off additional YouTube discovery searches, uncheck
**Manage Interests -> Use liked-video title keywords and creator names...**
Semantic *ranking* can still compare the candidates returned by basic topic
searches.

The model is currently **BAAI/bge-small-en-v1.5** (384 dimensions, English),
served by FastEmbed and licensed **MIT**. FastEmbed uses the **Apache-2.0**
license, and executes the ONNX encoder locally. The model is approximately
67 MB, plus separate package/runtime dependencies. Actual disk usage varies.
This does **not** require a Google login, GPU, remote embeddings API, or Ollama.

Sources:
- FastEmbed: https://github.com/qdrant/fastembed
- FastEmbed model registry: https://qdrant.github.io/fastembed/examples/Supported_Models/
- Model card: https://huggingface.co/BAAI/bge-small-en-v1.5
- ONNX Runtime: https://onnxruntime.ai/

## System architecture

    User chooses a video to like / watch / dislike
                     |
                     v
         SQLite local video library
       (title, channel, tags, rating,
       approximate watched seconds, etc.)
                     |
                     v
      Positive/negative preference examples
            derived *when ranking*
                     |
                     v
    YouTube search: broad topics + (optionally)
    liked-video keywords / creator queries
                     |
                     v
      Optional one-hop semantic discovery
       from a related public candidate title
                     |
                     v
            Candidate videos
                     |
                     v
      Local ONNX embedding encoder (384-D)
                     |
                     v
       Cached vectors in SQLite (float32)
                     |
                     v
    Similarity to your positive AND negative
              historical examples
                     |
                     v
       Existing v0.8 content-based score
         + semantic features, followed by
           Feed Balance diversity penalties
                     |
                     v
         Ranked personalized homepage

The same saved videos drive v0.8 and v0.9. The embedding encoder does not
send requests during inference. YouTube discovery and embedded playback
still contact YouTube/Google; see Privacy below.

## The math: from titles to meaning

For each candidate title c, the encoder produces a vector E(c) in R^384.
We normalize all vectors and compare them with cosine similarity:

    cos(c, h) = dot(E(c), E(h))  # both normalized

Each historical video h has a *signed* behavior weight, computed in
semantic_engine.preference_weight(h).

- An explicit **like** is a strong positive example.
- A watch of at least **45 seconds** is weaker evidence, strengthened by
  duration-normalized watch progress and completion counts.
- An explicit **dislike** is a negative example.
- **Play clicks alone** aren't assumed to mean enjoyment.
- Saving a video without watching or liking it provides no positive example.

The current equation (chosen heuristically, **not fitted coefficients**) is:

    positive(c) = 3.6 * max(0, max_h{cos(c,h)*weight(h)} - 0.20)
    negative(c) = -3.8 * max(0, max_d{cos(c,d)} - 0.15)

where h ranges over **positive** examples and d over **disliked** examples.
Only the 24 strongest positive and 12 negative title examples are used.

Using a **maximum across examples**, rather than one average vector, helps
preserve different interests. You can enjoy both games and psychology without
a single average vector collapsing them together.

The final score is:

    score(c) = v0.8_features(c)
             + positive(c) + negative(c)
             - feed_balance_diversity_penalty(c)

The base v0.8 features include topic, TF-IDF title, creator, discovery
position and multi-query agreement. Greedy reranking reduces repeated
topics/creators with the same user-controlled Feed Balance setting.

You can inspect these contributions using the **Why this video?** action in a
recommended card's ⋮ menu. This opens an accessible dialog next to your
current context—not a status message above the grid. The dialog shows the
video's reason, total score and positive/negative contributions, including
semantic similarities and the Feed Balance penalty. You can close it with the
button or Escape. It uses only the data already returned by the local feed
API; opening the explanation makes no new network request.

The score is not a calibrated probability or a scientific measure of
what you will enjoy.

## How it "learns" your preferences

1. You explicitly like, dislike or watch a video.
2. The browser sends only ordinary playback events to your local FastAPI
   server, as in v0.6. Your SQLite video record is updated.
3. The next semantic feed request **recomputes the preference examples**
   using current activity.
4. Embeddings of titles are cached and reused. If the user feedback changes,
   the preference examples change immediately without retraining the
   language model.
5. New videos can now be ranked based on the semantic resemblance of their
   titles to your separate liked/watched examples.

The current implementation does **not** learn numeric scoring coefficients
automatically, understand a full video's spoken content, or detect whether
you're genuinely paying attention. Watch time is only an approximate signal.

**Cold start:** With zero meaningful likes/watches, v0.9 has no personalized
semantic signal and ranks like the v0.8 content algorithm. Start by liking a
few examples of educational content you enjoy (e.g. critical thinking,
communication, fun science facts) and gaming videos you enjoy. You don't
have to enter a dictionary of matching categories.

## How candidate discovery works

An embedding ranker can **only rank candidates it knows about**.

- We reuse v0.8's up-to-five searches from discovery_plan.build_queries.
  These may include interests such as "knowledge", *plus* short title
  keywords and creator phrases from liked/watched examples if you allow it.
- With v0.9 selected **and** refined discovery enabled, the local model
  chooses a semantically similar public candidate title from results that
  are already available. We take **at most one** extra search using its
  short salient title keywords.
- This is a bounded **one-hop expansion** (up to six queries total), not
  LLM-generated queries or an arbitrary hardcoded topic dictionary.
- An expansion only happens above a semantic similarity threshold of 0.52
  and below a lexical overlap threshold of 0.50. These are experimental
  choices, not guarantees of relevance.
- Each search is cached by the existing one-hour SQLite discovery cache.
- With refined searches turned **off**, there is no semantic expansion
  search and the semantic model reranks only the broad topic candidates.

It can still miss educational videos if YouTube search never returns them.
A future release could index subscribed creators, RSS feeds or approved
public catalogs; that is not included here.

## File guide for contributors

| File | Responsibility |
| --- | --- |
| semantic_engine.py | Model lifecycle, optional download, SQLite vector cache, behavior weights, semantic similarity, one-hop expansion |
| content_ranker.py | Existing TF-IDF features, new optional semantic scores, greedy Feed Balance |
| discovery_plan.py | Topic, short title keyword and creator search planning |
| app.py | FastAPI routes, non-destructive SQLite schema migration, model opt-in checks |
| static/app.js | Model readiness, ranking selection, explanations and UI updates |
| static/index.html / style.css | Optional model setup and model selection controls |
| tests/test_semantic_engine.py | Fake local encoder, deterministic scoring, cache, API and privacy tests |
| requirements-semantic.txt | Optional ONNX embedding dependencies |
| docs/SEMANTIC_ENGINE.md | This design, equations, and extension guidance |

## SQLite persistence and privacy

The original video library remains in the videos table. v0.9 adds
semantic_vectors(cache_key, model, vector, updated_at). The cache contains
hashes of the model namespace + title text and little-endian float32 vectors.
It does not duplicate raw titles, but **embeddings can still leak semantic
information**. Treat this SQLite database and backups as sensitive.

The model files live under data/models/. Both data/ and your virtual
environment are ignored by Git. You can inspect the local model status:

    python -m semantic_engine status

To clear **only derived vector embeddings**, keeping watch history and likes:

    python -m semantic_engine clear-cache

To remove the model itself, stop LocalTube and delete data/models/ manually.
The default v0.8 content engine requires no model and still works.

**External contact in this feature:**
- During the explicit download, model-hosting servers may see your IP.
- YouTube searches reveal your search terms and IP. One-hop search
  expansion reveals a *public candidate title's* keywords, not your full
  local viewing-history database. You can disable refined searches.
- The same YouTube embed and optional Google thumbnails used in earlier
  LocalTube versions remain unchanged.
- Inference, vectors, feedback and personalized scores are computed locally.
  No per-user profile is uploaded to a semantic API.
- Bind LocalTube to 127.0.0.1 until authentication exists; there's no
  protection for exposing local preferences to the public Internet.

## Errors and fallback

- If the model isn't installed, GET /api/semantic/status reports ready:false.
  The UI keeps v0.9 disabled. GET /api/feed?ranker=semantic returns HTTP 409.
- If an already prepared model cannot load, the feed returns a clear 503
  instead of pretending a different algorithm is semantic.
- You can always choose v0.8 Content or v0.6 Baseline. Both remain
  independent of FastEmbed installation and embeddings.
- If YouTube search fails entirely, the existing saved library still works.
- Tests use fake encoders and mocked discovery; the default test suite
  **does not need an Internet connection or downloaded model**.

## Experiment safely

Examples of useful experiments for a newcomer:

1. Change the behavior threshold (45 seconds) in preference_weight, with
   corresponding tests. Check whether long videos get unjustified bonuses.
2. Compare max-example similarity with top-k similarity, then test whether
   a mixture of gaming and learning interests is preserved.
3. Add metadata fields (e.g. public descriptions) to video embeddings.
   Change CACHE_REVISION so older cached vectors aren't mixed with new data.
   This may require privacy and rate-limit changes in metadata discovery.
4. Measure relevance AND topic/creator diversity using a fixed, locally
   recorded candidate pool. Don't treat unwatched results as dislikes.
5. Try another permissively licensed encoder, test model size / speed /
   semantic relevance and document license and cache-revision changes.
6. Eventually learn coefficients from *explicit* preferences and held-out
   interactions. A learned weight model is separate from the embedding
   encoder and must beat v0.8 in evaluated use.

The important principle: **the new system should prove useful compared
to the v0.8 baseline before becoming the default.** Its implementation
is deliberately small, debuggable and optional rather than magical.
