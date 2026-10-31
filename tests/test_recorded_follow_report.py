"""No output may be written into the video runtime; failures stay in inventory."""
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from test_recorded_follow_metrics import fixture


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/evaluate_recorded_follow.py'


def test_report_reads_terminal_telemetry_without_rewriting_source(tmp_path):
    assert SCRIPT.exists(), 'Read-only follow report entrypoint missing'
    root = tmp_path / 'video/runs'; run = root / 'continuous01_20261030'; run.mkdir(parents=True)
    raw = fixture()
    files = {'tracking_result.json': raw,
             'smoke_result.json': {'status': 'API_SMOKE_COMPLETED', 'cleanup_confirmed': True,
                                   'protected_unchanged': True},
             'continuous_prefixes.json': raw['prefixes'], 'continuous_advice.json': raw['decisions']}
    for name, data in files.items(): (run/name).write_text(json.dumps(data))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run.iterdir()}
    output = tmp_path / 'ours/report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--run-root', str(root), '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert len(data['runs']) == 7
    assert data['runs'][0]['metrics']['frames'] == 5
    assert data['runs'][0]['source_snapshot_status'] == 'NOT_AVAILABLE'
    assert data['runs'][1]['status'] == 'RUN_MISSING'
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in before.items())


def test_report_refuses_output_in_protected_runtime_before_reading_inputs(tmp_path):
    assert SCRIPT.exists(), 'Read-only follow report entrypoint missing'
    root = tmp_path / 'video/runs'; root.mkdir(parents=True)
    output = tmp_path / 'video/report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--run-root', str(root), '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert not output.exists()
    assert 'read-only runtime' in result.stderr


def test_report_refuses_to_overwrite_existing_evidence(tmp_path):
    assert SCRIPT.exists(), 'Read-only follow report entrypoint missing'
    output = tmp_path / 'ours.json'; output.write_text('preserve')
    result = subprocess.run([sys.executable, str(SCRIPT), '--run-root', str(tmp_path/'video/runs'), '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and output.read_text() == 'preserve'


@pytest.mark.parametrize('broken', ['receipt', 'tracking', 'sources'])
def test_malformed_attempt_stays_in_inventory_without_dropping_other_runs(tmp_path, broken):
    root = tmp_path / 'video/runs'; run = root / 'continuous01_20261030'; run.mkdir(parents=True)
    receipt = {'cleanup_confirmed': True, 'status': 'API_SMOKE_COMPLETED'}
    tracking = fixture()
    if broken == 'receipt': receipt = []
    if broken == 'tracking': tracking = []
    if broken == 'sources': tracking['source_sha256'] = []
    (run/'smoke_result.json').write_text(json.dumps(receipt))
    (run/'tracking_result.json').write_text(json.dumps(tracking))
    output = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--run-root', str(root), '--output', str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert len(data['runs']) == 7
    assert data['runs'][0]['status'] == 'READ_OR_SCHEMA_ERROR'
    assert data['runs'][1]['status'] == 'RUN_MISSING'
