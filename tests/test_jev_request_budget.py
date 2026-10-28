import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def make_ledger(path, prior=0):
    from jev_uav_track.request_budget import initialize_preparation_ledger
    # Test-only reconciliation receipt, no account or real requests.
    receipt = path.with_suffix(".json")
    receipt.write_text(json.dumps({"status": "RECONCILED", "prior_attempts": prior,
                                  "scope": "jev-uav-preparation-20261026",
                                  "evidence": "synthetic unit-test receipt"}), encoding="utf-8")
    initialize_preparation_ledger(path, receipt)


def test_missing_or_unknown_ledger_cannot_authorize_requests(tmp_path):
    from jev_uav_track.request_budget import RequestBudget, BudgetError, initialize_preparation_ledger
    with pytest.raises(BudgetError):
        RequestBudget(tmp_path / "missing.db").reserve("choice")
    receipt = tmp_path / "unknown.json"
    receipt.write_text(json.dumps({"status": "UNKNOWN", "prior_attempts": None}), encoding="utf-8")
    with pytest.raises(BudgetError):
        initialize_preparation_ledger(tmp_path / "unknown.db", receipt)
    assert not (tmp_path / "unknown.db").exists()


def test_batch_limit_persists_across_client_instances(tmp_path):
    from jev_uav_track.request_budget import RequestBudget, BudgetError
    path = tmp_path / "ledger.db"
    make_ledger(path)
    for _ in range(8):
        RequestBudget(path, batch_id="same-batch").reserve("choice")
    with pytest.raises(BudgetError, match="batch"):
        RequestBudget(path, batch_id="same-batch").reserve("choice")


@pytest.mark.parametrize("prior,same_batch,expected", [(30, False, 2), (0, True, 8)])
def test_concurrent_reservations_never_exceed_limits(tmp_path, prior, same_batch, expected):
    from jev_uav_track.request_budget import RequestBudget, BudgetError
    path = tmp_path / "ledger.db"
    make_ledger(path, prior=prior)
    def attempt(i):
        try:
            RequestBudget(path, batch_id="same" if same_batch else f"batch-{i}").reserve("choice")
            return True
        except BudgetError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(12))) == expected


def test_cannot_overwrite_a_frozen_attempt_ledger(tmp_path):
    from jev_uav_track.request_budget import initialize_preparation_ledger, BudgetError
    path = tmp_path / "ledger.db"
    make_ledger(path, prior=31)
    before = path.read_bytes()
    with pytest.raises(BudgetError):
        initialize_preparation_ledger(path, path.with_suffix(".json"))
    assert path.read_bytes() == before


def test_failed_network_request_consumes_budget_and_next_call_is_blocked(tmp_path, monkeypatch):
    from jev_uav_track.request_budget import RequestBudget, BudgetError
    from jev_uav_track.jev_client import TypeSafeJevClient, JevProtocolError
    from test_core import make_state
    from jev_uav_track.rollout import KinematicRolloutModel
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    path = tmp_path / "ledger.db"
    make_ledger(path, prior=31)
    state = make_state()
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    client = TypeSafeJevClient("test-only-key", budget=RequestBudget(path))
    sent = []
    def fail(request, **kwargs):
        import sqlite3
        from contextlib import closing
        # A different connection must see the committed reservation BEFORE I/O.
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        sent.append(request.get_method())
        raise TimeoutError("synthetic timeout")
    monkeypatch.setattr("jev_uav_track.jev_client._open_request", fail)
    with pytest.raises(JevProtocolError):
        client.choose(state, actions)
    with pytest.raises(BudgetError):
        client.choose(state, actions)
    assert sent == ["POST"]
    assert b"test-only-key" not in path.read_bytes()
    assert client.coverage() == {"status": "INCOMPLETE", "choice_requests": 2,
                                 "valid_choice_responses": 0, "reserved_attempts": 1,
                                 "budget_denials": 1}


def test_unbudgeted_client_cannot_make_any_network_call(monkeypatch):
    from jev_uav_track.jev_client import TypeSafeJevClient
    from jev_uav_track.request_budget import BudgetError
    def forbidden(*args, **kwargs):
        pytest.fail("Unbudgeted request escaped before accounting")
    monkeypatch.setattr("jev_uav_track.jev_client._open_request", forbidden)
    with pytest.raises(BudgetError):
        TypeSafeJevClient("test-only-key").list_models()


