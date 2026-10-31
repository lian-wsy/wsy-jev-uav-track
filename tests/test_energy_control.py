"""Approved simulation energy cannot replace geometry or renew authorization."""
from dataclasses import replace

import pytest

from jev_uav_track.async_control import AsyncDecisionCoordinator, BoundedControlLoop, MeasuredSafety
from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
from jev_uav_track.frames import CarlaAirAlignment
from test_async_control import Clock, ControlledExecutor, packet, pipeline, setup_loop, state


def simulation_loop(config=None, domain="synthetic_simulation"):
    clock, executor, pipe = Clock(), ControlledExecutor(), pipeline()
    budget = SimulatedEnergyBudget(config or SimulatedEnergyConfig(20., 90., 120.), start_s=1.)
    coordinator = AsyncDecisionCoordinator(lambda r: pipe.selector.select(r.state, r.candidates),
                                           clock=clock, executor=executor)
    loop = BoundedControlLoop(pipe, coordinator, alignment=CarlaAirAlignment((0., 0., 0.)),
                              clock=clock, simulated_energy=budget, execution_domain=domain)
    return clock, executor, coordinator, loop, budget


def step(loop, clock, **updates):
    # No native battery telemetry. Geometry/collision still arrive independently.
    safety = replace(MeasuredSafety(clock.now, True, None, 10., False), **updates)
    return loop.tick(state(), packet(clock.now), safety)


@pytest.mark.parametrize("domain", [None, "real_flight", "unknown", "simulation"])
def test_simulated_energy_requires_explicit_allowed_domain(domain):
    with pytest.raises(ValueError, match="SIMULATION_ONLY"):
        simulation_loop(domain=domain)


@pytest.mark.parametrize("domain", ["synthetic_simulation", "isolated_simulation"])
def test_budget_is_in_choice_snapshot_and_bounded_dispatch_with_no_measured_battery(domain):
    clock, executor, coordinator, loop, budget = simulation_loop(domain=domain)
    first = step(loop, clock)
    request = coordinator.request_snapshot
    assert first.command is None
    assert request.state.uav.energy_source == "SIMULATED_RESOURCE"
    assert request.state.uav.battery_capacity_wh == 20.
    assert request.state.uav.battery_percent == 90.
    assert request.state.environment["energy_budget"]["power_w"] == 120.
    assert request.candidates[0].outcome.energy_cost_wh == pytest.approx(1. / 30.)
    assert request.candidates[0].metadata["energy_source"] == "SIMULATED_RESOURCE"
    clock.now = 1.05
    executor.complete()
    result = step(loop, clock)
    assert result.command.duration_s == .05
    assert result.energy.source == "SIMULATED_RESOURCE"
    assert result.energy.remaining_wh == pytest.approx(17.998333333333335)


@pytest.mark.parametrize("updates,reason", [({"valid": False}, "SAFETY_UNMEASURED"),
    ({"clearance_m": None}, "SAFETY_UNMEASURED"), ({"collision": None}, "SAFETY_UNMEASURED"),
    ({"collision": True}, "COLLISION"), ({"clearance_m": 1.}, "CLEARANCE"),
    ({"timestamp_s": .8}, "SAFETY_EXPIRED"), ({"timestamp_s": 2.}, "SAFETY_EXPIRED"),
    ({"clearance_m": float("nan")}, "SAFETY_NONFINITE")])
def test_budget_never_fills_or_bypasses_geometry_and_freshness(updates, reason):
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    assert step(loop, clock).command
    clock.now = 1.1
    result = step(loop, clock, **updates)
    assert result.command is None
    assert result.reason_code == reason
    assert result.energy.remaining_wh < 18.


def test_budget_waiting_consumes_energy_and_controller_reset_does_not_refill():
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)  # executor remains blocked; no command has run
    clock.now = 31.
    loop.reset()
    result = step(loop, clock)
    assert result.command is None
    assert result.energy.remaining_wh == pytest.approx(17.)
    assert result.energy.battery_percent == pytest.approx(85.)
    clock.now = 421.
    assert step(loop, clock).reason_code == "BATTERY_RESERVE"


def test_reset_does_not_allow_energy_clock_to_rewind():
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)
    clock.now = 2.
    step(loop, clock)
    loop.reset()
    clock.now = 1.
    assert step(loop, clock).reason_code == "ENERGY_BUDGET_INVALID"
    clock.now = 3.
    assert step(loop, clock).reason_code == "ENERGY_BUDGET_INVALID"


def test_energy_is_rechecked_after_costly_compilation(monkeypatch):
    from jev_uav_track import async_control
    clock, executor, coordinator, loop, budget = simulation_loop(SimulatedEnergyConfig(.1, 21.07, 3.6))
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    original = async_control.compile_carlaair_candidate

    def slow_compile(action, alignment):
        command = original(action, alignment)
        clock.now = 1.10
        return command

    monkeypatch.setattr(async_control, "compile_carlaair_candidate", slow_compile)
    result = step(loop, clock)
    assert result.command is None
    assert result.reason_code == "CURRENT_ACTION_UNSAFE"


