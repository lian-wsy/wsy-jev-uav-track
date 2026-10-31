"""Energy contract: percent and Wh cannot be subtracted without capacity."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from jev_uav_track.models import CandidateAction, CandidateOutcome, MissionConstraints, UAVState
from jev_uav_track.safety import safety_reasons


def energy_action(energy_wh):
    return CandidateAction("unit", "unit", "offline units only", 1., (0., 0., 0.), 0.,
                           CandidateOutcome(1., 0., 0., 1., 0., 10., energy_wh, 0.))


def energy_state(capacity_wh, source="SIMULATED_RESOURCE", percent=20.005):
    # Complete UAV fixture with the proposed capacity/source contract. The real
    # safety function is exercised; no selector or conversion helper is mocked.
    fields = asdict(UAVState(battery_percent=percent))
    fields.update(battery_capacity_wh=capacity_wh, energy_source=source)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("capacity,energy,rejected", [(20., .002, True), (200., .006, False)])
def test_reserve_uses_capacity_conversion_not_wh_as_percentage_points(capacity, energy, rejected):
    # Hand calculated remaining SOC: 19.995% and 20.002%, respectively.
    reasons = safety_reasons(energy_action(energy), energy_state(capacity), MissionConstraints())
    assert ("battery_reserve" in reasons) is rejected


@pytest.mark.parametrize("capacity", [None, 0., -1., float("nan"), float("inf"), True])
def test_unknown_or_invalid_capacity_never_authorizes_an_action(capacity):
    assert "energy_unavailable" in safety_reasons(
        energy_action(.002), energy_state(capacity), MissionConstraints())


def test_unknown_energy_source_never_authorizes_an_action():
    assert "energy_unavailable" in safety_reasons(
        energy_action(.002), energy_state(20., "UNKNOWN"), MissionConstraints())


@pytest.mark.parametrize("energy", [-.01, float("nan"), float("inf"), True])
def test_bad_candidate_energy_cannot_create_free_reserve(energy):
    assert "energy_invalid" in safety_reasons(
        energy_action(energy), energy_state(20.), MissionConstraints())


def test_state_serialization_preserves_energy_provenance_without_inventing_capacity():
    from jev_uav_track.models import TargetBelief, TrackingState
    unknown = UAVState()
    assert getattr(unknown, "energy_source", None) == "UNKNOWN"
    assert unknown.battery_capacity_wh is None
    assert "energy_unavailable" in safety_reasons(energy_action(0.), unknown, MissionConstraints())
    state = TrackingState(TargetBelief(), UAVState(battery_capacity_wh=20., energy_source="SIMULATED_RESOURCE"))
    assert state.to_dict()["uav"]["energy_source"] == "SIMULATED_RESOURCE"
    assert state.to_dict()["uav"]["battery_capacity_wh"] == 20.


def test_resource_budget_consumes_elapsed_time_without_commands_or_double_charging():
    from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
    budget = SimulatedEnergyBudget(SimulatedEnergyConfig(20., 90., 120.), start_s=10.)
    first = budget.snapshot(10.)
    assert first.remaining_wh == 18.
    after = budget.snapshot(40.)
    assert after.remaining_wh == pytest.approx(17.)
    assert after.battery_percent == pytest.approx(85.)
    assert after.source == "SIMULATED_RESOURCE"
    assert budget.snapshot(40.) == after
    assert budget.forecast_wh(3.) == pytest.approx(.1)
    assert budget.snapshot(40.) == after  # forecasting does not consume energy
    assert budget.snapshot(10000.).remaining_wh == 0.


@pytest.mark.parametrize("bad_time", [9., float("nan"), float("inf"), True])
def test_resource_clock_error_latches_fail_closed(bad_time):
    from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
    budget = SimulatedEnergyBudget(SimulatedEnergyConfig(20., 90., 120.), start_s=10.)
    with pytest.raises(ValueError, match="ENERGY_CLOCK"):
        budget.snapshot(bad_time)
    with pytest.raises(ValueError, match="ENERGY_CLOCK"):
        budget.snapshot(11.)


@pytest.mark.parametrize("values", [(0., 90., 120.), (20., 101., 120.), (20., 90., 0.),
                                   (20., 90., -1.), (20., 90., float("nan")), (True, 90., 120.),
                                   (20., 90., 5e-324)])
def test_resource_config_rejects_invalid_or_free_hover_budget(values):
    from jev_uav_track.energy import SimulatedEnergyConfig
    with pytest.raises(ValueError):
        SimulatedEnergyConfig(*values)


@pytest.mark.parametrize("duration", [0., -1., float("inf"), True])
def test_resource_forecast_rejects_invalid_duration(duration):
    from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
    budget = SimulatedEnergyBudget(SimulatedEnergyConfig(20., 90., 120.), start_s=0.)
    with pytest.raises(ValueError):
        budget.forecast_wh(duration)


def test_positive_duration_cost_underflow_fails_closed():
    from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
    budget = SimulatedEnergyBudget(SimulatedEnergyConfig(20., 90., 120.), start_s=0.)
    with pytest.raises(ValueError, match="ENERGY_COST_INVALID"):
        budget.forecast_wh(5e-324)


def test_proxy_replay_energy_uses_capacity_not_percentage_points():
    from jev_uav_track.closed_loop import _advance_uav
    initial = UAVState(battery_percent=90., battery_capacity_wh=20., energy_source="SIMULATED_RESOURCE")
    # Half of a 1 s, 1 Wh action consumes .5 Wh = 2.5 percentage points.
    advanced = _advance_uav(initial, energy_action(1.), .5)
    assert advanced.battery_percent == pytest.approx(87.5)


def test_demo_labels_capacity_and_converts_its_action_energy(monkeypatch, capsys):
    from jev_uav_track import demo
    original = demo.JevTrackPipeline.step
    states, actions = [], []

    def record(self, state, **kwargs):
        states.append(state)
        result = original(self, state, **kwargs)
        actions.append(result.action)
        return result

    monkeypatch.setattr(demo.JevTrackPipeline, "step", record)
    demo.run_demo(2)
    assert states[0].uav.energy_source == "SIMULATED_RESOURCE"
    assert states[0].uav.battery_capacity_wh == 20.
    assert states[1].uav.battery_percent == pytest.approx(96. - 5. * actions[0].outcome.energy_cost_wh)
    assert "SIMULATED_RESOURCE" in capsys.readouterr().out


def test_replay_requires_explicit_simulated_capacity_and_labels_legacy_cost():
    from dataclasses import replace
    from jev_uav_track.closed_loop import ClosedLoopReplayConfig, run_closed_loop_replay
    from test_closed_loop import FakeEpisode
    class NoEnergyEpisode(FakeEpisode):
        def tracking_state(self, index, constraints=None):
            state = super().tracking_state(index, constraints)
            return replace(state, uav=replace(state.uav, battery_capacity_wh=None, energy_source="UNKNOWN"))
    with pytest.raises(ValueError, match="SIMULATED_CAPACITY_REQUIRED"):
        run_closed_loop_replay(NoEnergyEpisode())
    report = run_closed_loop_replay(NoEnergyEpisode(), config=ClosedLoopReplayConfig(
        simulated_battery_capacity_wh=20., minimum_obstacle_margin_m=0.))
    assert report["energy_contract"]["source"] == "SIMULATED_RESOURCE"
    assert report["energy_contract"]["model_id"] == "legacy_action_heuristic_no_idle"
    assert report["energy_contract"]["capacity_wh"] == 20.
