import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from jev_uav_track import request_budget as budgets


def receipt(tmp_path, **overrides):
    path = tmp_path / 'authorization.json'
    data = {'status': 'AUTHORIZED_NEW_PILOT_SCOPE',
            'scope': 'jev-uav-outcome-pilot-20261030',
            'historical_preparation_attempts': 'UNKNOWN', 'request_limit': 8,
            'evidence': 'Synthetic test authorization; no real account or calls.'}
    data.update(overrides)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def initialize(tmp_path):
    assert hasattr(budgets, 'initialize_pilot_ledger'), 'Explicit pilot authorization is missing'
    path = tmp_path / 'pilot.db'
    budgets.initialize_pilot_ledger(path, receipt(tmp_path))
    return path


def test_pilot_is_eight_total_across_batches_and_restarts_not_eight_per_batch(tmp_path):
    path = initialize(tmp_path)
    for i in range(8):
        assert budgets.RequestBudget(path, batch_id=str(i), scope=budgets.PILOT_SCOPE).reserve('choice') == i + 1
    with pytest.raises(budgets.BudgetError, match='total limit'):
        budgets.RequestBudget(path, scope=budgets.PILOT_SCOPE).reserve('models')


def test_old_callers_cannot_silently_use_new_authorization(tmp_path):
    path = initialize(tmp_path)
    with pytest.raises(budgets.BudgetError):
        budgets.RequestBudget(path).reserve('choice')
    with pytest.raises(budgets.BudgetError):
        budgets.initialize_preparation_ledger(tmp_path / 'old.db', receipt(tmp_path))
    assert not (tmp_path / 'old.db').exists()


@pytest.mark.parametrize('change', [
    {'status': 'RECONCILED'}, {'scope': 'unlimited'}, {'request_limit': 32},
    {'request_limit': True}, {'historical_preparation_attempts': 0}, {'evidence': ''},
    {'prior_attempts': 0},
])
def test_invalid_or_falsely_reconciled_authorization_is_rejected(tmp_path, change):
    assert hasattr(budgets, 'initialize_pilot_ledger'), 'Explicit pilot authorization is missing'
    with pytest.raises(budgets.BudgetError):
        budgets.initialize_pilot_ledger(tmp_path / 'pilot.db', receipt(tmp_path, **change))
    assert not (tmp_path / 'pilot.db').exists()


def test_pilot_ledger_cannot_be_reset_or_scope_switched(tmp_path):
    path = initialize(tmp_path)
    budgets.RequestBudget(path, scope=budgets.PILOT_SCOPE).reserve('choice')
    before = path.read_bytes()
    with pytest.raises(budgets.BudgetError):
        budgets.initialize_pilot_ledger(path, receipt(tmp_path))
    assert path.read_bytes() == before
    with pytest.raises(budgets.BudgetError):
        budgets.RequestBudget(path, scope='unknown')


def test_concurrent_clients_share_exactly_eight_attempts(tmp_path):
    path = initialize(tmp_path)
    def attempt(i):
        try:
            budgets.RequestBudget(path, batch_id=str(i), scope=budgets.PILOT_SCOPE).reserve('choice')
            return True
        except budgets.BudgetError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(12))) == 8


def test_environment_client_explicitly_selects_pilot_scope(tmp_path, monkeypatch):
    from jev_uav_track.jev_client import TypeSafeJevClient
    path = initialize(tmp_path)
    monkeypatch.setenv('JEV_API_KEY', 'synthetic-not-a-real-key')
    monkeypatch.setenv('JEV_BUDGET_LEDGER', str(path))
    monkeypatch.delenv('JEV_BUDGET_SCOPE', raising=False)
    with pytest.raises(budgets.BudgetError):
        TypeSafeJevClient.from_environment()._reserve('choice')
    monkeypatch.setenv('JEV_BUDGET_SCOPE', budgets.PILOT_SCOPE)
    client = TypeSafeJevClient.from_environment()
    client._reserve('models')
    assert client.reserved_attempts == 1


def test_discovery_failures_and_retries_share_eight_before_transport(tmp_path, monkeypatch):
    from jev_uav_track.jev_client import TypeSafeJevClient, JevProtocolError
    from test_core import make_state
    from jev_uav_track.primitives import DEFAULT_PRIMITIVES
    from jev_uav_track.rollout import KinematicRolloutModel
    path = initialize(tmp_path)
    monkeypatch.setenv('JEV_API_KEY', 'synthetic-not-a-real-key')
    monkeypatch.setenv('JEV_BUDGET_LEDGER', str(path))
    monkeypatch.setenv('JEV_BUDGET_SCOPE', budgets.PILOT_SCOPE)
    sent = []
    def fail(request, **kwargs):
        sent.append(request.get_method())
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr('jev_uav_track.jev_client._open_request', fail)
    state = make_state()
    actions = KinematicRolloutModel().generate(DEFAULT_PRIMITIVES, state)
    for i in range(8):
        client = TypeSafeJevClient.from_environment()
        with pytest.raises(JevProtocolError):
            client.list_models() if i < 2 else client.choose(state, actions)
    with pytest.raises(budgets.BudgetError):
        TypeSafeJevClient.from_environment().choose(state, actions)
    assert sent == ['GET', 'GET'] + ['POST'] * 6


def test_new_scope_rejects_old_ledger_and_corrupt_prior_count(tmp_path):
    import sqlite3
    from test_jev_request_budget import make_ledger
    old = tmp_path / 'old.db'
    make_ledger(old)
    with pytest.raises(budgets.BudgetError):
        budgets.RequestBudget(old, scope=budgets.PILOT_SCOPE).reserve('choice')
    path = initialize(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE metadata SET prior=1')
    with pytest.raises(budgets.BudgetError):
        budgets.RequestBudget(path, scope=budgets.PILOT_SCOPE).reserve('choice')
