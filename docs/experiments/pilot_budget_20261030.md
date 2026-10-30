# New authorization for the outcome pilot

2026-10-30, Asia/Shanghai. Supersedes historical-reconciliation-as-prerequisite **for this new pilot only**, following explicit user approval. Old reports correctly retain UNKNOWN historic attempts; no old receipt or database is rewritten.

`initialize_pilot_ledger(path, receipt)` accepts the checked-in `pilot_authorization_20261030.json`. It initializes a new scope, not a fresh copy of the old preparation allowance. **Eight total requests**, across all video/data clients, process restarts and batches. Discovery, errors, retries and crashes after reservation count. Failed calls are not refunded. Eight successful choices is a target, not guaranteed under this attempt cap.

The sole agreed simulator owner is chat `01a18baa-2b83-77b3-826b-51e80b3b439b`. Both tasks must use exactly one task-local ledger (agreed canonical path communicated at handoff); no independent video ledger, alternative path after exhaustion, or test receipts in production. This is not a distributed anti-tampering/payment system: operator copies or fabricated receipts cannot be prevented by SQLite.

API key stays in the process environment. Required scope configuration:

```python
from pathlib import Path
from jev_uav_track.request_budget import initialize_pilot_ledger, PILOT_SCOPE, RequestBudget

# Initialize ONCE with the actual approved receipt, never if a ledger exists.
# initialize_pilot_ledger(Path(task_ledger), Path(approved_receipt))
budget = RequestBudget(Path(task_ledger), batch_id="pilot01", scope=PILOT_SCOPE)
```

Environment client uses `JEV_BUDGET_LEDGER`, `JEV_BUDGET_SCOPE=jev-uav-outcome-pilot-20261030`, and `JEV_BUDGET_BATCH=pilot01`. Without the explicit scope it remains an old preparation caller and refuses the new database. `JEV_API_KEY` is never printed. No automatic `.env` load was added.

Tests cover new authorization validation, no overwrite, old/new scope rejection, eight-total concurrency/restarts, explicit environment selection, corruption rejection, and transport-not-called for request nine. Existing preparation accounting retains 32 total / 8 batch. Tests use synthetic receipts and transport stubs only; their counts are not real Jev usage.

This implementation and its tests alone do not prove any API response, executed action, collected episode, PASS acceptance, or tracking improvement.
