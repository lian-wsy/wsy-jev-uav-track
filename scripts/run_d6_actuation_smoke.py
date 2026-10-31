"""Deterministic offline D6 transport failure matrix; no live clients or keys."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jev_uav_track.actuation import BoundedActuator, FlightTelemetry, StopVerifier
from jev_uav_track.async_control import AsyncDecisionCoordinator, BoundedControlLoop
from jev_uav_track.energy import SimulatedEnergyBudget, SimulatedEnergyConfig
from jev_uav_track.frames import CarlaAirAlignment
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.mock_simulator import BOUNDED_PRIMITIVES, DeterministicMockSession
from jev_uav_track.pipeline import JevTrackPipeline
from run_d6_mock_smoke import SyntheticClock, SyntheticDelayedExecutor, plain


SCENARIOS = ("airborne", "grounded", "expired_before_send", "rpc_rejected", "rpc_overrun",
             "watchdog_before_send", "unsettled_after_neutral")


class SyntheticMovementPort:
    """Immediate synthetic kinematics, not a simulator or physical stopping model.

    Failure injections are declared scenario parameters, never edits to live
    outcomes. A rejected prefix is deliberately applied in this surrogate to
    test neutral handling under an unknown remote outcome.
    """
    def __init__(self, session, clock, scenario):
        self.session, self.clock, self.scenario = session, clock, scenario
        self.calls = []
        self._sends = 0

    def send(self, command):
        self._sends += 1
        record = {"kind": "send", "sent_s": self.clock.now, "command": asdict(command)}
        self.calls.append(record)
        self.session.step(command)
        self.clock.now += command.duration_s
        if self._sends == 1 and self.scenario == "rpc_overrun":
            self.clock.now += .25
        acknowledged = not (self._sends == 1 and self.scenario == "rpc_rejected")
        record.update(acknowledged=acknowledged, completed_s=self.clock.now)
        return acknowledged

    def cancel(self):
        self.calls.append({"kind": "cancel", "completed_s": self.clock.now})


def own_telemetry(session, clock, *, grounded=False, unsettled=False):
    # This entire packet is synthetic. No native sensor timestamps or measured
    # battery/clearance are fabricated or injected into a live client.
    state = session.observe().state.uav
    velocity = (.1, 0., 0.) if unsettled else (state.velocity_mps[0], state.velocity_mps[1], -state.velocity_mps[2])
    return FlightTelemetry(clock.now - .01, clock.now, int(round(clock.now * 1e9)),
                           0 if grounded else 1, velocity, (0., 0., 0.), False, 0)


def run_case(method, scenario, seed):
    clock = SyntheticClock()
    selector_scheduler = SyntheticDelayedExecutor(clock, .05)
    transport_scheduler = SyntheticDelayedExecutor(clock, 0.)
    pipe = JevTrackPipeline(MockJevClient() if method == "mock_jev" else UtilityRankingClient(),
                           primitives=BOUNDED_PRIMITIVES)
    replies = []

    def select(request):
        result = pipe.selector.select(request.state, request.candidates)
        replies.append(plain(result))
        return result

    coordinator = AsyncDecisionCoordinator(select, clock=clock, executor=selector_scheduler)
    energy_config = SimulatedEnergyConfig(20., 90., 120.)
    loop = BoundedControlLoop(pipe, coordinator, alignment=CarlaAirAlignment((0., 0., 0.)),
        clock=clock, simulated_energy=SimulatedEnergyBudget(energy_config, start_s=clock.now),
        execution_domain="synthetic_simulation")
    session = DeterministicMockSession()
    observation = session.reset(seed=seed, scenario="normal")
    port = SyntheticMovementPort(session, clock, scenario)
    actuator = BoundedActuator(lambda: port, clock=clock, executor=transport_scheduler,
                              execution_domain="synthetic_simulation")
    receipt = None
    stop_trace = []
    stop_verdict = "NOT_EVALUATED"
    try:
        initial_tick = loop.tick(observation.state, observation.capture, observation.safety)
        request = plain(coordinator.request_snapshot)
        observation = session.step(None)
        clock.now = observation.safety.timestamp_s
        selector_scheduler.advance()
        result = loop.tick(observation.state, observation.capture, observation.safety)
        if result.command is None:
            raise RuntimeError("SYNTHETIC_PIPELINE_FAILED")
        baseline = own_telemetry(session, clock, grounded=scenario == "grounded")
        submit_code = actuator.submit(result, baseline)
        if submit_code == "SUBMITTED":
            if scenario == "expired_before_send":
                clock.now = result.dispatch_deadline_s
            elif scenario == "watchdog_before_send":
                clock.now += .25
                actuator.poll()  # This latches; later completion cannot clear it.
            transport_scheduler.advance()
            receipt = actuator.poll()
            if receipt is None:
                raise RuntimeError("SYNTHETIC_TRANSACTION_INCOMPLETE")
            if (receipt.status == "PREFIX_AND_NEUTRAL_ACKNOWLEDGED"
                    and not receipt.owner_termination_required):
                verifier = StopVerifier(receipt, baseline)
                for _ in range(3):
                    session.step(None)
                    clock.now += .05
                    sample = own_telemetry(session, clock, unsettled=scenario == "unsettled_after_neutral")
                    stop_verdict = verifier.observe(sample, now_s=clock.now)
                    stop_trace.append({"telemetry": asdict(sample), "verdict": stop_verdict})
        return {"method": method, "scenario": scenario, "seed": seed,
                "initial_tick": asdict(initial_tick), "request": request, "replies": replies,
                "controller_tick": asdict(result), "selection_counts": dict(coordinator.counters),
                "baseline_telemetry": asdict(baseline), "submit_code": submit_code,
                "receipt": asdict(receipt) if receipt else None, "rpc_calls": port.calls,
                "stop_trace": stop_trace, "stop_verdict": stop_verdict,
                "candidate_horizon_s": 1., "max_prefix_s": .05, "energy_config": asdict(energy_config),
                "transport_kind": "SYNTHETIC_IMMEDIATE_KINEMATICS_WITH_DECLARED_FAILURES"}
    finally:
        actuator.close()
        coordinator.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=False, exist_ok=False)
    except OSError:
        print("OUTPUT_NOT_FRESH", file=sys.stderr)
        return 2
    try:
        runs = [run_case(method, scenario, args.seed) for method in ("utility", "mock_jev") for scenario in SCENARIOS]
        report = {"evidence_class": "SYNTHETIC_NOT_CARLA", "case_count": len(runs), "runs": runs,
                  "live_motion": "NOT_EVALUATED", "physical_braking": "NOT_EVALUATED",
                  "tracking_improvement": "NOT_EVALUATED", "sustained_20hz": "NOT_EVALUATED",
                  "limits": ["SYNTHETIC_TIME_AND_TELEMETRY", "SYNTHETIC_SAFETY_AND_ENERGY",
                             "SYNTHETIC_NEUTRAL_KINEMATICS_NOT_PHYSICS", "TRANSACTIONAL_SMOKE_NOT_TRACKING",
                             "NO_AIRSIM_CLIENT_OR_JEV_NETWORK", "NO_TRAINING_OR_TOWN03_DATA"]}
        with (args.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except Exception:
        print("ACTUATION_SMOKE_FAILED", file=sys.stderr)
        return 1
    print("SYNTHETIC_ACTUATION_SMOKE_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
