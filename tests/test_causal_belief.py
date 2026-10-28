from dataclasses import replace
from types import SimpleNamespace
import pytest
from jev_uav_track.causal_belief import CausalTargetEstimator
from jev_uav_track.closed_loop import ClosedLoopReplayConfig, run_closed_loop_replay
from jev_uav_track.jev_client import UtilityRankingClient
from jev_uav_track.models import UAVState
from test_closed_loop import FakeEpisode


def test_replay_command_expires_before_next_decision():
    from jev_uav_track.closed_loop import _advance_uav
    command = SimpleNamespace(command_mps=(2, 0, 0), duration_s=1, yaw_rate_dps=10,
                              outcome=SimpleNamespace(energy_cost_wh=1))
    uav = _advance_uav(UAVState(), command, 3)
    assert uav.position_m == (2, 0, 0)
    assert uav.velocity_mps == (0, 0, 0)
    assert uav.yaw_deg == 10
    with pytest.raises(ValueError, match="finite and positive"):
        _advance_uav(UAVState(), command, float("nan"))


def test_extrapolate_visible_history_and_reset_velocity_for_new_identity():
    estimator = CausalTargetEstimator()
    def update(t, p, actor=7):
        return estimator.update(timestamp_s=t, measurement=p, actor_id=actor,
            uav=UAVState(), desired_range_m=20, fov_half_angle_deg=75, identity_confidence=1)
    assert update(0, (10, 0, 0)).velocity_mps == (0, 0, 0)
    assert update(1, (12, 0, 0)).velocity_mps == (2, 0, 0)
    hidden = update(3, None)
    assert hidden.position_m == (16, 0, 0)
    assert hidden.covariance_trace > 4
    assert update(4, (100, 0, 0), 8).velocity_mps == (0, 0, 0)


class SpyUtility(UtilityRankingClient):
    def __init__(self):
        self.states = []
    def choose(self, state, actions):
        self.states.append(state.to_dict())
        return super().choose(state, actions)


@pytest.mark.parametrize("initially_hidden", [False, True])
def test_hidden_truth_perturbation_cannot_change_selector_input(initially_hidden):
    class PoisonedEpisode(FakeEpisode):
        def tracking_state(self, index, constraints=None):
            state = super().tracking_state(index, constraints)
            target = replace(state.target, velocity_mps=(999, -999, 500))
            if not self.target_visible[index]:
                target = replace(target, position_m=(-500, 123, 88), range_m=900, bearing_deg=179)
            return replace(state, target=target, environment={"secret_future": "must_not_leak"})
    a, b = FakeEpisode(), PoisonedEpisode()
    if initially_hidden:
        a.target_visible = b.target_visible = [False, False, False, True, True, True]
        a.target_occluded = b.target_occluded = [True, True, True, False, False, False]
    b.target_actor_ids = [7 if visible else 999 for visible in b.target_visible]
    left, right = SpyUtility(), SpyUtility()
    cfg = ClosedLoopReplayConfig(stride=1, max_decisions=6, minimum_obstacle_margin_m=0)
    first = run_closed_loop_replay(a, client=left, config=cfg)
    second = run_closed_loop_replay(b, client=right, config=cfg)
    assert left.states == right.states
    assert [r["choice"] for r in first["records"]] == [r["choice"] for r in second["records"]]
    assert all("ground_truth_actor_id" not in s["environment"] for s in left.states)


def test_new_visible_identity_does_not_create_spurious_velocity():
    class SwitchedEpisode(FakeEpisode):
        target_visible = [True] * 6
        target_occluded = [False] * 6
        target_actor_ids = [7, 7, 8, 8, 8, 8]

        def tracking_state(self, index, constraints=None):
            state = super().tracking_state(index, constraints)
            if index >= 2:
                state = replace(state, target=replace(state.target, position_m=(40 + index, 0, 0)))
            return state

    client = SpyUtility()
    run_closed_loop_replay(SwitchedEpisode(), client=client,
        config=ClosedLoopReplayConfig(stride=1, max_decisions=6, minimum_obstacle_margin_m=0))
    assert client.states[2]["environment"]["target_actor_id"] == 7
    assert tuple(client.states[2]["target"]["velocity_mps"]) == (0, 0, 0)
    assert tuple(client.states[3]["target"]["velocity_mps"]) == (1, 0, 0)
