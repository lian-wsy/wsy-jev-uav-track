"""Offline D6 evidence with real selector logic and synthetic request timing."""
from __future__ import annotations

import argparse
from concurrent.futures import Future
from dataclasses import asdict, fields, is_dataclass
import json
from pathlib import Path
import sys
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jev_uav_track.async_control import AsyncDecisionCoordinator, BoundedControlLoop
from jev_uav_track.frames import CarlaAirAlignment
from jev_uav_track.jev_client import MockJevClient, UtilityRankingClient
from jev_uav_track.mock_simulator import BOUNDED_PRIMITIVES, DeterministicMockSession
from jev_uav_track.pipeline import JevTrackPipeline


def plain(value):
    """Serialize immutable snapshot mappings without weakening their contract."""
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


class SyntheticClock:
    def __init__(self):
        self.now = 1.

    def __call__(self):
        return self.now


class SyntheticDelayedExecutor:
    """Controlled Future completion; this is not a thread/latency benchmark.

    Real pipeline and client selection execute on the explicit due tick. The
    Event regression separately verifies the actual daemon worker's behavior.
    """
    def __init__(self, clock, delay_s):
        self.clock, self.delay_s, self.pending = clock, delay_s, None

    def submit(self, fn):
        if self.pending is not None:
            raise RuntimeError("SYNTHETIC_SLOT_OCCUPIED")
        future = Future()
        self.pending = (self.clock.now + self.delay_s, future, fn)
        return future

    def advance(self):
        if self.pending is not None and self.clock.now >= self.pending[0] - 1e-9:
            _, future, fn = self.pending
            self.pending = None
            future.set_result(fn())


def run_case(method, scenario, seed, ticks):
    clock = SyntheticClock()
    scheduler = SyntheticDelayedExecutor(clock, {"normal": .05, "delayed": .15, "timeout": .25}[scenario])
    client = MockJevClient() if method == "mock_jev" else UtilityRankingClient()
    pipeline = JevTrackPipeline(client, primitives=BOUNDED_PRIMITIVES)
    requests, responses = [], []

    def select(request):
        result = pipeline.selector.select(request.state, request.candidates)
        responses.append({"request_id": request.request_id, "finished_s": clock.now, "result": plain(result)})
        return result

    coordinator = AsyncDecisionCoordinator(select, clock=clock, executor=scheduler)
    loop = BoundedControlLoop(pipeline, coordinator, alignment=CarlaAirAlignment((0., 0., 0.)), clock=clock)
    session = DeterministicMockSession()
    observation = session.reset(seed=seed, scenario=scenario)
    initial = observation
    trace = []
    commands = []
    observed_after_step = []
    try:
        for _ in range(ticks):
            clock.now = observation.safety.timestamp_s
            scheduler.advance()
            result = loop.tick(observation.state, observation.capture, observation.safety)
            request = coordinator.request_snapshot
            if request is not None and (not requests or requests[-1]["request_id"] != request.request_id):
                requests.append(plain(request))
            trace.append({"observation": asdict(observation), "tick": asdict(result),
                          "coordinator_code": coordinator.last_code, "counts": dict(coordinator.counters),
                          "in_flight": coordinator.in_flight})
            commands.append(result.command)
            observation = session.step(result.command)
            observed_after_step.append(observation)
        reset = session.reset(seed=seed, scenario=scenario)
        repeated_trace = [session.step(command) for command in commands]
        # Full deterministic replay under identical seed and exact emitted actions.
        reproducible = reset == initial and repeated_trace == observed_after_step
        return {"method": method, "scenario": scenario, "seed": seed,
                "client_class": type(client).__name__, "controller": "BoundedControlLoop",
                "candidate_horizon_s": 1., "high_level_period_s": 1., "low_level_period_s": .05,
                "primitive_commands": [asdict(primitive) for primitive in BOUNDED_PRIMITIVES],
                "counts": {**coordinator.counters, "commands": loop.command_count},
                "in_flight_at_close": coordinator.in_flight,
                "reset_reproducible": reproducible, "requests": requests, "responses": responses,
                "trace": trace, "final_observation": asdict(observation)}
    finally:
        coordinator.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="Fresh exclusive directory; existing paths are rejected")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--ticks", type=int, default=45)
    args = parser.parse_args(argv)
    if not 21 <= args.ticks <= 10000:
        parser.error("TICKS_OUT_OF_RANGE")
    try:
        args.output_dir.mkdir(parents=False, exist_ok=False)
    except OSError:
        print("OUTPUT_NOT_FRESH", file=sys.stderr)
        return 2
    try:
        runs = [run_case(method, scenario, args.seed, args.ticks)
                for method in ("utility", "mock_jev") for scenario in ("normal", "delayed", "timeout")]
        report = {"evidence_class": "SYNTHETIC_NOT_CARLA", "timing_model": "SYNTHETIC_CONTROLLED_EXECUTOR",
                  "live_control": "NOT_EVALUATED", "d1_data": "NOT_EVALUATED",
                  "tracking_improvement": "NOT_EVALUATED", "learned_winner_promoted": False,
                  "reset_reproducible": all(run["reset_reproducible"] for run in runs), "runs": runs,
                  "limits": ["SYNTHETIC_KINEMATICS", "SYNTHETIC_SAFETY", "SYNTHETIC_SELECTOR_TIMING",
                             "NO_PHYSICAL_STOPPING_PROOF", "NO_LIVE_OR_JEV_GAIN_CLAIM"]}
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        with (args.output_dir / "report.json").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    except Exception:
        print("SMOKE_FAILED", file=sys.stderr)
        return 1
    print("SYNTHETIC_SMOKE_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
