# D6 execution and independent runtime implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task. This implements the original, user-approved D6 and the 2026-10-31 timed D1–D6 authorization, not D7.

**Goal:** Make independent runtime configuration explicit and exercise a bounded asynchronous control loop with deterministic mock reset, stale-response rejection and timeout regression.

**Architecture:** Keep CARLA/AirSim startup separate from the simulator-neutral decision/control loop. Preserve the existing 200 ms observation gate, one-second candidate horizon and bounded low-level command limits. Use real pipeline/selector logic with an explicitly synthetic deterministic adapter for offline evidence; real-runtime API checks are separate evidence.

**Tech Stack:** Python 3.10+, standard library, existing numpy/pytest; no additional packages.

**Spec:** `docs/sprint_20261026_20261108.md`, original D2/D3/D6 acceptance rows and current 2026-10-31 user authorization. Original D1/D4/D5 requirements remain unchanged. This is execution of that approved scope, not a proposal to expand it.

## Global Constraints

- Work only in `D:/JevUavRuns/repos/jev-outcome-dataset-20261030`, branch `feat/jev-outcome-dataset`. Do not modify the shared WPS checkout.
- Do not message other chats, start/control their processes, or touch reserved runtime `D:/JevUavRuns/20261028_d2_probe01` or ports 3100/3101/3102/42451.
- No simulator launches by implementers/reviewers. The primary agent may run only freshly verified owned infrastructure under the current user authorization.
- No source HDF reads/writes, training, Jev calls, key access, request ledger mutations or external network by implementers. Reuse existing training evidence.
- Town03 remains heldout: infrastructure API only in this plan; no arm/motion/images/strategy evaluation there. A successful API check is not a PASS episode.
- Startup free RAM >=4 GiB, runtime >=768 MiB, protected source settings immutable, independent copy/output, fail closed on unknown ownership. Never lower these gates.
- Preserve the existing 150 ms capture / 200 ms observation expiry / <=100 ms command prefix / <=0.3 m/s / <=15 deg/s limits. A new observation cannot revive an already stale high-level response.
- Missing/nonfinite safety measurements forbid motion. Do not call manufactured defaults real measurements. Synthetic values must be labeled synthetic.
- All selectors use the same controller, candidate horizon and command duration. No automatic promotion of validation-best model.
- Use TDD and apply_patch. Keep raw failures and known gaps. Offline mock results do not establish live control, safety or Jev gains.

### Task 1: Explicit, frozen independent endpoints

**Files:** modify `src/jev_uav_track/isolated_runtime.py`, `scripts/probe_isolated_carla.py`, `scripts/probe_carla_apis.py`; add `tests/test_runtime_endpoints.py`, extend existing map/runtime tests if needed.

**Interfaces:** add immutable `RuntimePorts(carla_rpc=3100, carla_streaming=3101, carla_secondary=3102, airsim_rpc=42451)` with distinct integer ports in 1024..65535. Existing default behavior remains for historical reproduction, but this task's live plan uses 3200/3201/3202/42461 explicitly. Pass it consistently through settings, launch arguments, snapshots/readiness, main client and every telemetry client. Add corresponding four explicit CLI options to both launch and client scripts. Freeze the ports in prepared.json and refuse mismatched reuse before process/RPC creation; legacy receipts without ports only admit the original defaults.

- [x] RED: test settings/arguments for new endpoint tuple and mismatch refusal; tests must fail because the current implementation is hardcoded.

```python
ports = RuntimePorts(3200, 3201, 3202, 42461)
path = write_settings(task, ports=ports)
assert json.loads(path.read_text())["ApiServerPort"] == 42461
args = launch_arguments(task, ports=ports, map_name="Town03")
assert "-carla-rpc-port=3200" in args
assert "-carla-streaming-port=3201" in args
assert "-carla-secondary-port=3202" in args
```

- [x] RED: fake only external CARLA/AirSim clients and OS process launch; verify every requested connection uses the specified ports, wrong loaded settings fails, Town03 motion fails before RPC, a prepared-port mismatch launches nothing, legacy receipts cannot use the new tuple, duplicate/bool/out-of-range ports fail before writes.
- [x] GREEN: implement smallest consistent propagation; snapshot still watches protected standard/legacy ports as well as chosen ports. Do not relax conservative external-process rejection. Resource monitoring must continue during the client probe, not only startup; if reserve falls below 768 MiB stop only this job and preserve failure evidence.
- [x] Verify `python -m pytest -q tests/test_runtime_endpoints.py tests/test_isolated_runtime.py tests/test_map_probe.py`, then full suite and compileall. Self-review, record RED/GREEN and commit only owned files.

### Task 2: D6 asynchronous bounded loop and deterministic mock

**Files:** create `src/jev_uav_track/async_control.py`, `src/jev_uav_track/mock_simulator.py`, `scripts/run_d6_mock_smoke.py`, `tests/test_async_control.py`, `tests/test_d6_mock_smoke.py`.

