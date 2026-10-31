"""Closed-loop replay evaluation for the Jev-Track prototype.

The replay keeps the recorded target trajectory and visibility annotations
read-only, while updating a separate kinematic UAV state from the selected
high-level action.  It is therefore useful for comparing selectors on exactly
the same event window, but it is not a CARLA, AirSim, or flight-dynamics
simulation.  The observation model is explicitly a proxy: recorded
visibility/occlusion labels are combined with a geometric range/FOV check.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Mapping, Sequence

from .history import attach_history, build_target_belief_history
from .energy import remaining_battery_percent
from .causal_belief import CausalTargetEstimator
from .jev_client import JevClient, MockJevClient
from .learned_rollout import LearnedOutcomeRolloutModel
from .models import MissionConstraints, TrackingState, UAVState
from .primitives import DEFAULT_PRIMITIVES
from .rollout import KinematicRolloutModel
from .selector import JevTrackSelector, SelectionConfig
from .training import LinearOutcomeModel


@dataclass(frozen=True)
class ClosedLoopReplayConfig:
    """Fixed-window settings for a deterministic kinematic replay."""

    start_frame: int = 0
    end_frame: int | None = None
    stride: int = 8
    max_decisions: int = 64
    history_window_size: int = 8
    desired_range_m: float = 20.0
    minimum_obstacle_margin_m: float = 3.0
    maximum_collision_probability: float = 0.05
    fov_half_angle_deg: float = 75.0
    max_visible_range_m: float = 60.0
    near_miss_margin_m: float = 0.5
    near_miss_collision_probability_fraction: float = 0.5
    initial_battery_percent: float = 100.0
    simulated_battery_capacity_wh: float | None = None
    enable_safety_gate: bool = True
    use_short_history: bool = True
    use_identity_confidence: bool = True
    identity_neutral_confidence: float = 0.5
    max_learned_feature_zscore: float | None = None
    max_learned_state_feature_zscore: float | None = None
    learned_prediction_blend: float = 1.0

    def __post_init__(self) -> None:
        if self.start_frame < 0:
            raise ValueError("start_frame cannot be negative")
        if self.end_frame is not None and self.end_frame < 0:
            raise ValueError("end_frame cannot be negative")
        if self.stride <= 0 or self.max_decisions <= 0:
            raise ValueError("stride and max_decisions must be positive")
        if self.history_window_size <= 0:
            raise ValueError("history_window_size must be positive")
        if self.fov_half_angle_deg <= 0.0 or self.fov_half_angle_deg > 180.0:
            raise ValueError("fov_half_angle_deg must be in (0, 180]")
        if self.max_visible_range_m <= 0.0:
            raise ValueError("max_visible_range_m must be positive")
        if self.near_miss_margin_m < 0.0:
            raise ValueError("near_miss_margin_m cannot be negative")
        if not 0.0 <= self.near_miss_collision_probability_fraction <= 1.0:
            raise ValueError("near_miss_collision_probability_fraction must be in [0, 1]")
        if not 0.0 <= self.initial_battery_percent <= 100.0:
            raise ValueError("initial_battery_percent must be in [0, 100]")
        if self.simulated_battery_capacity_wh is not None:
            remaining_battery_percent(self.initial_battery_percent, 0., self.simulated_battery_capacity_wh)
        if (
            self.max_learned_feature_zscore is not None
            and self.max_learned_feature_zscore <= 0.0
        ):
            raise ValueError("max_learned_feature_zscore must be positive when set")
        if (
            self.max_learned_state_feature_zscore is not None
            and self.max_learned_state_feature_zscore <= 0.0
        ):
            raise ValueError("max_learned_state_feature_zscore must be positive when set")
        if not 0.0 <= self.learned_prediction_blend <= 1.0:
            raise ValueError("learned_prediction_blend must be in [0, 1]")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in vector))


def _angle_delta_deg(target_deg: float, reference_deg: float) -> float:
    """Return the signed shortest angle from reference to target."""

    return (float(target_deg) - float(reference_deg) + 180.0) % 360.0 - 180.0


def _sample_indices(
    frame_count: int,
    start_frame: int,
    end_frame: int | None,
    stride: int,
    max_decisions: int,
) -> list[int]:
    stop = frame_count if end_frame is None else min(frame_count, end_frame)
    indices = list(range(min(start_frame, stop), stop, stride))
    if len(indices) <= max_decisions:
        return indices
    if max_decisions == 1:
        return [indices[0]]
    positions = [
        round(position * (len(indices) - 1) / (max_decisions - 1))
        for position in range(max_decisions)
    ]
    return [indices[position] for position in positions]


class _IdentityTracker:
    """A deliberately conservative sticky identity tracker for replay.

    Ground-truth actor ids are used only for metric annotation.  The simulated
    tracker receives an id when the proxy observation is visible and keeps its
    last id through an occlusion.  At a visible crossing it requires two
    consecutive observations of a new id before confirming a switch.  This
    makes identity retention measurable without leaking an unseen actor id
    into the decision state or switching on one ambiguous frame.
    """

    def __init__(self, switch_confirmation_steps: int = 2) -> None:
        if switch_confirmation_steps <= 0:
            raise ValueError("switch_confirmation_steps must be positive")
        self.tracked_id: int | None = None
        self.confidence = 0.5
        self.lost_steps = 0
        self.switch_events = 0
        self.confirmed_switch_events = 0
        self.switch_confirmation_steps = int(switch_confirmation_steps)
        self._pending_id: int | None = None
        self._pending_steps = 0

    def update(self, observed_id: int | None, visible: bool) -> tuple[int | None, float]:
        if visible and observed_id is not None and observed_id >= 0:
            if self.tracked_id is None:
                self.tracked_id = observed_id
                self.confidence = 1.0
            elif observed_id == self.tracked_id:
                self._pending_id = None
                self._pending_steps = 0
                self.confidence = min(1.0, self.confidence + 0.12)
            else:
                # Keep the previous identity until the new id is repeated;
                # this is intentionally conservative at a crossing.
                self.switch_events += 1
                self.confidence = min(self.confidence, 0.25)
                if self._pending_id == observed_id:
                    self._pending_steps += 1
                else:
                    self._pending_id = observed_id
                    self._pending_steps = 1
                if self._pending_steps >= self.switch_confirmation_steps:
                    self.tracked_id = observed_id
                    self.confirmed_switch_events += 1
                    self.confidence = 0.60
                    self._pending_id = None
                    self._pending_steps = 0
            self.lost_steps = 0
        else:
            self.lost_steps += 1
            self.confidence = max(0.25, self.confidence - 0.10)
            self._pending_id = None
            self._pending_steps = 0
        return self.tracked_id, self.confidence


def _proxy_observation(
    episode: Any,
    frame_index: int,
    sim_uav: UAVState,
    config: ClosedLoopReplayConfig,
) -> Dict[str, Any]:
    """Build a non-RGB observation from annotations and simulated geometry."""

    base = episode.tracking_state(frame_index)
    relative = tuple(
        float(base.target.position_m[axis]) - float(sim_uav.position_m[axis])
        for axis in range(3)
    )
    range_m = _norm(relative)
    world_bearing = math.degrees(math.atan2(relative[1], relative[0]))
    relative_bearing = _angle_delta_deg(world_bearing, sim_uav.yaw_deg)
    geometry_in_frame = (
        abs(relative_bearing) <= config.fov_half_angle_deg
        and range_m <= config.max_visible_range_m
    )
    annotated_visible = bool(episode.target_visible[frame_index])
    annotated_in_frame = bool(episode.target_in_frame[frame_index])
    annotated_occluded = bool(episode.target_occluded[frame_index])
    proxy_visible = (
        annotated_visible
        and annotated_in_frame
        and not annotated_occluded
        and geometry_in_frame
    )
    if proxy_visible:
        visibility_probability = 1.0
    elif annotated_occluded:
        visibility_probability = 0.10
    elif not annotated_visible or not annotated_in_frame:
        visibility_probability = 0.05
    else:
        visibility_probability = 0.20
    occlusion_risk = 0.9 if annotated_occluded else (0.6 if not proxy_visible else 0.0)
    covariance = 0.05
    covariance += 0.80 if annotated_occluded else 0.0
    covariance += 0.95 if not geometry_in_frame or not annotated_in_frame else 0.0
    return {
        "base": base,
        "range_m": range_m,
        "world_bearing_deg": world_bearing,
        "relative_bearing_deg": relative_bearing,
        "geometry_in_frame": geometry_in_frame,
        "annotated_visible": annotated_visible,
        "annotated_in_frame": annotated_in_frame,
        "annotated_occluded": annotated_occluded,
        "proxy_visible": proxy_visible,
        "visibility_probability": visibility_probability,
        "occlusion_risk": _clamp(occlusion_risk, 0.0, 1.0),
        "covariance_trace": covariance,
    }


def _make_state(
    episode: Any,
    frame_index: int,
    sim_uav: UAVState,
    identity_id: int | None,
    identity_confidence: float,
    observation: Mapping[str, Any],
    constraints: MissionConstraints,
    config: ClosedLoopReplayConfig,
    estimator: CausalTargetEstimator,
) -> TrackingState:
    base = observation["base"]
    visible = bool(observation["proxy_visible"])
    target = estimator.update(
        measurement=base.target.position_m if visible else None,
        # Velocity belongs to the visible measurement's identity, even while
        # the sticky identity tracker is still confirming an association.
        actor_id=int(episode.target_actor_ids[frame_index]) if visible else None,
        uav=sim_uav,
        desired_range_m=constraints.desired_range_m,
        fov_half_angle_deg=config.fov_half_angle_deg,
        identity_confidence=(
            identity_confidence
            if config.use_identity_confidence
            else config.identity_neutral_confidence
        ),
        timestamp_s=float(episode.image_timestamps[frame_index]),
    )
    environment = {
        "frame_index": frame_index,
        "target_actor_id": identity_id,
        "proxy_visible": visible,
        "observation_source": "visible_oracle_position+causal_extrapolation",
        "command_frame": "world_z_up",
    }
    return TrackingState(
        target=target,
        uav=sim_uav,
        constraints=constraints,
        mission_goal="Maintain target identity and visual visibility in closed-loop replay",
        obstacle_context=(
            "Kinematic replay proxy; initial-state margin (not a live sensor)="
            f"{sim_uav.minimum_obstacle_margin_m:.2f}m"
        ),
        environment=environment,
    )


def _advance_uav(uav: UAVState, action: Any, dt_s: float) -> UAVState:
    dt_s = float(dt_s)
    if not math.isfinite(dt_s) or dt_s <= 0:
        raise ValueError("Replay step must be finite and positive")
    if not math.isfinite(action.duration_s) or action.duration_s <= 0:
        raise ValueError("Action duration must be finite and positive")
    # Zero-order command hold expires at its declared duration. The remaining
    # interval is an ideal zero-velocity hold (proxy only, no braking dynamics).
    executed_s = min(dt_s, float(action.duration_s))
    position = tuple(
        float(uav.position_m[axis]) + float(action.command_mps[axis]) * executed_s
        for axis in range(3)
    )
    battery = remaining_battery_percent(
        uav.battery_percent, float(action.outcome.energy_cost_wh) * executed_s / action.duration_s,
        uav.battery_capacity_wh)
    yaw = (float(uav.yaw_deg) + float(action.yaw_rate_dps) * executed_s + 180.0) % 360.0 - 180.0
    return replace(
        uav,
        position_m=position,
        velocity_mps=action.command_mps if dt_s <= action.duration_s else (0.0, 0.0, 0.0),
        yaw_deg=yaw,
        battery_percent=battery,
    )


def _mean(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values if math.isfinite(float(value))]
    return sum(values) / len(values) if values else None


def run_closed_loop_replay(
    episode: Any,
    client: JevClient | None = None,
    config: ClosedLoopReplayConfig | None = None,
    outcome_model: LinearOutcomeModel | None = None,
    model_name: str | None = None,
) -> Dict[str, Any]:
    """Run one client on a fixed annotated episode window.

    The returned metrics are selector/replay metrics.  ``identity_coverage``
    is explicitly marked ``NOT_AUDITED`` for a single-actor episode, because a
    perfect sticky id through occlusion does not establish crossing robustness.
    """

    cfg = config or ClosedLoopReplayConfig()
    if str(episode.metadata.get("validation_status", "")).upper() != "PASS":
        raise ValueError("Closed-loop replay requires validation_status=PASS")
    indices = _sample_indices(
        episode.frame_count,
        cfg.start_frame,
        cfg.end_frame,
        cfg.stride,
        cfg.max_decisions,
    )
    if not indices:
        raise ValueError("The replay window contains no frames")
    constraints = MissionConstraints(
        desired_range_m=cfg.desired_range_m,
        minimum_obstacle_margin_m=cfg.minimum_obstacle_margin_m,
        maximum_collision_probability=cfg.maximum_collision_probability,
    )
    first = episode.tracking_state(indices[0], constraints=constraints)
    capacity = cfg.simulated_battery_capacity_wh
    if capacity is None and first.uav.energy_source == "SIMULATED_RESOURCE":
        capacity = first.uav.battery_capacity_wh
    if capacity is None:
        raise ValueError("SIMULATED_CAPACITY_REQUIRED: replay needs an explicit synthetic capacity in Wh")
    # Own recorded camera orientation only; never align to hidden target truth.
    sim_uav = replace(
        first.uav,
        yaw_deg=first.uav.yaw_deg,
        battery_percent=cfg.initial_battery_percent,
        battery_capacity_wh=capacity,
        energy_source="SIMULATED_RESOURCE",
        # Do not replace a causal initial state with the full-episode minimum.
        # HDF adapters without timestamped clearance fail closed at zero.
        minimum_obstacle_margin_m=first.uav.minimum_obstacle_margin_m,
    )
    decision_client = client or MockJevClient()
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
    baseline = KinematicRolloutModel()
    rollout: Any = baseline
    if outcome_model is not None:
        rollout = LearnedOutcomeRolloutModel(
            outcome_model,
            max_feature_zscore=cfg.max_learned_feature_zscore,
            max_state_feature_zscore=cfg.max_learned_state_feature_zscore,
            prediction_blend=cfg.learned_prediction_blend,
        )

    identity = _IdentityTracker()
    estimator = CausalTargetEstimator()
    history_states: list[TrackingState] = []
    records: list[Dict[str, Any]] = []
    previous_action_id: str | None = None
    loss_start_s: float | None = None
    reacquisition_times: list[float] = []
    unresolved_loss_events = 0
    identity_correct = 0
    identity_observed_frames = 0
    # Coverage status is based on the whole episode, not only the sampled
    # decision frames.  Retention metrics below still use the fixed replay
    # window so the three clients remain comparable.
    identity_ground_truth_ids: set[int] = {
        int(value) for value in episode.target_actor_ids if int(value) >= 0
    }
    selected_unsafe_count = 0
    near_miss_count = 0
    hard_rejection_count = 0
    client_error_count = 0
    learned_model_ood_count = 0
    energy_cost_wh = 0.0

    for position, frame_index in enumerate(indices):
        observation = _proxy_observation(episode, frame_index, sim_uav, cfg)
        ground_truth_id = int(episode.target_actor_ids[frame_index])
        if ground_truth_id >= 0:
            identity_ground_truth_ids.add(ground_truth_id)
        tracked_id, identity_confidence = identity.update(
            ground_truth_id if ground_truth_id >= 0 and observation["proxy_visible"] else None,
            bool(observation["proxy_visible"]),
        )
        state = _make_state(
            episode,
            frame_index,
            sim_uav,
            tracked_id,
            identity_confidence,
            observation,
            constraints,
            cfg,
            estimator,
        )
        history: Any = None
        if cfg.use_short_history:
            history_states.append(state)
            history = build_target_belief_history(
                history_states,
                max_length=cfg.history_window_size,
            )
            state = attach_history(state, history)
        else:
            history_states.append(state)
        candidates = rollout.generate(DEFAULT_PRIMITIVES, state)
        result = selector.select(state, candidates, previous_action_id)
        selected = result.action
        selected_unsafe = selected.id in result.rejected_action_ids
        selected_near_miss = (
            selected.outcome.minimum_obstacle_margin_m
            < constraints.minimum_obstacle_margin_m + cfg.near_miss_margin_m
            or selected.outcome.collision_probability
            >= (
                cfg.near_miss_collision_probability_fraction
                * constraints.maximum_collision_probability
            )
        )
        selected_unsafe_count += int(selected_unsafe)
        near_miss_count += int(selected_near_miss)
        hard_rejection_count += len(result.rejected_action_ids)
        energy_cost_wh += float(selected.outcome.energy_cost_wh)
        learned_model_ood_count += int(selected.metadata.get("learned_model_ood", False))
        if result.reason.startswith("jev_error:"):
            client_error_count += 1

        timestamp_s = float(episode.image_timestamps[frame_index])
        proxy_visible = bool(observation["proxy_visible"])
        if not proxy_visible and loss_start_s is None:
            loss_start_s = timestamp_s
        elif proxy_visible and loss_start_s is not None:
            reacquisition_times.append(max(0.0, timestamp_s - loss_start_s))
            loss_start_s = None

        if ground_truth_id >= 0:
            identity_observed_frames += 1
            identity_correct += int(tracked_id == ground_truth_id)

        records.append(
            {
                "frame_index": frame_index,
                "timestamp_s": timestamp_s,
                "choice": selected.id,
                "rollout_model": selected.metadata.get("model"),
                "learned_model_ood": bool(selected.metadata.get("learned_model_ood", False)),
                "learned_model_ood_reason": selected.metadata.get("learned_model_ood_reason"),
                "max_feature_zscore": selected.metadata.get("max_feature_zscore"),
                "state_feature_zscore": selected.metadata.get("state_feature_zscore"),
                "prediction_blend": selected.metadata.get("prediction_blend"),
                "fallback": bool(result.used_fallback),
                "reason": result.reason,
                "model": result.decision.model_version,
                "confidence": result.decision.confidence,
                "top_probability": result.decision.top_probability,
                "probability_margin": result.decision.margin,
                "proxy_visible": proxy_visible,
                "annotated_visible": bool(observation["annotated_visible"]),
                "annotated_in_frame": bool(observation["annotated_in_frame"]),
                "annotated_occluded": bool(observation["annotated_occluded"]),
                "geometry_in_frame": bool(observation["geometry_in_frame"]),
                "range_m": float(observation["range_m"]),
                "belief_position_m": list(state.target.position_m),
                "belief_velocity_mps": list(state.target.velocity_mps),
                "belief_covariance_trace": state.target.covariance_trace,
                "relative_bearing_deg": float(observation["relative_bearing_deg"]),
                "tracked_actor_id": tracked_id,
                "ground_truth_actor_id": ground_truth_id,
                "identity_confidence": identity_confidence,
                "selected_unsafe": selected_unsafe,
                "selected_near_miss": selected_near_miss,
                "selected_collision_probability": selected.outcome.collision_probability,
                "selected_obstacle_margin_m": selected.outcome.minimum_obstacle_margin_m,
                "rejected_action_ids": list(result.rejected_action_ids),
                "history_length": history.length if history is not None else 0,
                "history_occlusion_fraction": (
                    history.occlusion_fraction if history is not None else None
                ),
                "history_identity_consistency": (
                    history.identity_consistency if history is not None else None
                ),
                "history_target_acceleration_mps2": (
                    history.target_acceleration_mps2 if history is not None else None
                ),
                "sim_uav_position_m": list(sim_uav.position_m),
                "sim_uav_yaw_deg": sim_uav.yaw_deg,
                "sim_uav_battery_percent": sim_uav.battery_percent,
                "energy_cost_wh": selected.outcome.energy_cost_wh,
            }
        )
        previous_action_id = selected.id
        if position + 1 < len(indices):
            next_timestamp = float(episode.image_timestamps[indices[position + 1]])
            sim_uav = _advance_uav(sim_uav, selected, next_timestamp - timestamp_s)

    if loss_start_s is not None:
        unresolved_loss_events = 1
    unique_ids = sorted(identity_ground_truth_ids)
    crossing_audited = len(unique_ids) > 1
    metrics: Dict[str, Any] = {
        "decision_count": len(records),
        "proxy_visible_fraction": _mean(float(record["proxy_visible"]) for record in records),
        "annotated_visible_fraction": _mean(
            float(record["annotated_visible"]) for record in records
        ),
        "annotated_occluded_fraction": _mean(
            float(record["annotated_occluded"]) for record in records
        ),
        "reacquisition_count": len(reacquisition_times),
        "mean_reacquisition_time_s": _mean(reacquisition_times),
        "max_reacquisition_time_s": max(reacquisition_times, default=None),
        "unresolved_loss_events": unresolved_loss_events,
        "identity_retention_rate": (
            identity_correct / identity_observed_frames if identity_observed_frames else None
        ),
        "identity_correct_frames": identity_correct,
        "identity_observed_frames": identity_observed_frames,
        "identity_switch_events": identity.switch_events,
        "identity_confirmed_switch_events": identity.confirmed_switch_events,
        "selected_unsafe_count": selected_unsafe_count,
        "near_miss_count": near_miss_count,
        "near_miss_rate": near_miss_count / len(records) if records else None,
        "safety_event_count": selected_unsafe_count + near_miss_count,
        "safety_event_rate": (
            (selected_unsafe_count + near_miss_count) / len(records)
            if records
            else None
        ),
        "selected_unsafe_rate": selected_unsafe_count / len(records) if records else None,
        "learned_model_ood_count": learned_model_ood_count,
        "learned_model_ood_rate": (
            learned_model_ood_count / len(records) if records else None
        ),
        "learned_model_ood_reason_counts": dict(
            Counter(
                str(record["learned_model_ood_reason"])
                for record in records
                if record.get("learned_model_ood_reason")
            )
        ),
        "hard_rejection_count": hard_rejection_count,
        "fallback_rate": (
            sum(bool(record["fallback"]) for record in records) / len(records)
            if records
            else None
        ),
        "client_error_count": client_error_count,
        "energy_cost_wh": energy_cost_wh,
    }
    return {
        "episode": str(episode.path),
        "schema": episode.metadata.get("schema", "unknown"),
        "validation_status": episode.metadata.get("validation_status", "unreported"),
        "evaluation_mode": "kinematic_replay_proxy",
        "observation_source": "visible_oracle_position+causal_extrapolation",
        "belief_protocol_version": 2,
        "execution_protocol": "command_until_expiry_then_ideal_zero_velocity_hold",
        "identity_coverage": {
            "status": "AUDITED" if crossing_audited else "NOT_AUDITED",
            "reason": (
                "Multiple actor ids are present in this PASS episode."
                if crossing_audited
                else "This PASS episode contains one actor id; identity crossing is not covered."
            ),
            "ground_truth_actor_ids": unique_ids,
        },
        "model_name": model_name,
        "config": cfg.__dict__,
        "energy_contract": {"source": "SIMULATED_RESOURCE", "capacity_wh": capacity,
                            "model_id": "legacy_action_heuristic_no_idle"},
        "metrics": metrics,
        "choice_counts": dict(Counter(record["choice"] for record in records)),
        "records": records,
        "reacquisition_times_s": reacquisition_times,
        "note": (
            "The replay updates a separate kinematic UAV state and uses recorded target "
            "annotations plus a geometric FOV/range proxy. Safety events are predicted "
            "selector events, not proof of physical collision safety."
        ),
    }


def compare_closed_loop_reports(
    reports: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compare choices and requested metrics for named replay clients."""

    comparisons: Dict[str, Any] = {}
    metric_names = (
        "mean_reacquisition_time_s",
        "identity_retention_rate",
        "safety_event_count",
        "selected_unsafe_count",
        "near_miss_count",
    )
    for left_name, right_name in itertools.combinations(reports, 2):
        left = reports[left_name]
        right = reports[right_name]
        left_records = {int(item["frame_index"]): item for item in left.get("records", [])}
        right_records = {int(item["frame_index"]): item for item in right.get("records", [])}
        shared = sorted(set(left_records) & set(right_records))
        changed = sum(
            left_records[index]["choice"] != right_records[index]["choice"]
            for index in shared
        )
        left_metrics = left.get("metrics", {})
        right_metrics = right.get("metrics", {})
        metric_delta: Dict[str, float | None] = {}
        for metric in metric_names:
            left_value = left_metrics.get(metric)
            right_value = right_metrics.get(metric)
            if left_value is None or right_value is None:
                metric_delta[metric] = None
            else:
                metric_delta[metric] = float(right_value) - float(left_value)
        comparisons[f"{left_name}_vs_{right_name}"] = {
            "left": left_name,
            "right": right_name,
            "shared_frames": len(shared),
            "choice_changed_count": changed,
            "choice_agreement_rate": 1.0 - changed / len(shared) if shared else None,
            "left_metrics": {metric: left_metrics.get(metric) for metric in metric_names},
            "right_metrics": {metric: right_metrics.get(metric) for metric in metric_names},
            "right_minus_left": metric_delta,
        }
    return comparisons


