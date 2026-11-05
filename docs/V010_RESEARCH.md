# v0.10 Research: Evaluation and Multi-Interest Experiments

**Status: EXPERIMENTAL.** This work lives on branch
**research/v0.10-evaluation**, not main. The original v0.9 feed remains
available unchanged. In addition to the frozen-pool evaluation documented
here, this research branch NOW has an optional new **v0.10 Watch Learning**
runtime mode, non-destructive local playback-session table, and automatic
learning from meaningful watches. See [WATCH_LEARNING.md](WATCH_LEARNING.md)
for that implementation, privacy controls, and limitations. Main is unaffected.

## What we're testing

**Hypothesis:** Representing liked and watched videos as several independent
semantic interest groups may perform better than v0.9's single
strongest-matching example, particularly when somebody enjoys both
education and gaming.

We're testing this, not claiming the result in advance.

From the repository root:

    source .venv/bin/activate
    python -m pytest -q
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json --k 5 --json
    python -m experiments.evaluate experiments/fixtures/mixed_interests.json --threshold 0.85

These example commands require **no model, no network, and no API keys**.
The fixture uses *handcrafted four-dimensional toy vectors*, not real language
embeddings. It proves deterministic behavior, not real-world accuracy.

### Evaluate with an already downloaded local BGE model

    python -m experiments.evaluate experiments/fixtures/mixed_interests.json --local-model

The optional local-model mode requires the existing FastEmbed/ONNX model from
v0.9, installed with requirements-semantic.txt and explicitly prepared using
python -m semantic_engine download. Model preparation contacts the model host.
After preparation, the experiment loads the model entirely locally and uses a
**temporary SQLite embedding cache**, never your production database.

The toy relevance labels may not reflect actual language-model judgments.

## Three variants, identical candidate videos

| Variant | Semantic model |
| --- | --- |
| v0.8_content | None. Existing TF-IDF title and topic scoring |
| v0.9_semantic_reference | Exact formula from current semantic_engine.semantic_features, on fixed supplied vectors |
| v0.10_multi_interest_trial | Experimental semantic interest groups with recency weighting |

All three call the **existing** content_ranker.rank_content with the
same batches, saved history, selected topics and Feed Balance. Thus, the
test isolates semantic ranking, not additional YouTube discovery queries.

The tests assert that the frozen v0.9 formula exactly matches
semantic_engine.semantic_features on a deterministic fake encoder.

No ranker ever receives the candidate's manually judged relevance label.

## Experimental interest model: how it works

File: experiments/v010.py

1. Reuse the exact v0.9 behavior signal from semantic_engine.preference_weight.
   Likes and meaningful watches are positive; dislikes are negative.
   Clicks or saved videos without watches or likes aren't assumed positive.
2. Adjust positive weights by recency:

       recency(age_days) = 0.65 + 0.35 * 2 ** (-age_days / 45)

   Older interests remain meaningful due to the 0.65 floor. Missing
   timestamps get neutral weighting.
3. In descending strength order, group positive video vectors by cosine
   similarity to the nearest existing group's normalized, weighted centroid.
   If similarity is less than the experimental threshold 0.78, create
   another cluster; keep at most 8 groups, without forcibly merging
   unrelated examples.
4. Each group summarizes one neighborhood of interests: its centroid,
   supporting video IDs, and mean strength. There are **no hand-coded topic
   names or synonym maps**.
5. For each candidate, use the strongest **group** similarity and a modest
   support multiplier:

       support_factor = 0.78 + 0.08 * min(3, group_size)
       group_signal = cosine(candidate, centroid) * weight * support_factor
       semantic_positive = 3.6 * max(0, max_group_signal - 0.20)

6. Keep the v0.9 disliked-title penalty unchanged:

       semantic_negative = -3.8 * max(0, max_disliked_cosine - 0.15)

These are **hand-selected research coefficients**, not learned neural-network
weights, trained two-tower embeddings, or validated thresholds. BGE embedding
cosines are not calibrated probabilities; the 0.78 clustering threshold needs
real-video evaluation and likely adjustment.

## Metrics: what we measure, and what we don't

- **NDCG@k**: graded ranking quality, rewarding valuable videos at the top.
- **Precision@k**: fraction of the first k with relevance label 2 or 3.
- **Semantic diversity@k**: average pairwise 1 - max(0, cosine similarity).
  Higher means less redundant titles, not necessarily higher satisfaction.