def test_redirect_cannot_send_a_second_request_under_one_reservation(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from jev_uav_track.request_budget import RequestBudget
    from jev_uav_track.jev_client import TypeSafeJevClient, JevProtocolError
    from jev_uav_track.rollout import KinematicRolloutModel
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    from test_core import make_state
    hits = []
    state = make_state()
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.end_headers()

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choice": actions[0].id}).encode())

    ledger = tmp_path / "ledger.db"
    make_ledger(ledger)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = TypeSafeJevClient("local-test-only", budget=RequestBudget(ledger),
                                   endpoint=f"http://127.0.0.1:{server.server_port}/first", timeout_s=2)
        with pytest.raises(JevProtocolError):
            client.choose(state, actions)
        assert hits == ["/first"]
        assert client.coverage()["reserved_attempts"] == 1
        assert client.coverage()["status"] == "INCOMPLETE"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("prior", [True, -1, 33, 1.5, "0"])
def test_invalid_reconciled_prior_is_rejected(tmp_path, prior):
    from jev_uav_track.request_budget import BudgetError
    with pytest.raises(BudgetError):
        make_ledger(tmp_path / "ledger.db", prior=prior)
    assert not (tmp_path / "ledger.db").exists()


def test_corrupt_ledger_blocks_network(tmp_path, monkeypatch):
    from jev_uav_track.jev_client import TypeSafeJevClient
    from jev_uav_track.request_budget import BudgetError, RequestBudget
    path = tmp_path / "broken.db"
    path.write_bytes(b"not a SQLite database")
    monkeypatch.setattr("jev_uav_track.jev_client._open_request", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(BudgetError):
        TypeSafeJevClient("dummy", budget=RequestBudget(path)).list_models()


def test_environment_wiring_and_success_accounting(tmp_path, monkeypatch):
    import io
    from jev_uav_track.jev_client import TypeSafeJevClient
    from jev_uav_track.request_budget import BudgetError
    from jev_uav_track.rollout import KinematicRolloutModel
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    from test_core import make_state
    monkeypatch.setenv("JEV_API_KEY", "test-only-key")
    monkeypatch.delenv("JEV_BUDGET_LEDGER", raising=False)
    with pytest.raises(BudgetError):
        TypeSafeJevClient.from_environment()
    path = tmp_path / "ledger.db"
    make_ledger(path)
    monkeypatch.setenv("JEV_BUDGET_LEDGER", str(path))
    monkeypatch.setenv("JEV_BUDGET_BATCH", "one-batch")
    state = make_state()
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    payload = json.dumps({"choice": actions[0].id, "confidence": 0.0}).encode()
    monkeypatch.setattr("jev_uav_track.jev_client._open_request", lambda *a, **k: io.BytesIO(payload))
    client = TypeSafeJevClient.from_environment()
    assert client.choose(state, actions).choice == actions[0].id
    assert client.coverage()["status"] == "COMPLETE"
    assert client.coverage()["reserved_attempts"] == 1
    assert client.budget.batch_id == "one-batch"
    from jev_uav_track import JevTrackPipeline
    result = JevTrackPipeline(client).step(state)
    assert result.used_fallback
    assert client.coverage()["status"] == "COMPLETE"
    assert client.coverage()["valid_choice_responses"] == 2


@pytest.mark.parametrize("script,options", [
    ("compare_jev", ["--max-frames", "3", "--stride", "1"]),
    ("run_closed_loop_replay", ["--max-decisions", "3", "--stride", "1"]),
])
def test_cli_budget_fallback_is_not_reported_as_complete_real_jev(tmp_path, monkeypatch, script, options):
    import importlib.util
    from test_optional_data import make_synthetic_episode
    path = tmp_path / "ledger.db"
    make_ledger(path, prior=32)
    episode = tmp_path / "episode.hdf5"
    make_synthetic_episode(episode)
    output = tmp_path / "report.json"
    monkeypatch.setenv("JEV_API_KEY", "test-only-key")
    monkeypatch.setenv("JEV_BUDGET_LEDGER", str(path))
    # This file must never replace the process environment.
    (tmp_path / ".env").write_text("JEV_API_KEY=forbidden-file-key", encoding="utf-8")
    monkeypatch.setattr("jev_uav_track.jev_client._open_request", lambda *a, **k: pytest.fail("network"))
    source = Path(__file__).resolve().parents[1] / "scripts" / (script + ".py")
    spec = importlib.util.spec_from_file_location(script, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [script, "--episode", str(episode), "--output", str(output), *options])
    module.main()
    report = json.loads(output.read_text())
    assert report["real_error"].startswith("INCOMPLETE_REAL_JEV")
    assert report["real_coverage"]["status"] == "INCOMPLETE"
    assert report["real_coverage"]["budget_denials"] > 0
    assert report["real_coverage"]["reserved_attempts"] == 0
    assert all("real" not in name for name in report["comparisons"])
    import os
    assert os.environ["JEV_API_KEY"] == "test-only-key"
    assert "test-only-key" not in output.read_text()