**Interfaces:** `AsyncDecisionCoordinator` owns at most one in-flight selector call, a reset generation and immutable request snapshot. `BoundedControlLoop` consumes fresh `TrackingState` plus `CaptureWindow`/explicit measured-safety validity, polls advice without waiting, rechecks the selected action against current safety and compiles via `compile_carlaair_candidate`/`TemporalObservationGate`. `DeterministicMockSession.reset(seed, scenario)` and fixed-step observations supply synthetic kinematics/safety; it is never presented as CARLA/AirSim or admitted data. The implementation may define compact dataclasses for request/tick records, but must keep these responsibilities separate.

- [x] RED: prove a selector blocked on a threading.Event cannot block control ticks, only one in-flight request exists, old replies after 200 ms are discarded, timeout does not start unlimited new worker threads, close is non-blocking and old-generation replies after reset cannot cause motion. Inject a monotonic clock / controlled Future for deterministic timing; use a real event-blocked worker in at least one integration test.

```python
# Illustrative contract; concrete signatures belong to the implementation.
# A late high-level reply must not be laundered through a fresh observation.
# Given request at t=0 and completion at t=.21, current frame t=.21:
# assert coordinator.accepted == 0
# assert coordinator.stale_responses == 1
# assert no_command_from_old_choice
```

- [x] RED: on each 50 ms tick enforce fresh measured battery/clearance, reject collision/nonfinite/expired/reversed inputs and unsafe/unknown selector choices. If fresh measurement fails, revoke prior motion; stopping/no command must be explicit, not an unsafe new fallback. Fresh inputs revalidate the bounded prefix of an already timely accepted action, but never extend its original one-second lifetime. Exception/timeout/invalid action cases must preserve an auditable reason code, not arbitrary exception text.
- [x] GREEN: single bounded worker, immutable copied snapshots/candidates, generation tagging, nonblocking poll, no queue accumulation or automatic network retries. Respect no-long-waits shutdown even if a call hangs; cannot kill a Python thread, so do not pretend cancellation succeeded or create replacement threads until the running call ends. Keep high-level period 1 s, low-level 0.05 s, candidate duration 1 s, command prefix <=0.05 s (and existing expiry cap). Use the same controller/limits for utility and Mock Jev; only client selection differs. Reject commands over the established speed/yaw caps rather than silently changing candidate semantics.
- [x] GREEN: deterministic synthetic adapter with explicit `SYNTHETIC_NOT_CARLA` evidence class, fixed-seed reset and fixed-step kinematics; tests show identical seed/scenario/actions reproduce initial state and trace, different seeds differ. Default bounded primitive commands must be valid at 0.3 m/s and 15 deg/s; do not replace production DEFAULT_PRIMITIVES or train new models.
- [x] RED/GREEN: real pipeline/Mock Jev and utility smoke with fixed seeds, normal/delayed/timeout cases, shared horizon/controller and full raw traces. CLI writes to a fresh exclusive output directory, JSON finite, no overwrites/network/API key use. Report request/accept/stale/timeout/fallback/command counts, reset reproducibility and unavailable live/D1 data; do not assert tracking improvement.
- [x] Verify focused tests, full suite and compileall; self-review; record RED/GREEN and one reproducible smoke command; commit owned code/tests/script only. Primary agent writes acceptance report after independent review.

### Task 3: Primary-agent empirical acceptance and documentation

- [x] Review commits before live use. Recheck source/path/process/listeners/configuration and resource gates. Create only this task's new runtime at `D:/JevUavRuns/20261031_d6_probe01`, exact ports 3200/3201/3202/42461. No links/shared package or video-instance access.
- [x] If conditions hold, prepare one independent copy and run one Town03 API-only smoke, retaining actual map, settings, versions, resources, original failures, protected hashes and cleanup evidence. If blocked record one bounded failure and continue offline. No collection or fake same-snapshot labels.
- [x] Run D6 mock smoke, check deterministic outputs and all counters. Real unavailable paths stay NOT_EVALUATED/PARTIAL.
- [x] Update original-scope D1–D6 requirement/evidence matrix, daily report, roadmap/sprint current-scope banner. Reuse D3/D4/D5 results, preserve negative results and Town03 PASS=0 unless actual complete data audit proves otherwise.
- [x] Run targeted/final regression, compileall and git diff --check; independent review; push verified commits to feature branch only, no merge to main. Keep goal unfinished while any original criterion lacks evidence.

## Slice completion receipt

2026-10-31: 481 passed / 1 skipped; compileall and diff checks exit 0. Independent full-slice review of `a0e9476..ab82dd9` approved publication with no blocking findings. Feature branch was pushed and its remote ref verified as `ab82dd9a5a4f44c7588f4d14cebdc700cff45a8c` before this receipt. No main merge or workspace deletion. Task execution checklist completion does not mean original D1–D6 acceptance: Town03 PASS and difficult-scene/live gaps remain PARTIAL, as recorded in the acceptance report.
