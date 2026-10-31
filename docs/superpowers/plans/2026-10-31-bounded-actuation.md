# Bounded actuation implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task in the existing isolated clone. The user waived repeated approval prompts; independent code review remains required before publication.

**Goal:** Carry actual controller expiry into one asynchronous movement/cancel/neutral transaction, and separate RPC acknowledgements from measured stopping.

**Architecture:** Extend `ControlTick` with an absolute deadline; add an injected, one-slot actuator and own-vehicle telemetry verifier. The AirSim adapter does not connect or arm. Offline integration uses the existing real selector/control loop and explicitly synthetic port/telemetry.

**Tech Stack:** Python >=3.10, standard library, existing pytest; no dependencies added.

**Spec:** `docs/superpowers/specs/2026-10-31-bounded-actuation-design.md`.

## Global constraints

- Existing independent clone/feature branch only; no other chats or protected runtime operations.
- No training, source HDF reads, Jev requests, new ledger, key access or live launch in this slice.
- <=0.3 m/s, <=15 deg/s, <=50 ms prefixes; 200 ms expiry/watchdog; no unmeasured clearance substitution.
- Grounded state does not authorize tracking. Acknowledgement does not establish stop or safety.
- Preserve old evidence and negative results; D1 Town03 PASS remains 0, D6 live readiness remains partial.

## Task 1: Preserve expiry across the controller/transport boundary

**Files:** `src/jev_uav_track/async_control.py`, `tests/test_async_control.py`.

**Interface:** `ControlTick.dispatch_deadline_s: float | None = None`. Compiled ticks carry `min(capture.wall_start_s+.2, advice.valid_until_s, energy_deadline_if_any)`. `NO_COMMAND` ticks carry None.

- [x] Add failing tests using the existing controlled executor: normal second tick at 1.05 carries deadline 1.24; missing measurements carry None; near original action expiry retains 2.0 even with newer capture; energy deadline cannot be exceeded.

```python
clock, executor, coordinator, loop, measured = setup_loop()
tick(loop, clock, measured)
executor.complete()
clock.now = 1.05
result = tick(loop, clock, measured)
assert result.dispatch_deadline_s == pytest.approx(1.24)
```

- [x] Run `python -m pytest -q tests/test_async_control.py`; confirm RED on missing lease.
- [x] Return deadline from the existing final-dispatch callback, without adding a later callback that could extend validity. Re-run focused tests.

## Task 2: One-slot transactional actuator and stop verifier

**Files:** create `src/jev_uav_track/actuation.py`, `tests/test_actuation.py`.

**Interfaces:** `FlightTelemetry`, `BoundedActuator(transport_factory, execution_domain=..., generation=0, clock=..., executor=None)`, `submit(tick, telemetry) -> str`, `poll()`, `close()`, `AirSimMovementPort(client, sdk, vehicle_name)`, `StopVerifier(receipt, baseline).observe(telemetry, now_s) -> str`. Explicit simulation domain is required; it is not ownership proof.

- [x] RED: table-driven grounded/unknown/stale/overlimit/absent-lease/replayed/wrong-generation cases emit no movement; worker-delayed expiry emits none. Test the actual production actuator with an injected external port, not source strings.

```python
assert actuator.submit(prefix, grounded) == "GROUNDED_NOT_READY"
assert port.sent == []
assert actuator.submit(prefix, airborne) == "SUBMITTED"
clock.now = 1.21
scheduler.complete()
assert actuator.poll().status == "EXPIRED_BEFORE_SEND"
assert port.sent == []
```

- [x] GREEN: dataclasses, finite/exact type validation, immutable snapshots, one worker slot, final expiry shortening, one-way close/timeout latch, no worker restart or movement retry.
- [x] RED/GREEN: successful RPC is followed by cancel then zero-velocity/zero-yaw 50 ms; rejected/raising RPC still attempts both; cancel failure does not skip neutral; only `True` is acknowledgement; unknown exceptions do not leak text. Test a genuinely blocked worker and nonblocking submit/poll/close.
- [x] RED/GREEN: AirSim wrapper forwards exact world-NED/yaw/vehicle and invokes Future.get, never join alone. Constructor has no side effects; use an injected client already owned by a supervisor.
- [x] RED/GREEN: three fresh distinct stop samples are required, moving/angular-moving/replayed/stale/new-collision/changed-landed samples cannot pass, late/failed/unknown neutral cannot pass, samples must start after neutral completion.
- [x] Run `python -m pytest -q tests/test_actuation.py tests/test_async_control.py` and review mutations in expiry, cancel/neutral and acknowledgement branches.

## Task 3: Reproducible integration evidence and publication

**Files:** create `scripts/run_d6_actuation_smoke.py`, `tests/test_actuation_smoke.py`, experiment report/evidence; update daily/roadmap/acceptance pointers.

- [x] RED/GREEN: script uses the production pipeline/loop/actuator, synthetic port/clock/own telemetry, utility and Mock Jev under identical limits. Include grounded refusal, delayed-before-send rejection, acknowledgement+stop telemetry, motion RPC failure and watchdog timeout. Output fresh exclusive directory, finite JSON; no RPC/client construction, key read or training.
- [x] Run `python scripts/run_d6_actuation_smoke.py --output-dir artifacts/d6_actuation_20261031`; run with existing Python3.10 and a distinct output path. Verify raw JSON and preserve non-success cases.
- [x] Full `python -m pytest -q`, both interpreters' compileall, `git diff --check`. Independent code review and fix any evidenced findings with RED/GREEN tests.
- [x] Write measured results and remaining live geometry/startup/transport limits. Commit only this slice, push existing feature branch and verify remote HEAD. Do not complete the D1–D6 goal.

## Completion receipt for this slice, not the D1–D6 goal

Code `9f20a1d`, experiment report and independent evidence review published on the existing feature branch. `834b5a99d84f80f679c7f8d1f37a716e39711b3e` was pushed and its remote ref verified before this receipt. Full 763 passed/1 skipped; independent targeted 152 passed and seven report/source hashes plus both 14-case matrices verified. The report explicitly states poll-driven watchdog/hung-RPC limits. No main merge, live launch, training, source HDF read, Jev call or other-chat contact. Original D1–D6 remains incomplete; this checklist describes only the bounded offline transport slice.
