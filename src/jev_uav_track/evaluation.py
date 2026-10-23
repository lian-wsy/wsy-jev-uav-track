"""Offline Jev-style evaluation over recorded CarlaAir states."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .data.hdf5_adapter import HDF5Episode
from .history import attach_history, build_episode_belief_history
from .jev_client import JevClient, MockJevClient
from .learned_rollout import LearnedOutcomeRolloutModel
from .models import CandidateAction, MissionConstraints
from .primitives import DEFAULT_PRIMITIVES
from .rollout import KinematicRolloutModel
from .safety import filter_safe_actions
from .selector import JevTrackSelector, SelectionConfig
from .training import LinearOutcomeModel


@dataclass(frozen=True)
class OfflineEvaluationConfig:
    max_frames: int = 64
    stride: int = 5
    desired_range_m: float = 20.0
    minimum_obstacle_margin_m: float = 0.0
    maximum_collision_probability: float = 0.05
    mock_temperature: float = 0.35
    enable_safety_gate: bool = True
    use_short_history: bool = True
    history_window_size: int = 8
    use_identity_confidence: bool = True
    identity_neutral_confidence: float = 0.5


def _sample_indices(frame_count: int, max_frames: int, stride: int) -> list[int]:
    if max_frames <= 0 or stride <= 0:
        raise ValueError("max_frames and stride must be positive")
    indices = list(range(0, frame_count, stride))
    if len(indices) <= max_frames:
        return indices
    if max_frames == 1:
        return [indices[0]]
    positions = [round(index * (len(indices) - 1) / (max_frames - 1)) for index in range(max_frames)]
    return [indices[position] for position in positions]


def evaluate_episode(
    episode: HDF5Episode,
    config: OfflineEvaluationConfig | None = None,
    client: JevClient | None = None,
    outcome_model: LinearOutcomeModel | None = None,
) -> Dict[str, Any]:
    """Evaluate selector behavior without sending frames or state to Jev.

    The default ``MockJevClient`` is a deterministic Jev-shaped baseline. The
    report labels actual annotations separately from predicted outcomes because
    a recorded episode cannot provide counterfactual results for every action.
    The module flags make safety gating, short history, and identity confidence
    explicit experimental factors without changing the recorded source files.
    """

    cfg = config or OfflineEvaluationConfig()
    constraints = MissionConstraints(
        desired_range_m=cfg.desired_range_m,
        minimum_obstacle_margin_m=cfg.minimum_obstacle_margin_m,
        maximum_collision_probability=cfg.maximum_collision_probability,
    )
    baseline = KinematicRolloutModel()
    rollout: Any = baseline
    if outcome_model is not None:
        rollout = LearnedOutcomeRolloutModel(outcome_model, baseline=baseline)
    decision_client = client or MockJevClient(temperature=cfg.mock_temperature)
    selection_config = SelectionConfig()
    if not cfg.use_short_history:
        selection_config = SelectionConfig(
            hysteresis_margin=0.0,
            maximum_utility_drop_for_hold=0.0,
        )
    selector = JevTrackSelector(
        decision_client,
        config=selection_config,
        enable_safety_gate=cfg.enable_safety_gate,
    )

    records: list[Dict[str, Any]] = []
    previous_action_id: Optional[str] = None
    skipped = 0
    for frame_index in _sample_indices(episode.frame_count, cfg.max_frames, cfg.stride):
        state = episode.tracking_state(frame_index, constraints=constraints)
        history = None
        if cfg.use_short_history:
            history = build_episode_belief_history(
                episode,
                frame_index,
                window_size=cfg.history_window_size,
                constraints=constraints,
            )
            state = attach_history(state, history)
        if not cfg.use_identity_confidence:
            state = replace(
                state,
                target=replace(
                    state.target,
                    identity_confidence=cfg.identity_neutral_confidence,
                ),
            )
            if history is not None:
                state = attach_history(
                    state,
                    history.with_identity_confidence(cfg.identity_neutral_confidence),
                )
        candidates = rollout.generate(DEFAULT_PRIMITIVES, state)
        constraint_safe_actions, constraint_rejected_ids = filter_safe_actions(
            candidates,
            state.uav,
            constraints,
        )
        if cfg.enable_safety_gate:
            safe_actions, rejected_ids = constraint_safe_actions, constraint_rejected_ids
        else:
            safe_actions, rejected_ids = list(candidates), ()
        if not safe_actions:
            skipped += 1
            continue
        result = selector.select(
            state,
            candidates,
            previous_action_id=(previous_action_id if cfg.use_short_history else None),
        )
        oracle = max(safe_actions, key=lambda item: item.outcome.utility(constraints))
        selected = result.action
        observed = episode.observed_metrics(frame_index)
        selected_margin = float(selected.outcome.minimum_obstacle_margin_m)
        selected_margin_value: float | None = (
            selected_margin if math.isfinite(selected_margin) else None
        )
        records.append(
            {
                **observed,
                "choice": selected.id,
                "fallback": result.used_fallback,
                "reason": result.reason,
                "model": result.decision.model_version,
                "confidence": result.decision.confidence,
                "top_probability": result.decision.top_probability,
                "probability_margin": result.decision.margin,
                "predicted_visibility": selected.outcome.visibility_probability,
                "predicted_range_error_m": selected.outcome.range_error_m,
                "selected_utility": selected.outcome.utility(constraints),
                "identity_confidence": state.target.identity_confidence,
                "history_enabled": cfg.use_short_history,
                "history_length": history.length if history is not None else 0,
                "history_duration_s": history.duration_s if history is not None else 0.0,
                "history_identity_consistency": (
                    history.identity_consistency if history is not None else None
                ),
                "history_occlusion_fraction": (
                    history.occlusion_fraction if history is not None else None
                ),
                "history_target_acceleration_mps2": (
                    history.target_acceleration_mps2 if history is not None else None
                ),
                "safety_gate_enabled": cfg.enable_safety_gate,
                "selected_unsafe": selected.id in constraint_rejected_ids,
                "selected_collision_probability": selected.outcome.collision_probability,
                "selected_obstacle_margin_m": selected_margin_value,
                "oracle_action": oracle.id,
                "oracle_utility": oracle.outcome.utility(constraints),
                "utility_regret": oracle.outcome.utility(constraints)
                - selected.outcome.utility(constraints),
                "rejected_action_ids": list(rejected_ids),
            }
        )
        previous_action_id = selected.id

    choice_counts = Counter(record["choice"] for record in records)
    if records:
        def mean(key: str) -> float | None:
            values = [
                float(record[key])
                for record in records
                if record.get(key) is not None and math.isfinite(float(record[key]))
            ]
            return sum(values) / len(values) if values else None
        fallback_rate = sum(bool(record["fallback"]) for record in records) / len(records)
        report_metrics = {
            "evaluated_frames": len(records),
            "skipped_frames": skipped,
            "fallback_rate": fallback_rate,
            "safe_action_rate": len(records) / (len(records) + skipped),
            "mean_selected_utility": mean("selected_utility"),
            "mean_oracle_regret": mean("utility_regret"),
            "mean_predicted_visibility": mean("predicted_visibility"),
            "mean_actual_visibility": sum(bool(record["target_visible"]) for record in records) / len(records),
            "mean_actual_range_m": mean("target_range_m"),
            "mean_predicted_range_error_m": mean("predicted_range_error_m"),
            "unsafe_selected_rate": sum(bool(record["selected_unsafe"]) for record in records)
            / len(records),
            "mean_identity_confidence": mean("identity_confidence"),
        }
    else:
        report_metrics = {
            "evaluated_frames": 0,
            "skipped_frames": skipped,
            "fallback_rate": None,
            "safe_action_rate": 0.0,
            "unsafe_selected_rate": None,
            "mean_identity_confidence": None,
        }
    return {
        "episode": str(episode.path),
        "schema": episode.metadata.get("schema", "unknown"),
        "validation_status": episode.metadata.get("validation_status", "unreported"),
        "visibility_observed": bool(episode.metadata.get("visibility_observed", False)),
        "backend": getattr(decision_client, "__class__", type(decision_client)).__name__,
        "rollout_model": "linear-outcome-calibrator-v0" if outcome_model else "kinematic-rollout-v0",
        "config": cfg.__dict__,
        "metrics": report_metrics,
        "choice_counts": dict(choice_counts),
        "records": records,
    }


def compare_jev_reports(
    mock_report: Mapping[str, Any],
    real_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare Mock Jev and a real Jev report produced on the same frames."""

    mock_records = {
        int(record["frame_index"]): record
        for record in mock_report.get("records", [])
    }
    real_records = {
        int(record["frame_index"]): record
        for record in real_report.get("records", [])
    }
    shared_indices = sorted(set(mock_records) & set(real_records))
    frame_comparison = []
    changed = 0
    for frame_index in shared_indices:
        mock_record = mock_records[frame_index]
        real_record = real_records[frame_index]
        choice_changed = mock_record["choice"] != real_record["choice"]
        changed += int(choice_changed)
        frame_comparison.append(
            {
                "frame_index": frame_index,
                "mock_choice": mock_record["choice"],
                "real_choice": real_record["choice"],
                "choice_changed": choice_changed,
                "mock_fallback": bool(mock_record["fallback"]),
                "real_fallback": bool(real_record["fallback"]),
                "real_reason": real_record["reason"],
                "real_model": real_record["model"],
                "real_confidence": real_record["confidence"],
                "real_top_probability": real_record["top_probability"],
            }
        )
    return {
        "mock_backend": mock_report.get("backend"),
        "real_backend": real_report.get("backend"),
        "shared_frames": len(shared_indices),
        "choice_changed_count": changed,
        "choice_agreement_rate": (
            1.0 - changed / len(shared_indices) if shared_indices else None
        ),
        "mock_metrics": mock_report.get("metrics", {}),
        "real_metrics": real_report.get("metrics", {}),
        "frame_comparison": frame_comparison,
    }
