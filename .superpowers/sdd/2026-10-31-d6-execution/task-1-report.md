# Task 1 implementation report

Status: DONE. Base: `0d5334d`, branch `feat/jev-outcome-dataset`.

## Scope and implementation

- Read the task brief and Global Constraints before editing. Used the TDD skill and its `writing-good-tests.md` reference, followed by verification-before-completion.
- Added frozen `RuntimePorts` with historical defaults 3100/3101/3102/42451. All four values must be distinct exact integers (booleans and floats rejected), each in 1024..65535.
- Settings and both independent-package fallback settings use the chosen AirSim endpoint. Shipping launch arguments use all three chosen CARLA endpoints.
- Both real CLI entry points accept `--carla-rpc-port`, `--carla-streaming-port`, `--carla-secondary-port`, `--airsim-rpc-port`. The launcher passes all four explicitly to the API client.
- `prepared.json` freezes an exact four-field ports object. Reuse rejects different, malformed or invalid port receipts before any process/RPC creation. Legacy receipts without ports permit only the original defaults. A rejected receipt creates no run directory.
- Preflight, startup, client monitoring and postflight snapshots include the chosen tuple plus standard ports 2000/2001/2002/41451 and historical dedicated ports 3100/3101/3102/42451. External Python/simulator/listener refusal remains conservative.
- Readiness requires the chosen CARLA RPC and AirSim RPC listeners to belong to the owned Shipping PID. The primary CARLA and AirSim clients and all five contract telemetry clients use the chosen endpoints. API reports and run requests/results retain the tuple.
- Runtime reserve remains 768 MiB. It is checked while waiting for the client in bounded communicate slices and immediately after the client completes. Invalid/nonfinite memory snapshots fail closed. A reserve failure closes the owned job and retains the low-memory snapshot, failure reason, client exit code and stdout/stderr (or an explicit log collection error).
- Town03 still rejects every non-API profile before RPC/process/run creation. Loaded settings and loaded map checks remain enforced. No controller/capture/command limits were changed.

## TDD evidence

### RED 1, before production changes

Command: `python -m pytest -q tests/test_runtime_endpoints.py`

Observed: **14 failed in 0.38s**, exit 1. Each failed at the intentional missing-feature assertion:

```text
E AssertionError: Explicit immutable runtime endpoints are missing
E assert False
E + where False = hasattr(runtime, 'RuntimePorts')
```

Cases covered settings/arguments/immutability; duplicate, boolean, float and out-of-range values; legacy/frozen receipt mismatch refusal; preparation receipt; protected and chosen snapshot ports; primary clients; wrong loaded settings; every contract telemetry connection; client-phase reserve failure and owned cleanup. These were actual missing-API assertion failures, not import/collection errors. Full raw output remains in execution tool result `aae4ae`.

### GREEN 1

Command: `python -m pytest -q tests/test_runtime_endpoints.py tests/test_isolated_runtime.py tests/test_map_probe.py`

Observed: **40 passed in 2.40s**, exit 0 (`4b69f3`).

### RED 2, before memory-completion/log fixes

Added real CLI execution using `runpy`, successful/legacy owned launches, final client-completion memory checks, retained failure logs, and invalid-memory cases.

Command: `python -m pytest -q tests/test_runtime_endpoints.py`

Observed: **5 failed, 18 passed in 1.95s**, exit 1 (`f78171`). Retained exact failure excerpts:

```text
test_client_memory_guard_and_owned_cleanup[waiting]
E FileNotFoundError: .../runs/memory/client_stdout.txt

test_client_memory_guard_and_owned_cleanup[complete]
E Failed: DID NOT RAISE SystemExit
"status": "API_SMOKE_COMPLETED"
"postflight": { "free_memory_kib": 786431, ... }

test_unknown_memory_cannot_pass_snapshot_validation[nan]
test_unknown_memory_cannot_pass_snapshot_validation[inf]
test_unknown_memory_cannot_pass_snapshot_validation[True]
E Failed: DID NOT RAISE ValueError
```

These failures proved missing log retention during reserve aborts, a completion-boundary reserve gap, and invalid measurement acceptance. The full raw output remains in the execution record; the original failures were not rewritten as passes.

### Final GREEN and regression

| Command | Actual result |
| --- | --- |
| `python -m pytest -q tests/test_runtime_endpoints.py tests/test_isolated_runtime.py tests/test_map_probe.py` | 49 passed in 2.23s, exit 0 (`6b5c03`) |
| `python -m pytest -q` | 418 passed, 1 skipped in 10.63s, exit 0 (`d17bac`, completion `3dc3d4`) |
| `python -m compileall -q src scripts tests` | exit 0, no diagnostics |
| `git diff --check` | exit 0, no whitespace diagnostics |

The full suite was run once for this task after focused green. Baseline provided by the primary agent was 395 passed, 1 skipped; the new endpoint module adds 23 cases. No simulator or network was used. The existing Windows ownership test runs its own synthetic Python sleep child under its test-owned job; it neither launches nor controls any external simulator process.

## Self-review

- Reviewed the complete scoped diff. Mutating a primary/telemetry port, omitting forwarded CLI flags, accepting a mismatched receipt, reverting chosen readiness ports or dropping client-phase memory checks is covered by behavioral tests.
- External dependencies alone are faked: CARLA/AirSim RPC surfaces, OS query/process/copy boundaries, and test timing. Settings/receipt writes, argument builders, snapshot validation, map/settings checks, contract flow and launcher safety/control flow execute for real.
- CLI tests run both scripts' actual `__main__` parsers. The contract integration test exercises the separate velocity reader and all four threaded axis readers, expecting literal port 42461 for every connection.
- Existing map tests verify Town03 motion rejection before any connection and low-memory startup refusal with retained map-request evidence. Their helper now accepts expected endpoints; its default behavior is unchanged.
- Only owned code/tests and this report are committed. Concurrent documentation edits by the primary agent are excluded.

## Limits and concerns

- This is offline implementation evidence. No independent runtime was prepared/launched here, no live API smoke was attempted, and no episode or tracking gain is claimed. Live physical preflight and exact-port ownership verification remain the primary agent's Task 3.
- Monitoring is periodic, not a hard real-time reserve guarantee: communicate slices are at most 2 seconds, and each Windows snapshot query already has its existing 20-second OS timeout. Query failure aborts the owned probe.
- No shared WPS files, protected source package/settings, reserved runtime, real HDF source data, Jev/network/key/ledger resources were accessed or changed. No push, reviewer, chat messaging or subagent creation was performed.
