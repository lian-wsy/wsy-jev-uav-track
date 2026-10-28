"""Read-only audit of the explicit image/state/yaw contract in a frozen split."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from jev_uav_track.data import HDF5Episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.split.read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["episodes"]:
        if entry.get("validation_status") != "PASS":
            raise ValueError("Only frozen PASS entries may be audited")
        episode = HDF5Episode.open(entry["path"], load_images=False)
        if episode.metadata.get("validation_status") != "PASS":
            raise ValueError("Source HDF5 must also declare PASS")
        lag = episode.image_timestamps - episode.state_timestamps[episode.state_indices]
        rows.append({
            "filename": episode.path.name,
            "split": entry["split"],
            "frames": episode.frame_count,
            "max_absolute_alignment_error_s": float(np.max(np.abs(lag))),
            "image_dt_min_s": float(np.min(np.diff(episode.image_timestamps))),
            "image_dt_max_s": float(np.max(np.diff(episode.image_timestamps))),
            "recorded_camera_yaw_available": episode.camera_yaws_deg is not None,
        })
    report = {
        "protocol": "explicit_same_snapshot_timestamps_backward_velocity_v2",
        "source_mode": "read_only_state_arrays_no_rgb",
        "episode_count": len(rows),
        "episodes": rows,
        "limitation": "Schema/time audit only; does not certify simulator isolation or counterfactual outcomes.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"episodes": len(rows), "output": str(args.output)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
