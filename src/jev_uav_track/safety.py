"""Hard safety checks and deterministic fallback ranking."""

from __future__ import annotations

from typing import Iterable, List, Tuple

from .models import CandidateAction, MissionConstraints, UAVState


def safety_reasons(action: CandidateAction, uav: UAVState, constraints: MissionConstraints) -> Tuple[str, ...]:
    reasons = []
    outcome = action.outcome
    if not outcome.feasible:
        reasons.append("infeasible_dynamics_or_duration")
    if outcome.collision_probability > constraints.maximum_collision_probability:
        reasons.append("collision_probability")
    if outcome.minimum_obstacle_margin_m < constraints.minimum_obstacle_margin_m:
        reasons.append("obstacle_margin")
    if action.duration_s > constraints.maximum_action_duration_s:
        reasons.append("action_duration")
    if uav.battery_percent - outcome.energy_cost_wh < constraints.reserve_battery_percent:
        reasons.append("battery_reserve")
    return tuple(reasons)


def filter_safe_actions(
    actions: Iterable[CandidateAction], uav: UAVState, constraints: MissionConstraints
) -> Tuple[List[CandidateAction], Tuple[str, ...]]:
    safe: List[CandidateAction] = []
    rejected: List[str] = []
    for action in actions:
        if safety_reasons(action, uav, constraints):
            rejected.append(action.id)
        else:
            safe.append(action)
    return safe, tuple(rejected)
