"""Run a small offline closed-loop demonstration."""

from __future__ import annotations

import math
from dataclasses import replace

from .jev_client import MockJevClient
from .energy import remaining_battery_percent
from .models import MissionConstraints, TargetBelief, TrackingState, UAVState
from .pipeline import JevTrackPipeline


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def make_demo_state() -> TrackingState:
    return TrackingState(
        target=TargetBelief(
            position_m=(18.0, 2.0, 0.0),
            velocity_mps=(1.1, 0.25, 0.0),
            range_m=18.1,
            bearing_deg=6.0,
            image_center_error=0.35,
            covariance_trace=1.6,
            visibility_probability=0.72,
            occlusion_risk=0.2,
            identity_confidence=0.9,
        ),
        uav=UAVState(
            position_m=(0.0, 0.0, 0.0),
            velocity_mps=(0.0, 0.0, 0.0),
            battery_percent=96.0,
            battery_capacity_wh=20.0,
            energy_source="SIMULATED_RESOURCE",
            minimum_obstacle_margin_m=12.0,
        ),
        constraints=MissionConstraints(
            desired_range_m=20.0,
            reserve_battery_percent=20.0,
            minimum_obstacle_margin_m=3.0,
            maximum_collision_probability=0.05,
            maximum_speed_mps=8.0,
            maximum_action_duration_s=2.0,
        ),
        mission_goal="Keep the target identifiable and visible while preserving a safe margin",
        obstacle_context="Open test area; obstacle map is unavailable in this demo",
    )


def run_demo(iterations: int = 6) -> None:
    state = make_demo_state()
    pipeline = JevTrackPipeline(MockJevClient())
    previous_action_id: str | None = None

    print("Jev-Track offline demonstration: SIMULATED_RESOURCE, example capacity=20Wh; heuristic action cost only")
    for step in range(iterations):
        result = pipeline.step(state, previous_action_id=previous_action_id)
        action = result.action
        print(
            f"step={step:02d} choice={action.id:<18} "
            f"p={result.decision.top_probability:.2f} "
            f"confidence={result.decision.confidence:.2f} "
            f"visibility={action.outcome.visibility_probability:.2f} "
            f"reason={result.reason}"
        )

        terminal = action.trajectory[-1]
        target_position = tuple(
            state.target.position_m[index]
            + state.target.velocity_mps[index] * action.duration_s
            for index in range(3)
        )
        next_range = _distance(target_position, terminal.position_m)
        state = replace(
            state,
            target=replace(
                state.target,
                position_m=target_position,
                range_m=next_range,
                image_center_error=action.outcome.image_center_error,
                covariance_trace=action.outcome.covariance_trace,
                visibility_probability=action.outcome.visibility_probability,
                occlusion_risk=max(0.0, 1.0 - action.outcome.visibility_probability),
                timestamp_s=state.target.timestamp_s + action.duration_s,
            ),
            uav=replace(
                state.uav,
                position_m=terminal.position_m,
                velocity_mps=terminal.velocity_mps,
                battery_percent=remaining_battery_percent(state.uav.battery_percent,
                    action.outcome.energy_cost_wh, state.uav.battery_capacity_wh),
            ),
        )
        previous_action_id = action.id


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()

