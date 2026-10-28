"""Read-only adapter for CarlaAir HDF5 episodes.

The adapter intentionally loads state and annotation arrays without loading the
large RGB tensor by default. It never writes to the source HDF5 file and keeps
the source path in each generated ``TrackingState`` for provenance.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from ..models import MissionConstraints, TargetBelief, TrackingState, UAVState


class HDF5SchemaError(ValueError):
    """Raised when an HDF5 episode cannot be mapped safely to tracking state."""


def _dependencies() -> tuple[Any, Any]:
    try:
        import h5py  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised without optional extras
        raise ImportError(
            "HDF5 support requires the optional dependencies; install with "
            "pip install -e \".[data]\""
        ) from exc
    return h5py, np


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            item = value.item()
            if item is not value:
                return _decode(item)
        except (ValueError, TypeError):
            pass
    if isinstance(value, Mapping):
        return {str(key): _decode(item) for key, item in value.items()}
    return value


def _finite_difference(values: Any, timestamps: Any, np: Any) -> Any:
    values = np.asarray(values, dtype=np.float64)
    timestamps = np.asarray(timestamps, dtype=np.float64)
    result = np.zeros_like(values, dtype=np.float64)
    if len(values) < 2:
        return result
    for index in range(1, len(values)):
        left, right = index - 1, index
        dt = float(timestamps[right] - timestamps[left])
        if abs(dt) < 1e-9:
            continue
        result[index] = (values[right] - values[left]) / dt
    return result.astype(np.float32)


def _as_vector(row: Any) -> tuple[float, float, float]:
    return (float(row[0]), float(row[1]), float(row[2]))


@dataclass
class HDF5Episode:
    """State-only view of one CarlaAir HDF5 episode."""

    path: Path
    frame_count: int
    image_height: int
    image_width: int
    image_timestamps: Any
    state_timestamps: Any
    state_indices: Any
    target_positions: Any
    camera_positions: Any
    target_velocities: Any
    camera_velocities: Any
    target_ranges_m: Any
    target_visible: Any
    target_in_frame: Any
    target_occluded: Any
    target_pixel_height: Any
    target_actor_ids: Any
    metadata: Dict[str, Any]
    images: Optional[Any] = None
    camera_yaws_deg: Optional[Any] = None

    @classmethod
    def open(cls, path: str | Path, load_images: bool = False) -> "HDF5Episode":
        """Open one episode read-only and normalize its state arrays."""

        h5py, np = _dependencies()
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        with h5py.File(source, "r") as h5:
            image_dataset = h5.get("observation/images/cam_high")
            if image_dataset is None or len(image_dataset.shape) != 4:
                raise HDF5SchemaError("observation/images/cam_high must be a 4D dataset")
            frame_count, image_height, image_width = map(int, image_dataset.shape[:3])

            def read(name: str, required: bool = False) -> Any:
                dataset = h5.get(name)
                if dataset is None:
                    if required:
                        raise HDF5SchemaError(f"Missing required dataset: {name}")
                    return None
                return dataset[:]

            image_timestamps = read("timestamp/image_timestamps", required=True)
            image_timestamps = np.asarray(image_timestamps, dtype=np.float64).reshape(-1)
            if len(image_timestamps) != frame_count:
                raise HDF5SchemaError("timestamp/image_timestamps length does not match images")

            positions = np.asarray(read("state/positions", required=True), dtype=np.float32)
            if positions.ndim != 2 or positions.shape[1] != 3:
                raise HDF5SchemaError("state/positions must have shape (N, 3)")
            state_timestamps = read("timestamp/position_timestamps", required=True)
            state_timestamps = np.asarray(state_timestamps, dtype=np.float64).reshape(-1)
            if len(state_timestamps) != len(positions):
                raise HDF5SchemaError("timestamp/position_timestamps length mismatch")

            for name, times in (("image", image_timestamps), ("state", state_timestamps)):
                if not len(times) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
                    raise HDF5SchemaError(f"{name} timestamps must be finite and strictly increasing")

            raw_indices = np.asarray(read("image_state/state_index", required=True)).reshape(-1)
            if not np.isfinite(raw_indices).all() or np.any(raw_indices != np.floor(raw_indices)):
                raise HDF5SchemaError("state_index must contain finite integer rows")
            state_indices = raw_indices.astype(np.int64)
            if len(state_indices) != frame_count:
                raise HDF5SchemaError("image_state/state_index length mismatch")
            if np.any(state_indices < 0) or np.any(state_indices >= len(positions)):
                raise HDF5SchemaError("image_state/state_index contains an invalid state row")
            # The local producer records image and state from the same snapshot.
            # Refuse stale/future mappings rather than silently using truth at a
            # different instant. Tolerance permits floating-point roundoff only.
            if not np.allclose(state_timestamps[state_indices], image_timestamps, atol=1e-6, rtol=0):
                raise HDF5SchemaError("image/state timestamps must align within 1 microsecond")

            camera_positions_all = read("action/camera_positions")
            if camera_positions_all is None:
                camera_positions_all = np.zeros_like(positions)
            camera_positions_all = np.asarray(camera_positions_all, dtype=np.float32)
            if camera_positions_all.shape != positions.shape:
                raise HDF5SchemaError("action/camera_positions must match state/positions")

            target_positions = positions[state_indices]
            camera_positions = camera_positions_all[state_indices]
            camera_rotations = read("action/camera_rotations")
            camera_yaws = None
            if camera_rotations is not None:
                camera_rotations = np.asarray(camera_rotations, dtype=np.float64)
                if camera_rotations.shape != positions.shape or not np.isfinite(camera_rotations).all():
                    raise HDF5SchemaError("camera_rotations must be finite state-axis (N, 3)")
                # Verified local writer stores roll, yaw, pitch in degrees.
                camera_yaws = camera_rotations[state_indices, 1]
            target_velocities_all = _finite_difference(positions, state_timestamps, np)
            camera_velocities_all = _finite_difference(camera_positions_all, state_timestamps, np)
            target_velocities = target_velocities_all[state_indices]
            camera_velocities = camera_velocities_all[state_indices]

            relative_positions = read("action/camera_relative_positions")
            if relative_positions is not None and np.asarray(relative_positions).shape == (frame_count, 3):
                relative_positions = np.asarray(relative_positions, dtype=np.float32)
            else:
                relative_positions = target_positions - camera_positions

            target_ranges = read("action/camera_target_distance")
            if target_ranges is None or len(np.asarray(target_ranges).reshape(-1)) != frame_count:
                target_ranges = np.linalg.norm(relative_positions, axis=1)
            target_ranges = np.asarray(target_ranges, dtype=np.float32).reshape(-1)

            visible = read("visibility/target_visible")
            visible_observed = visible is not None
            if visible is None:
                visible = np.ones(frame_count, dtype=bool)
            visible = np.asarray(visible, dtype=bool).reshape(-1)
            in_frame = read("visibility/target_in_frame")
            if in_frame is None:
                in_frame = visible.copy()
            in_frame = np.asarray(in_frame, dtype=bool).reshape(-1)
            occluded = read("visibility/target_occluded")
            if occluded is None:
                occluded = np.zeros(frame_count, dtype=bool)
            occluded = np.asarray(occluded, dtype=bool).reshape(-1)
            pixel_height = read("visibility/target_pixel_height")
            if pixel_height is None:
                pixel_height = np.zeros(frame_count, dtype=np.float32)
            pixel_height = np.asarray(pixel_height, dtype=np.float32).reshape(-1)

            actor_ids = read("image_state/actor_ids")
            if actor_ids is None:
                actor_ids = read("state/actor_ids")
            if actor_ids is None:
                actor_ids = np.full(frame_count, -1, dtype=np.int32)
            actor_ids = np.asarray(actor_ids, dtype=np.int64).reshape(-1)

            metadata: Dict[str, Any] = {
                "source_path": str(source),
                "schema": _decode(h5.attrs.get("schema", "unknown")),
                "visibility_observed": bool(visible_observed),
                "image_color_order": _decode(h5.attrs.get("image_color_order", "unknown")),
            }
            for key, value in h5.attrs.items():
                metadata.setdefault(str(key), _decode(value))
            episode_json = metadata.get("episode_metadata_json")
            if isinstance(episode_json, str):
                try:
                    metadata["episode_metadata"] = json.loads(episode_json)
                except json.JSONDecodeError:
                    pass

            images = image_dataset[:] if load_images else None

        lengths = {
            "target_ranges": len(target_ranges),
            "visible": len(visible),
            "in_frame": len(in_frame),
            "occluded": len(occluded),
            "pixel_height": len(pixel_height),
            "actor_ids": len(actor_ids),
        }
        if any(length != frame_count for length in lengths.values()):
            raise HDF5SchemaError(f"Per-frame dataset length mismatch: {lengths}, frame_count={frame_count}")

        return cls(
            path=source,
            frame_count=frame_count,
            image_height=image_height,
            image_width=image_width,
            image_timestamps=image_timestamps,
            state_timestamps=state_timestamps,
            state_indices=state_indices,
            target_positions=target_positions,
            camera_positions=camera_positions,
            target_velocities=target_velocities,
            camera_velocities=camera_velocities,
            target_ranges_m=target_ranges,
            target_visible=visible,
            target_in_frame=in_frame,
            target_occluded=occluded,
            target_pixel_height=pixel_height,
            target_actor_ids=actor_ids,
            metadata=metadata,
            images=images,
            camera_yaws_deg=camera_yaws,
        )

    def _visibility_probability(self, index: int) -> float:
        # Raw vehicle episodes may contain no visibility annotation at all.
        # Keep that uncertainty explicit instead of treating the target as
        # certainly visible merely because a state row exists.
        if not bool(self.metadata.get("visibility_observed", False)):
            return 0.5
        if not bool(self.target_visible[index]):
            return 0.05
        if bool(self.target_occluded[index]):
            return 0.55
        return 1.0 if bool(self.target_in_frame[index]) else 0.35

    def tracking_state(
        self,
        index: int,
        constraints: Optional[MissionConstraints] = None,
    ) -> TrackingState:
        if not 0 <= index < self.frame_count:
            raise IndexError(index)
        target_position = _as_vector(self.target_positions[index])
        camera_position = _as_vector(self.camera_positions[index])
        relative = tuple(target_position[i] - camera_position[i] for i in range(3))
        bearing_deg = math.degrees(math.atan2(relative[1], relative[0]))
        visible = bool(self.target_visible[index])
        in_frame = bool(self.target_in_frame[index])
        occluded = bool(self.target_occluded[index])
        covariance = 0.05 + (0.8 if occluded else 0.0) + (0.95 if not in_frame else 0.0)
        static_clearance = self.metadata.get("minimum_static_clearance_m", 10.0)
        try:
            static_clearance = max(0.0, float(static_clearance))
        except (TypeError, ValueError):
            static_clearance = 10.0
        environment = {
            "source_path": str(self.path),
            "frame_index": index,
            "schema": self.metadata.get("schema", "unknown"),
            "target_actor_id": int(self.target_actor_ids[index]),
            "image_height": self.image_height,
            "image_width": self.image_width,
            "target_pixel_height": float(self.target_pixel_height[index]),
            "visibility_observed": bool(self.metadata.get("visibility_observed", False)),
        }
        target = TargetBelief(
            position_m=target_position,
            velocity_mps=_as_vector(self.target_velocities[index]),
            range_m=max(0.0, float(self.target_ranges_m[index])),
            bearing_deg=bearing_deg,
            image_center_error=0.0 if in_frame else 1.0,
            covariance_trace=covariance,
            visibility_probability=self._visibility_probability(index),
            occlusion_risk=0.9 if occluded else (0.35 if not visible else 0.0),
            identity_confidence=1.0 if int(self.target_actor_ids[index]) >= 0 else 0.3,
            timestamp_s=float(self.image_timestamps[index]),
        )
        uav = UAVState(
            position_m=camera_position,
            velocity_mps=_as_vector(self.camera_velocities[index]),
            yaw_deg=float(self.camera_yaws_deg[index]) if self.camera_yaws_deg is not None else 0.0,
            minimum_obstacle_margin_m=static_clearance,
        )
        return TrackingState(
            target=target,
            uav=uav,
            constraints=constraints or MissionConstraints(),
            mission_goal="Maintain target identity and visual visibility",
            obstacle_context=f"CarlaAir episode; measured static clearance={static_clearance:.2f}m",
            history_summary="",
            environment=environment,
        )

    def iter_tracking_states(
        self,
        stride: int = 1,
        start: int = 0,
        stop: Optional[int] = None,
        constraints: Optional[MissionConstraints] = None,
    ) -> Iterator[TrackingState]:
        if stride <= 0:
            raise ValueError("stride must be positive")
        end = self.frame_count if stop is None else min(stop, self.frame_count)
        for index in range(max(0, start), end, stride):
            yield self.tracking_state(index, constraints=constraints)

    def observed_metrics(self, index: int) -> Dict[str, Any]:
        """Return ground-truth annotations for offline evaluation only."""

        if not 0 <= index < self.frame_count:
            raise IndexError(index)
        return {
            "frame_index": index,
            "timestamp_s": float(self.image_timestamps[index]),
            "target_visible": bool(self.target_visible[index]),
            "target_in_frame": bool(self.target_in_frame[index]),
            "target_occluded": bool(self.target_occluded[index]),
            "target_pixel_height": float(self.target_pixel_height[index]),
            "target_range_m": float(self.target_ranges_m[index]),
        }
