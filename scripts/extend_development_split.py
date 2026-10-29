"""Preserve an old split and add deterministic PASS training cells, with full SHA.

Never creates final-test claims. Source HDF5 is read-only; hash reads are serial
and rate-limited. Existing frozen outputs are not overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

from jev_uav_track.data import HDF5Episode
from jev_uav_track.holdout import require_development_source
from jev_uav_track.split_integrity import validate_split_integrity

PATTERN = re.compile(r"(?P<cell>.+)_seed(?P<seed>\d+)\.hdf5$")


def select_additions(rows, *, excluded_seeds, limit):
    cells = {}
    for row in rows:
        path = Path(row["path"])
        match = PATTERN.fullmatch(path.name)
        if (row.get("validation_status") != "PASS" or not match
                or path.parent.name != "quota_grid_20261004_formal"):
            continue
        seed = int(match["seed"])
        if seed in excluded_seeds:
            continue
        require_development_source(path, row)
        item = dict(row, seed=seed, filename=path.name, split="train", cell=match["cell"])
        existing = cells.get(match["cell"])
        if existing is None or seed < existing["seed"]:
            cells[match["cell"]] = item
    if len(cells) < limit:
        raise ValueError(f"Insufficient independent cells: requested {limit}, found {len(cells)}")
    return [cells[key] for key in sorted(cells)][:limit]


def throttled_sha(path, mib_per_s):
    digest = hashlib.sha256()
    total = 0
    started = time.monotonic()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
            delay = total / (mib_per_s * 1024**2) - (time.monotonic() - started)
            if delay > 0:
                time.sleep(delay)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-split", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--exposure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--additions", type=int, default=22)
    parser.add_argument("--hash-mib-per-s", type=float, default=16)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Frozen output already exists; choose a new version")
    if not 1 <= args.additions <= 43 or not math.isfinite(args.hash_mib_per_s) or args.hash_mib_per_s <= 0:
        raise ValueError("Positive bounded additions and finite hash rate required")
    base = json.loads(args.base_split.read_text(encoding="utf-8"))
    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    exposure = json.loads(args.exposure.read_text(encoding="utf-8"))
    exclusions = set(exposure["effect_exposed_seeds"]) | {r["seed"] for r in base["episodes"]}
    additions = select_additions(coverage["episodes"], excluded_seeds=exclusions, limit=args.additions)
    entries = [dict(r) for r in base["episodes"]] + additions
    for entry in entries:
        require_development_source(entry['path'], entry)
    if not 30 <= len(entries) <= 60:
        raise ValueError(f"30–60 target not met: {len(entries)}; do not silently relax selection")
    sources = {}
    for p in (args.base_split, args.coverage, args.exposure):
        sources[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    for index, entry in enumerate(entries):
        path = Path(entry["path"])
        before = path.stat()
        episode = HDF5Episode.open(path, load_images=False)
        require_development_source(episode.path, episode.metadata)
        if episode.metadata.get("validation_status") != "PASS":
            raise ValueError(f"Source no longer PASS: {path.name}")
        digest = throttled_sha(path, args.hash_mib_per_s)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Source changed during audit: {path.name}")
        entry.update(sha256=digest, bytes=after.st_size, frame_count=episode.frame_count,
                     recorded_yaw_available=episode.camera_yaws_deg is not None)
        print(f"Verified {index + 1}/{len(entries)} {path.name}", flush=True)
    validate_split_integrity(entries)
    report = {
        "split_version": 2,
        "source_mode": "read_only_pass_development_extension",
        "source_manifest_sha256": sources,
        "policy": {"final_test_available": False, "independent_town_test_available": False,
                   "final_test_reason": "All audited towns already exposed; untouched town data still required",
                   "old_assignments_preserved": True, "additions_train_only": True,
                   "selection": "One minimum unused seed per sorted semantic cell in quota_grid_20261004_formal",
                   "hash_read_rate_mib_per_s": args.hash_mib_per_s,
                   "training_executed": False},
        "split_counts": dict(Counter(r["split"] for r in entries)),
        "effect_exposed_seed_count": len(exposure["effect_exposed_seeds"]),
        "episodes": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as dest:
        json.dump(report, dest, ensure_ascii=True, indent=2)
    print(json.dumps({"output": str(args.output), "split_counts": report["split_counts"]}))


if __name__ == "__main__":
    main()
