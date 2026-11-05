"""Explicitly export a PRIVATE, frozen research snapshot using local cached metadata.

No YouTube calls or downloads. This reads your library/watch history and
discovery_cache, then creates an ignored file under data/research/. Exported
titles and preference signals are sensitive. Never commit or share the JSON.

Usage:
    python -m experiments.capture
    python -m experiments.capture --max-candidates 20 --refined
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from discovery_plan import build_queries
from recommender import build_profile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "research" / "snapshot.json"


def snapshot_from_cache(*, max_candidates: int = 24,
                        refine: bool = False, balance: int = 65) -> dict:
    """Purely local read: use the same topic plan and cached search results."""
    if not 1 <= max_candidates <= 100 or not 0 <= balance <= 100:
        raise ValueError("max_candidates must be 1..100; balance 0..100")
    from app import DB_PATH, connect, videos
    if not DB_PATH.is_file():
        raise ValueError("LocalTube database not found. Run LocalTube first.")
    library = videos()
    with connect() as db:
        selected = [row["topic"] for row in db.execute(
            "SELECT topic FROM topics ORDER BY topic")]
        row = db.execute(
            "SELECT refresh_cursor FROM feed_settings WHERE id=1").fetchone()
        cursor = row["refresh_cursor"] if row else 0
        cached = {
            row["query"]: row["payload"]
            for row in db.execute("SELECT query,payload FROM discovery_cache")
        }
    profile = build_profile(selected, library)
    plan = build_queries(profile, library, cursor, include_refined=refine)
    sources = []
    for topic, query in plan:
        payload = cached.get(query.casefold())
        if payload is None:
            continue
        try:
            videos_list = json.loads(payload)
            if not isinstance(videos_list, list):
                continue
        except ValueError:
            continue
        sources.append((topic, [v for v in videos_list
                                if isinstance(v, dict)
                                and isinstance(v.get("title"), str)
                                and isinstance(v.get("id"), str)]))
    if not sources:
        raise ValueError(
            "No cached searches match your topics yet. Open Home in LocalTube "
            "to populate the cache, then run this offline export again."
        )

    # Round-robin topics avoids a first-search-only snapshot. Keep exact
    # original relative order within each search, with global ID dedupe.
    kept = [[] for _ in sources]
    seen_ids = {v.get("id") for v in library}
    candidate_ids = set()
    cursors = [0] * len(sources)
    while len(candidate_ids) < max_candidates:
        progress = False
        for group, (_, results) in enumerate(sources):
            while cursors[group] < len(results):
                video = results[cursors[group]]
                cursors[group] += 1
                if video["id"] in seen_ids or video["id"] in candidate_ids:
                    continue
                candidate_ids.add(video["id"])
                kept[group].append({
                    "id": video["id"], "title": video["title"],
                    "channel": video.get("channel", ""),
                    "duration": video.get("duration"),
                    # Explicitly unjudged; never infer a dislike from non-click.
                    "label": None,
                })
                progress = True
                break
            if len(candidate_ids) >= max_candidates:
                break
        if not progress:
            break

    if not candidate_ids:
        raise ValueError("No unseen cached candidates available to evaluate.")
    history_keys = {
        "id", "title", "channel", "tags", "rating", "plays",
        "watched_seconds", "duration_seconds", "completions",
        "last_activity_at", "last_watched_at",
    }
    clean_history = [
        {key: value for key, value in video.items() if key in history_keys}
        for video in library
    ]
    return {
        "name": "Private frozen LocalTube cache snapshot",
        "description": "Local metadata only. All labels null until you manually judge each candidate.",
        "evaluation_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topics": selected,
        "balance": balance,
        "library": clean_history,
        "batches": [
            {"topic": topic, "videos": chosen}
            for (topic, _), chosen in zip(sources, kept) if chosen
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="Local private JSON output (default: data/research/snapshot.json)")
    parser.add_argument("--max-candidates", type=int, default=24)
    parser.add_argument("--balance", type=int, default=65)
    parser.add_argument("--refined", action="store_true",
                        help="Use already cached refined title/creator queries too")
    args = parser.parse_args(argv)
    try:
        snapshot = snapshot_from_cache(
            max_candidates=args.max_candidates, balance=args.balance,
            refine=args.refined)
        target = args.output.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents overwriting a snapshot with user labels.
        with target.open("x", encoding="utf-8") as out:
            json.dump(snapshot, out, indent=2)
            out.write("\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Snapshot export failed: {exc}\n")
    n = sum(len(b["videos"]) for b in snapshot["batches"])
    print(f"Wrote {n} unjudged cached candidates: {target}")
    print("PRIVATE: contains titles and watch history. Keep it out of Git.")
    print("Set candidate label to 0..3 before running --local-model evaluation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
