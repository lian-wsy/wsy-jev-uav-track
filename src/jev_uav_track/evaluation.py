"""Offline Jev-style evaluation over recorded CarlaAir states."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .data.hdf5_adapter import HDF5Episode
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
    selector = JevTrackSelector(decision_client, config=SelectionConfig())

    records: list[Dict[str, Any]] = []
    previous_action_id: Optional[str] = None
    skipped = 0
    for frame_index in _sample_indices(episode.frame_count, cfg.max_frames, cfg.stride):
        state = episode.tracking_state(frame_index, constraints=constraints)
        candidates = rollout.generate(DEFAULT_PRIMITIVES, state)
        safe_actions, rejected_ids = filter_safe_actions(candidates, state.uav, constraints)
        if not safe_actions:
            skipped += 1
            continue
        result = selector.select(state, candidates, previous_action_id=previous_action_id)
        oracle = max(safe_actions, key=lambda item: item.outcome.utility(constraints))
        selected = result.action
        observed = episode.observed_metrics(frame_index)
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
        mean = lambda key: sum(float(record[key]) for record in records) / len(records)
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
        }
    else:
        report_metrics = {
            "evaluated_frames": 0,
            "skipped_frames": skipped,
            "fallback_rate": None,
            "safe_action_rate": 0.0,
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