- **Creator/topic coverage**: number of distinct creators and search topics.
- **Label coverage**: how many unseen candidates actually have judgments.

Human labels have the following scale:

| Label | Meaning |
| --- | --- |
| 0 | Irrelevant or would avoid |
| 1 | Slightly relevant |
| 2 | Useful or would consider watching |
| 3 | Highly relevant |
| null | Not judged / not shown (NOT a negative) |

**Crucial:** if **any** candidate in the frozen pool lacks a judgment,
NDCG@k and Precision@k return **null**. This intentionally prevents
unwatched or unseen videos from being silently treated as negatives.
The other descriptive diversity metrics remain available.

No online A/B testing, neural network training, remote analytics, or
impression tracking has been added.

## How to test real recommendations privately

The optional export command reads your local video history and **existing**
SQLite discovery cache; it does NOT contact YouTube or fetch fresh candidates.

    python -m experiments.capture --max-candidates 20

That explicitly creates the private file data/research/snapshot.json.
The exporter selects cached candidates round-robin across search topics,
deduplicates video IDs, excludes your saved videos, and initializes every
candidate's relevance label to null.

If the cache is empty, visit the Home page to populate it before capturing.
**Opening Home normally makes YouTube search requests**; the capture command
does not.

Open the private JSON in your editor. Under batches > videos, rate each
candidate by replacing its label null with 0, 1, 2 or 3. Don't look at
algorithm rankings first; that could bias your human judgments.

Then compare all three rankers with **real local embedding vectors**:

    python -m experiments.evaluate data/research/snapshot.json --local-model --k 10

The local-model flag is required because exported personal snapshots do
NOT contain static fixture vectors.

To take another snapshot, use a different output name:

    python -m experiments.capture --output data/research/session-02.json

The capture command uses exclusive file creation, so existing manually
labeled snapshots cannot be accidentally overwritten.

**Very important privacy note:** The exported JSON includes your video
titles, ratings, interests, and viewing-history metadata. Keep it private.
It goes under the Git-ignored data/ directory by default. Do not commit
your personal snapshots to GitHub or share them publicly. No research
command performs a background upload.

## Evaluating fairly

Before claiming a win:

1. Confirm all algorithms used the same frozen candidate IDs and Feed Balance.
2. Label all candidate titles independently, *before* reviewing algorithm ranks.
3. Evaluate multiple **real** snapshots, not just the toy fixture.
4. Preserve at least one later snapshot for final evaluation rather than
   repeatedly tuning thresholds to the same labels.
5. Check how the mixed gaming/education topics and creator variety behave.
6. Inspect whether semantic dislikes actually demote unwanted content.
7. Consider latency, CPU footprint, and how the algorithm handles few likes.

Our metrics are not calibrated click probabilities, unbiased online
estimates, or proof that one algorithm is universally best.

## Source guide

- experiments/v010.py: pure vector math, frozen v0.9 scoring reference,
  grouped-interest experiment and gentle recency.
- experiments/evaluate.py: fair candidate comparison and grading metrics;
  optional real local FastEmbed inference.
- experiments/capture.py: explicit **offline** export from existing cache,
  no YouTube requests, no automatic label assumptions.
- experiments/fixtures/mixed_interests.json: handcrafted deterministic
  learning + gaming case with likes, dislikes, and candidate labels.
- tests/test_v010_research.py: baseline parity, mixed interests,
  recency, no-network capture, missing labels, evaluation and CLI tests.
- docs/SEMANTIC_ENGINE.md: explanation of the stable v0.9 production model.

## Research to read next

- Google (Covington et al.), Deep Neural Networks for YouTube Recommendations
  (2016): https://research.google/pubs/deep-neural-networks-for-youtube-recommendations/
- ComiRec multi-interest recommendation (2020):
  https://arxiv.org/abs/2005.09347
- MMR / diversity-based reranking (1998):
  https://aclanthology.org/X98-1025/
- BGE small English model card and scoring caveats:
  https://huggingface.co/BAAI/bge-small-en-v1.5

## What is deliberately NOT implemented yet

- No modifications to production v0.9 or your browser experience.
- No automatic YouTube queries for research experiments.
- No new weights trained by ML or two-tower neural networks.
- No vector index or extra packages.
- No merged claim of quality until we have privately judged, real cases.

Next experiments should test calibrated semantic retrieval, top-k preference
prototypes, semantic MMR diversity and time-based evaluation one at a time.
