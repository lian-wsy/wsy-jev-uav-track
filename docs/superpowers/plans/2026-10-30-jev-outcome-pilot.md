# Jev outcome pilot implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task. Do not start a simulator from this checkout.

**Goal:** Collect causal decision inputs, real Jev advice and separately observed action outcomes without confusing advice with truth.

**Architecture:** The existing video task is the sole simulator owner. This checkout supplies a bounded request ledger and an exclusive-output recorder. Neither component starts CARLA, flies, reads source HDF or trains on Town03.

**Tech Stack:** Python 3.10+, standard-library SQLite/JSON, pytest.

**Spec:** User-approved chat design on 2026-10-30: six development episodes, eight real Jev decision points, visible/occluded/out-of-frame strata; independent Town03 remains held out. Actual requests, including failures/retries/discovery, are capped at eight for this pilot, so successful decision points may be fewer.

## Global constraints

- Existing historical preparation attempts remain UNKNOWN. New user authorization is not a reconciliation receipt.
- One new scope and one shared local ledger for video and dataset; no parallel ledger reset.
- API keys only from environment; no raw credentials, headers or exception messages in artifacts.
- Existing preparation scope remains compatible and fail closed. New scope requires explicit selection.
- Current simulator ownership: chat `01a18baa-2b83-77b3-826b-51e80b3b439b`; no launches here. Startup >=4 GiB and runtime >=768 MiB gates unchanged.
- Decision records are frozen before selector calls; execution outcomes are separate labels. No source HDF writes or Town03 training.
- A record is NOT_AUDITED until an independent review. Writing a file never declares PASS, paired counterfactual support or tracking improvement.

## Task 1: Explicit new authorization budget

Files: `src/jev_uav_track/request_budget.py`, `src/jev_uav_track/jev_client.py`, `tests/test_pilot_request_budget.py`.

- [x] RED: exercise `initialize_pilot_ledger(path, receipt)` with new-scope authorization, reserve eight requests across restarted clients/batches and reject ninth; reject implicit old-scope access, malformed receipts, overwrites and concurrent excess.
- [x] GREEN: add `PILOT_SCOPE`, initializer, explicit `RequestBudget(..., scope=PILOT_SCOPE)`, and `JEV_BUDGET_SCOPE` environment selection. Keep old receipts and tests unchanged.
- [x] Run `python -m pytest -q tests/test_pilot_request_budget.py tests/test_jev_request_budget.py`; full regression; commit only owned files and send commit to video task for reviewed integration.

## Task 2: Causal recorder with independent outcome files

Files: `src/jev_uav_track/decision_dataset.py`, `tests/test_decision_dataset.py`.

- [x] RED: create `DecisionDatasetWriter(root, protocol)`, freeze a decision input with candidate set and history timestamps; reject future observation/history, unsafe or duplicate candidates and heldout maps. Preserve original bytes after labels are written.
- [x] RED: record advice from real/mock/utility, distinguish API-supplied probabilities from compatibility one-hot defaults; errors only by approved code, never arbitrary exception text. Record real action outcomes with exact action id and observed timing, without treating unexecuted alternatives as labels.
- [x] GREEN: exclusive per-record JSON files, finite JSON, conservative integrity validation, input SHA linkage. Reject overwrite, unknown decisions and invalid timestamps. Use owned output only.
- [x] Run `python -m pytest -q tests/test_decision_dataset.py`; run offline synthetic minimal reproduction marked SYNTHETIC/NOT_AUDITED, never as collected episodes. Commit and send recorder interface to runner owner.

## Task 3: Sole-runner integration and acceptance

- [x] Runner owner integrates verified commits without overwriting concurrent changes; both tasks use the same new ledger and explicit scope. Verified main commits 0aa0516 and 0dbf1f2; one pilot ledger initialized, zero attempts at handoff.
- [ ] Before run, freeze episode/seed/decision sampling schedule and controller. Confirm measured safety input sources; otherwise retain no-execution result.
- [ ] Owner executes serial bounded pilot in independent development instance, retaining all failed attempts; no Town03 development. Verify reset error before any paired claim.
- [ ] Audit actual data, snapshot/candidate/outcome hashes, source clocks, executed command logs, missing strata and request count; review PASS separately. Train only from admitted actual outcomes; no training from synthetic smoke or Jev advice as truth.

## Current boundary

Task 3 depends on the other chat's safety/execution chain; neither this plan nor tests certify that chain. Dataset interfaces do not grant motion permission. Scope remains the approved pilot, not a full D6-D14 experiment matrix.