def aggregate_closed_loop_reports(
    reports: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate per-episode replay reports without hiding coverage gaps.

    Rates are weighted by the number of decision frames.  Reacquisition time is
    computed from the concatenated completed events, so an episode with no
    completed reacquisition does not silently contribute a zero-second event.
    """

    if not reports:
        raise ValueError("At least one closed-loop report is required")

    total_decisions = sum(
        int(report.get("metrics", {}).get("decision_count", 0)) for report in reports
    )
    total_identity_frames = sum(
        int(report.get("metrics", {}).get("identity_observed_frames", 0))
        for report in reports
    )
    total_identity_correct = sum(
        int(report.get("metrics", {}).get("identity_correct_frames", 0))
        for report in reports
    )
    reacquisition_times = [
        float(value)
        for report in reports
        for value in report.get("reacquisition_times_s", [])
    ]

    def _weighted_mean(name: str) -> float | None:
        values: list[tuple[float, int]] = []
        for report in reports:
            value = report.get("metrics", {}).get(name)
            count = int(report.get("metrics", {}).get("decision_count", 0))
            if value is not None and count > 0:
                values.append((float(value), count))
        denominator = sum(count for _, count in values)
        return sum(value * count for value, count in values) / denominator if denominator else None

    def _episode_statistics(name: str) -> Dict[str, Any]:
        """Describe between-episode variation without treating frames as iid.

        The aggregate rates above are frame-weighted for operational reporting.
        This companion summary gives each episode one vote, which exposes whether
        a result is driven by a single long recording.  The interval is a
        descriptive normal approximation over episode-level values; it is not a
        claim of a population confidence interval for the data collection.
        """

        values = [
            float(report.get("metrics", {}).get(name))
            for report in reports
            if report.get("metrics", {}).get(name) is not None
            and math.isfinite(float(report.get("metrics", {}).get(name)))
        ]
        if not values:
            return {"n": 0, "mean": None, "std": None, "ci95": None, "min": None, "max": None}
        mean = sum(values) / len(values)
        if len(values) < 2:
            std = 0.0
            ci95 = [mean, mean]
        else:
            variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
            std = math.sqrt(variance)
            half_width = 1.96 * std / math.sqrt(len(values))
            ci95 = [mean - half_width, mean + half_width]
            if name in {
                "proxy_visible_fraction",
                "annotated_visible_fraction",
                "annotated_occluded_fraction",
                "identity_retention_rate",
                "near_miss_rate",
                "safety_event_rate",
                "fallback_rate",
                "learned_model_ood_rate",
            }:
                ci95 = [_clamp(ci95[0], 0.0, 1.0), _clamp(ci95[1], 0.0, 1.0)]
        return {
            "n": len(values),
            "mean": mean,
            "std": std,
            "ci95": ci95,
            "min": min(values),
            "max": max(values),
            "method": "normal_approximation_over_episode_values",
        }

    identity_statuses = sorted({
        str(report.get("identity_coverage", {}).get("status", "UNKNOWN"))
        for report in reports
    })
    learned_ood_reason_counts: Counter[str] = Counter()
    for report in reports:
        learned_ood_reason_counts.update(
            report.get("metrics", {}).get("learned_model_ood_reason_counts", {})
        )
    episode_metric_names = (
        "proxy_visible_fraction",
        "annotated_visible_fraction",
        "annotated_occluded_fraction",
        "mean_reacquisition_time_s",
        "identity_retention_rate",
        "near_miss_rate",
        "safety_event_rate",
        "fallback_rate",
        "learned_model_ood_rate",
        "energy_cost_wh",
    )
    return {
        "episode_count": len(reports),
        "decision_count": total_decisions,
        "identity_coverage_statuses": identity_statuses,
        "episode_statistics": {
            name: _episode_statistics(name) for name in episode_metric_names
        },
        "metrics": {
            "proxy_visible_fraction": _weighted_mean("proxy_visible_fraction"),
            "annotated_visible_fraction": _weighted_mean("annotated_visible_fraction"),
            "annotated_occluded_fraction": _weighted_mean("annotated_occluded_fraction"),
            "reacquisition_count": len(reacquisition_times),
            "mean_reacquisition_time_s": (
                sum(reacquisition_times) / len(reacquisition_times)
                if reacquisition_times
                else None
            ),
            "max_reacquisition_time_s": max(reacquisition_times, default=None),
            "identity_retention_rate": (
                total_identity_correct / total_identity_frames
                if total_identity_frames
                else None
            ),
            "identity_observed_frames": total_identity_frames,
            "identity_confirmed_switch_events": sum(
                int(report.get("metrics", {}).get("identity_confirmed_switch_events", 0))
                for report in reports
            ),
            "selected_unsafe_count": sum(
                int(report.get("metrics", {}).get("selected_unsafe_count", 0))
                for report in reports
            ),
            "near_miss_count": sum(
                int(report.get("metrics", {}).get("near_miss_count", 0))
                for report in reports
            ),
            "near_miss_rate": (
                sum(int(report.get("metrics", {}).get("near_miss_count", 0)) for report in reports)
                / total_decisions
                if total_decisions
                else None
            ),
            "safety_event_count": sum(
                int(report.get("metrics", {}).get("safety_event_count", 0))
                for report in reports
            ),
            "safety_event_rate": (
                sum(int(report.get("metrics", {}).get("safety_event_count", 0)) for report in reports)
                / total_decisions
                if total_decisions
                else None
            ),
            "learned_model_ood_count": sum(
                int(report.get("metrics", {}).get("learned_model_ood_count", 0))
                for report in reports
            ),
            "learned_model_ood_rate": (
                sum(
                    int(report.get("metrics", {}).get("learned_model_ood_count", 0))
                    for report in reports
                )
                / total_decisions
                if total_decisions
                else None
            ),
            "learned_model_ood_reason_counts": dict(learned_ood_reason_counts),
            "hard_rejection_count": sum(
                int(report.get("metrics", {}).get("hard_rejection_count", 0))
                for report in reports
            ),
            "fallback_rate": _weighted_mean("fallback_rate"),
            "client_error_count": sum(
                int(report.get("metrics", {}).get("client_error_count", 0))
                for report in reports
            ),
            "energy_cost_wh": sum(
                float(report.get("metrics", {}).get("energy_cost_wh", 0.0))
                for report in reports
            ),
        },
        "episodes": [
            {
                "episode": report.get("episode"),
                "validation_status": report.get("validation_status"),
                "identity_coverage": report.get("identity_coverage"),
                "metrics": report.get("metrics", {}),
                "choice_counts": report.get("choice_counts", {}),
            }
            for report in reports
        ],
    }
