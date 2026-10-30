# Jev advice and measured-outcome pilot: recorder handoff

2026-10-30, Asia/Shanghai. This is an implemented recorder and offline smoke, **not a completed collection**. No real Jev request, simulator action or PASS episode was produced by the recorder work.

## Ownership and authorization

The video chat is the sole simulator runner. Its live observation, safety, command and recording code stays in the original checkout. This task works in an independent Git clone, and supplies the new scope budget and recorder by reviewed commits. No shared source, simulator configuration, collection/upload process or source HDF was modified here.

The user waived historical Jev reconciliation and approved six development episodes/eight real Jev decision points. Both tasks share `jev-uav-outcome-pilot-20261030`, **eight attempts total**, including discovery/failures/retries; no independent video allowance. The one actual ledger was initialized with the explicit user-authorization receipt and zero reservations at handoff. This does not claim historical preparation attempts were zero. See [budget protocol](pilot_budget_20261030.md).

## API contract

```python
from jev_uav_track.decision_dataset import DecisionDatasetWriter

writer = DecisionDatasetWriter(new_output_directory, protocol)
input_sha = writer.begin_decision(
    decision_id, episode_id, state, candidates,
    observation_time_s=observation_time, history_times_s=history_times,
    decision_time_s=decision_time, clock_domain="sim_monotonic",
    safety_sources={"clearance": "scene_geometry", "battery": "unobserved"})
# Above must complete BEFORE any selector call. Unknown safety makes safe ids empty.
# The runner must then skip selectors and dispatch, recording only refusal:
writer.record_no_execution(decision_id, reason="safety_rejection")
```

`protocol` declares kind (`LIVE_SIMULATION` or `SYNTHETIC`), exact code commit `code_sha`, actual model `model_sha256`, `max_decisions` <=8, and 1–6 episodes with unique `id`, development `map`, integer `seed`, intended `stratum` (visible/occluded/out_of_frame), and `split` (development_train/development_validation). Map03 aliases are rejected; only already exposed development maps are permitted. Freeze the concrete schedule before running; the smoke fixture schedule is **not** a scientific experiment schedule.

Recorder source SHA is added automatically. Every input binds protocol SHA; every advice/outcome binds input SHA. Later writes verify both files are unchanged. File creation is exclusive, fsynced, and rejects overwrites. After a process crash leave the incomplete run for audit; no silent restart/append/rewrite. Single writer only. File hashes detect modification, not malicious operators or forged sensor provenance.

All input times must use one declared monotonic domain: belief timestamp and all ordered history timestamps <= observation <= decision. State and candidate metadata reject known heldout origins and explicit map/seed/split contradictions. Missing identity/producer metadata remains unproven; this does not authenticate maps or prove a producer used only causal preprocessing. Free-text history must be formed by the existing causal history builder; no string checker can establish its temporal truth.

### Advice

Only candidates accepted by the existing filter **and** with non-missing safety source declarations may receive recorded advice. The recorder does not acquire telemetry or authorize dispatch. The runner still enforces measured safety, clocks, expiry, speed and ownership.

`record_advice(id, selector, decision, latency_ms=...)` accepts real_jev/mock_jev/utility. Fields are allowlisted, not the raw API response. When real Jev did not supply probabilities, stored probabilities are null/NOT_PROVIDED, even if the compatibility parser synthesized one-hot values. This prevents accidental teacher-confidence fabrication. API advice is `ADVICE_NOT_GROUND_TRUTH`; no advice record is accepted as an outcome training label.

`record_advice_failure(id, selector, error_code=..., latency_ms=...)` accepts bounded categories timeout/budget/protocol/transport/missing_key. Raw exception text and authorization headers are never logged. The runner must retain usage/model version/transport receipts separately in a sanitized log if needed; this recorder does not preserve raw request-response text or certify response provenance.

### Execution and labels

`record_outcome(id, executed_action_id, execution_trace=[...], label_end_s=..., metrics={...})` needs acknowledged commands matching the candidate's world-z-up velocity and yaw rate. Each trace entry has timestamp_s, duration_s, command_mps, yaw_rate_dps, acknowledged. Keep original API/NED receipts in the runner log and explicitly convert units/axes. Do not infer commands from displacement.

Observed metrics: visibility_fraction, collision, minimum_clearance_m, range_error_m, reacquisition_time_s. Unknown values are null. The runner must calculate metrics from actual timestamped observations and retain their evidence; supplied numbers are not independently audited here. Scalar reacquisition alone is insufficient for the frozen fixed-window event evaluation.

Actual summed command duration and elapsed span are separate. A 100ms prefix is not labeled as the predicted full one-second outcome. Gapped commands cannot claim continuous full-horizon execution merely because their durations add up. Delay/expiry is still the dispatcher's responsibility. Every row remains NOT_AUDITED, paired_counterfactual=false. Reset verification and PASS admission require separate audit; copying a run cannot establish counterfactual pairing.

## Reproduction and evidence

```powershell
python -m pytest -q tests/test_pilot_request_budget.py tests/test_jev_request_budget.py tests/test_decision_dataset.py
python scripts/smoke_decision_dataset.py --output artifacts/decision_dataset_smoke_new_run
```

Use a fresh output. Synthetic smoke writes a frozen input, Mock and utility advice, and a synthetic partial-action receipt with unknown outcome metrics. It creates no real client/simulator, never loads source HDF, and is not training data. Earlier smoke retained under `artifacts/decision_dataset_smoke_20261030`; subsequent source revisions must be identified by recorder_source_sha256 and a fresh run.

Independent review found candidate holdout metadata, conflicting episode provenance and protocol tamper gaps. Each was reproduced before correction; regression tests now cover them, along with future timestamps, source secrets, input mutation, write refusal, safety missingness, advice probability provenance, unknown labels and command mismatch.

## Remaining acceptance

- Collecting six real episodes and eight successful API decisions is **not complete**.
- Real safety provenance is not yet sufficient for execution; AirSim battery absence must remain unknown unless a justified explicit simulator resource model is approved and implemented. Never fill 100% to pass a gate.
- Intended visible/occluded/out-of-frame strata are protocol intent, not measured coverage. Real identity crossing remains NOT_AUDITED.
- No new model training until actual observed outcomes pass review. D5 negative results and Town03 holdout status remain unchanged.

## Read-only integrity inventory

```powershell
python scripts/audit_decision_dataset.py <completed-run-directory>
```

This reader never edits the run. It checks protocol/input/advice/outcome hash links, known source conflicts and heldout hints, temporal ordering, request-state equality, candidate partitions, advice provenance, executed-command correspondence and reported durations. Unexpected record files and truncated/invalid JSON are rejected. Missing outcomes and empty runs report INCOMPLETE; broken links or checked contracts report INVALID. CLI exit 0 means CHECKED for these invariants only, exit 2 means incomplete/invalid. Re-run only on a terminal run: a concurrent writer can legitimately yield an incomplete snapshot.

The report **always** retains training_admission=NOT_AUDITED and admitted_training_samples=0. API response *records* are separate from authenticated responses and budget attempts; attempts cannot be inferred from successes/failures written by a recorder. Intended scenario labels do not verify observed strata. SYNTHETIC execution receipts do not increase live_execution_records. Camera-only NOT_EXECUTED entries cannot count as collision-free labels.

Independent provenance, API/ledger reconciliation for this new batch, actual telemetry/frames/command acknowledgements, metric recomputation and eventual train/validation admission remain separate. Hash-consistent fabricated files are possible: this utility is not an authenticity or tamper-proof system. It does not inspect installed model/code files merely because their fingerprints are included in a protocol.
