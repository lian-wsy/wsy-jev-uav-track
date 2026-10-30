"""Conservative, durable accounting for the authorized preparation requests.

One shared local ledger must be used per explicitly authorized scope. An audit
receipt is an operator attestation, not a verification of account history.
Never put credentials or request/response bodies in this database.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import uuid

SCOPE = "jev-uav-preparation-20261026"
TOTAL_LIMIT = 32
BATCH_LIMIT = 8
PILOT_SCOPE = "jev-uav-outcome-pilot-20261030"
PILOT_LIMIT = 8


class BudgetError(ValueError):
    """Missing, invalid or exhausted accounting prevents network access."""


def initialize_preparation_ledger(path: Path, receipt: Path) -> None:
    """Explicit initialization only, after accounting for ALL prior attempts."""
    try:
        raw = Path(receipt).read_bytes()
        audit = json.loads(raw)
    except (OSError, ValueError):
        raise BudgetError("Cannot read reconciliation receipt") from None
    if (not isinstance(audit, dict) or audit.get("status") != "RECONCILED"
            or audit.get("scope") != SCOPE
            or type(audit.get("prior_attempts")) is not int
            or not 0 <= audit["prior_attempts"] <= TOTAL_LIMIT
            or not isinstance(audit.get("evidence"), str)
            or not audit["evidence"].strip()):
        raise BudgetError("Complete reconciled attempt history is required")
    _initialize_ledger(path, SCOPE, audit["prior_attempts"], raw)


def initialize_pilot_ledger(path: Path, receipt: Path) -> None:
    """New user-authorized eight-attempt pilot, NOT historical reconciliation.

    All pilot clients must share this ledger. The prior count below is zero
    only for this new scope; the historical preparation count stays UNKNOWN.
    A receipt is an operator attestation; it cannot authenticate user consent.
    """
    try:
        raw = Path(receipt).read_bytes()
        audit = json.loads(raw)
    except (OSError, ValueError):
        raise BudgetError("Cannot read pilot authorization receipt") from None
    if (not isinstance(audit, dict)
            or audit.get("status") != "AUTHORIZED_NEW_PILOT_SCOPE"
            or audit.get("scope") != PILOT_SCOPE
            or audit.get("historical_preparation_attempts") != "UNKNOWN"
            or type(audit.get("request_limit")) is not int
            or audit["request_limit"] != PILOT_LIMIT
            or "prior_attempts" in audit
            or not isinstance(audit.get("evidence"), str)
            or not audit["evidence"].strip()):
        raise BudgetError("Explicit new pilot authorization is required")
    _initialize_ledger(path, PILOT_SCOPE, 0, raw)


def _initialize_ledger(path: Path, scope: str, prior: int, raw: bytes) -> None:
    path = Path(path)
    try:
        # Refuse existing ledgers, including empty/corrupt ones. No reset path.
        with path.open("xb"):
            pass
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE metadata (scope TEXT, prior INTEGER, receipt_sha TEXT)")
            conn.execute("INSERT INTO metadata VALUES (?, ?, ?)",
                         (scope, prior, hashlib.sha256(raw).hexdigest()))
            conn.execute("CREATE TABLE attempts (id INTEGER PRIMARY KEY, batch TEXT NOT NULL, "
                         "operation TEXT NOT NULL, reserved_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    except (OSError, sqlite3.Error):
        raise BudgetError("Cannot initialize ledger; existing ledgers must not be replaced") from None


class RequestBudget:
    def __init__(self, path: Path, batch_id: str | None = None, *, scope: str = SCOPE):
        if scope not in (SCOPE, PILOT_SCOPE):
            raise BudgetError("Unknown request authorization scope")
        self.scope = scope
        self.total_limit = PILOT_LIMIT if scope == PILOT_SCOPE else TOTAL_LIMIT
        self.path = Path(path).resolve()
        self.batch_id = batch_id if batch_id is not None else uuid.uuid4().hex
        if not isinstance(self.batch_id, str) or not self.batch_id or len(self.batch_id) > 128:
            raise BudgetError("A short nonempty batch identifier is required")

    def _connect(self):
        # mode=rw must never silently create a missing ledger.
        return sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)

    def reserve(self, operation: str) -> int:
        """Commit before sending; crashes/failures remain charged, never refunded."""
        if operation not in {"choice", "models"}:
            raise BudgetError("Unsupported request operation")
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute("SELECT scope, prior, receipt_sha FROM metadata").fetchall()
                if (len(rows) != 1 or rows[0][0] != self.scope or type(rows[0][1]) is not int
                        or not 0 <= rows[0][1] <= self.total_limit
                        or (self.scope == PILOT_SCOPE and rows[0][1] != 0)
                        or not isinstance(rows[0][2], str) or len(rows[0][2]) != 64
                        or any(c not in "0123456789abcdef" for c in rows[0][2])):
                    raise BudgetError("Invalid or mismatched authorization metadata")
                total = rows[0][1] + conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
                batch = conn.execute("SELECT COUNT(*) FROM attempts WHERE batch=?",
                                     (self.batch_id,)).fetchone()[0]
                if batch >= BATCH_LIMIT:
                    raise BudgetError("Request batch limit reached (8)")
                if total >= self.total_limit:
                    raise BudgetError(f"Request total limit reached ({self.total_limit})")
                conn.execute("INSERT INTO attempts (batch, operation) VALUES (?, ?)",
                             (self.batch_id, operation))
            return total + 1
        except (OSError, sqlite3.Error):
            raise BudgetError("Request ledger unavailable or invalid; network disabled") from None