def test_energy_deadline_is_preserved_for_a_delayed_transport():
    # .00107 Wh above reserve at t=1, .001 Wh reserved for the 1 s
    # action at .001 Wh/s: the final send must precede t=1.07.
    clock, executor, coordinator, loop, budget = simulation_loop(SimulatedEnergyConfig(.1, 21.07, 3.6))
    step(loop, clock)
    executor.complete()
    clock.now = 1.05
    result = step(loop, clock)
    assert result.command is not None
    assert result.dispatch_deadline_s == pytest.approx(1.07)


def test_explicit_budget_rejects_conflicting_battery_measurement():
    clock, executor, coordinator, loop, budget = simulation_loop()
    assert step(loop, clock, battery_percent=100.).reason_code == "ENERGY_SOURCE_CONFLICT"
    assert coordinator.counters["requests"] == 0


@pytest.mark.parametrize("updates", [{"battery_capacity_wh": None}, {"energy_source": "UNKNOWN"},
                                      {"energy_source": "SIMULATED_RESOURCE"}])
def test_no_simulation_opt_in_cannot_implicitly_replace_missing_native_energy(updates):
    clock, executor, coordinator, loop, measured = setup_loop()
    result = loop.tick(state(), packet(clock.now), replace(measured, **updates))
    assert result.command is None
    assert result.reason_code == "ENERGY_UNAVAILABLE"
    assert coordinator.counters["requests"] == 0


@pytest.mark.parametrize("stage", ["snapshot", "safety"])
@pytest.mark.parametrize("start,finished,reason,remaining", [
    (1.999, 2.019, "ACTION_EXPIRED", None),
    (1.25, 1.45, "DISPATCH_REJECTED", None),
    (1.95, 1.98, "BOUNDED_PREFIX", .02),
])
def test_budget_checks_own_cost_cannot_cross_final_dispatch_deadlines(monkeypatch, stage, start, finished, reason, remaining):
    from jev_uav_track import async_control
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    assert step(loop, clock).command
    clock.now = start
    target, name = (budget, "snapshot") if stage == "snapshot" else (async_control, "safety_reasons")
    original, calls = getattr(target, name), 0

    def slow_check(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:  # tick-start check, then dispatch check
            clock.now = finished
        return result

    monkeypatch.setattr(target, name, slow_check)
    result = step(loop, clock)
    assert result.reason_code == reason
    if remaining is None:
        assert result.command is None
    else:
        assert result.command.duration_s == pytest.approx(remaining)
        assert finished + result.command.duration_s <= 2.


def test_budget_checks_own_cost_cannot_cross_energy_reserve(monkeypatch):
    clock, executor, coordinator, loop, budget = simulation_loop(SimulatedEnergyConfig(.1, 21.17, 3.6))
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    assert step(loop, clock).command
    clock.now = 1.1
    original, calls = budget.snapshot, 0

    def slow_snapshot(now_s):
        nonlocal calls
        result = original(now_s)
        calls += 1
        if calls == 2:
            clock.now = 1.2
        return result

    monkeypatch.setattr(budget, "snapshot", slow_snapshot)
    result = step(loop, clock)
    assert result.command is None
    assert result.reason_code == "CURRENT_ACTION_UNSAFE"


@pytest.mark.parametrize("reset", [False, True])
def test_final_observed_clock_is_a_persistent_lower_bound(monkeypatch, reset):
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    assert step(loop, clock).command
    clock.now = 1.1
    original, calls = budget.snapshot, 0

    def delayed_snapshot(now_s):
        nonlocal calls
        result = original(now_s)
        calls += 1
        if calls == 2:
            clock.now = 1.2
        return result

    monkeypatch.setattr(budget, "snapshot", delayed_snapshot)
    assert step(loop, clock).command
    if reset:
        loop.reset()
    clock.now = 1.16  # greater than tick-start, but less than final dispatch time
    result = step(loop, clock)
    assert result.command is None
    assert result.reason_code == "ENERGY_BUDGET_INVALID"
    clock.now = 1.3
    loop.reset()
    assert step(loop, clock).reason_code == "ENERGY_BUDGET_INVALID"


def test_control_cadence_uses_final_dispatch_time_after_slow_budget_check(monkeypatch):
    clock, executor, coordinator, loop, budget = simulation_loop()
    step(loop, clock)
    clock.now = 1.05
    executor.complete()
    assert step(loop, clock).command
    clock.now = 1.1
    original, calls = budget.snapshot, 0

    def delayed_snapshot(now_s):
        nonlocal calls
        result = original(now_s)
        calls += 1
        if calls == 2:
            clock.now = 1.2
        return result

    monkeypatch.setattr(budget, "snapshot", delayed_snapshot)
    assert step(loop, clock).command
    clock.now = 1.23  # only 30ms since actual dispatch, not a new 50ms slot
    assert step(loop, clock).reason_code == "TICK_TOO_SOON"
