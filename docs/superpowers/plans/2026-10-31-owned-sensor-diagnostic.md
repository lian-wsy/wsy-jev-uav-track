# Owned sensor diagnostic implementation plan

> **For agentic workers:** use test-driven implementation and a separate safety review before live execution. This is one tightly coupled settings/launcher/client change; implement it inline, not as parallel writers.

**Goal:** obtain named distance/Lidar raw returns in one no-arm/no-motion Town02 diagnostic, with exact restoration of the three owned settings copies.

**Architecture:** a task-local settings transaction stages durable backups before applying an explicit sensor block. The existing isolated launcher applies it only after preflight and restores it after owned handles have stopped; the read-only client verifies the loaded settings and passes exact names to the bounded raw diagnostic.

**Tech stack:** Python 3.10+ stdlib, pytest, owned Windows Job, existing CARLA/AirSim client.

**Spec:** the user's approved temporary sensor configuration scope, plus `docs/experiments/d6_live_sensor_probe_20261031.md` (limitations and previous evidence).

## Global constraints

- Only `D:/JevUavRuns/20261031_d6_probe01`, ports 3200/3201/3202/42461, one new output directory.
- No arm, movement, reset, images, controller integration, Jev calls, training, or Town03 development.
- No external process/config/lock changes; preserve 4 GiB startup and 768 MiB runtime gates.
- No API keys in files/logs; old failure evidence and source data remain unchanged.
- Diagnostic always reports `control_ready=false`, `clearance_m=null`, `NOT_CERTIFIED`.

## Checklist / acceptance

- [x] Existing isolated feature branch and clean baseline: 596 passed / 1 skipped.
- [x] Verify primary configuration semantics: pinned Microsoft AirSim commit `1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41`, `docs/sensors.md`, `docs/lidar.md`, `docs/distance_sensor.md`, `AirSimSettings.hpp`, sensor parameter headers. Upstream merges default sensors by missing type; local binary identity is not inferred from that source.
- [x] Add failing transaction tests, observe RED (24 missing-feature failures), implement, observe GREEN (24 passed); add 3 supplemental fault/ownership checks (27 passed).
- [x] Add failing named-RPC and launcher lifecycle tests, observe RED (18 failures / 30 existing passes), implement, observe GREEN (127 focused passed before the 3 supplemental checks).
- [x] Full tests: 641 passed / 1 skipped (17.73s); compile and CLI help on both interpreters passed; independent safety reviewer approved the single preflight-gated no-motion run (39 focused tests independently passed).
- [ ] One preflight-gated live run (or retain a blocked receipt without launch).
- [ ] Recheck exact settings and protected-file hashes / process exit / own listeners; preserve raw evidence and document limitations.
- [ ] Commit and push verified changes to existing feature branch, no merge, no whole-goal completion claim.

## Transaction contract

Create `src/jev_uav_track/sensor_configuration.py` and `tests/test_sensor_configuration.py`.

Interface: `OwnedSensorConfiguration(source, task, output, *, ports, map_name, profile)`, `.apply()`, `.restore(owned_stopped=True)`, `.audit`.

Only three fixed targets are allowed: `settings.json`, `package/settings.json`, `package/CarlaUE4/Binaries/Win64/settings.json`. Reject links/reparse points/hardlinks, output escapes, disagreeing originals, existing custom sensor settings, wrong multirotor/vehicle/port, duplicate JSON keys and nonfinite JSON. Back up all three original byte strings with exclusive create, flush/fsync/readback before the first settings replacement. Refuse an existing backup or receipt. Compare contents again just before each atomic replacement. On partial failure restore only files actually changed, continue restoring unaffected files, preserve backups and explicit failure audit. Never restore a changed file over an external edit or while owned handles remain live.

The sole settings delta is `Vehicles.JevDrone.Sensors`: explicit lowercase imu/magnetometer/gps/barometer types 2/4/3/1, enabled with upstream defaults; `JevDistanceFront` type5, min0.2/max40m, (0,0,-0.5)m, zero rotation; `JevLidarDiagnostic` type6, 4 channels, 4000points/s, 10 rotations/s, range40m, same pose, vertical+10/-30deg and horizontal0/359deg, `SensorLocalFrame`. Debug drawing and external-controller feeds false on the two diagnostic sensors. No spawn/camera/clock/port changes. These choices bound load; they do not certify obstacle coverage.

Test skeleton (each assertion exercises the real filesystem transaction):

```python
transaction.apply()
for path in targets:
    observed = json.loads(path.read_bytes())
    sensors = observed['Vehicles']['JevDrone'].pop('Sensors')
    assert observed == original_settings
    assert sensors['JevDistanceFront']['SensorType'] == 5
    assert sensors['JevLidarDiagnostic']['PointsPerSecond'] == 4000
transaction.restore(owned_stopped=True)
assert [p.read_bytes() for p in targets] == original_bytes
```

Inject failures at backup fsync, second config replacement and first restoration; assert original bytes remain recoverable and other copies restore. Inject a concurrent edit and hardlink; assert they are untouched. Existing receipts must not be overwritten. `restore(owned_stopped=False)` must refuse all writes.

## Client / launcher contract

Modify `sensor_probe.py`, `probe_carla_apis.py`, `probe_isolated_carla.py`, with tests in `test_sensor_probe.py`, `test_map_probe.py`, `test_runtime_endpoints.py`.

Add explicit `--configure-diagnostic-sensors` boolean, default false, valid only for `smoke`, Town02, `safety`. The launcher records opt-in and module SHA, applies settings inside its cleanup-protected try after preflight, propagates opt-in, and requires the returned sensor names and settings verification. In `finally`, restoration follows confirmed owned process exit and contributes to `AUDIT_FAILED` on any failure. Legacy/default execution remains untouched.

Extend `probe_safety_inputs(..., distance_sensor_name='', lidar_name='')`; validate bounded names before RPC, record names and query names, use them in raw calls. The client verifies the entire loaded settings plus exact diagnostic block before these calls. No new movement methods. Test disallowed profiles/maps before connection, wrong loaded settings, no implicit sensor enablement, named query arguments, partial launch failure and unconfirmed cleanup.

Run:

```powershell
python -m pytest -q tests/test_sensor_configuration.py tests/test_sensor_probe.py tests/test_map_probe.py tests/test_runtime_endpoints.py
python -m pytest -q
python -m compileall -q src scripts tests
& 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' -m compileall -q src scripts tests
```

Python3.10 does not have pytest; do not install it or claim that test suite ran there. The authorized live run uses its existing CARLA client, a new run name, all four explicit own ports and the opt-in flag. Source and task settings receipts plus exact integer sensor timestamps remain local raw evidence; summarize without converting nanosecond integers through JS floats.
