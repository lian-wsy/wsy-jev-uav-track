from pathlib import Path

import pytest


def test_runtime_cannot_be_shared_package_or_parent(tmp_path):
    from jev_uav_track.isolated_runtime import validate_roots
    source = tmp_path / 'package'
    source.mkdir()
    for unsafe in (source, source / 'run', tmp_path):
        with pytest.raises(ValueError):
            validate_roots(source, unsafe)
    validate_roots(source, tmp_path / 'task')


def test_settings_fallbacks_and_arguments_stay_inside_task(tmp_path):
    from jev_uav_track.isolated_runtime import write_settings, launch_arguments
    task = tmp_path / 'task with space'
    binary = task / 'package/CarlaUE4/Binaries/Win64/CarlaUE4-Win64-Shipping.exe'
    binary.parent.mkdir(parents=True)
    settings = write_settings(task)
    import json
    data = json.loads(settings.read_text())
    assert data['ApiServerPort'] == 42451
    assert data['LocalHostIp'] == '127.0.0.1'
    assert data['Recording']['Enabled'] is False
    assert Path(data['Recording']['Folder']).is_relative_to(task)
    for fallback in (binary.parent / 'settings.json', task / 'package/settings.json'):
        assert fallback.read_bytes() == settings.read_bytes()
    args = launch_arguments(task)
    assert args[-2:] == ['--settings', str(settings)]
    assert str(binary) == args[0]
    assert '-carla-rpc-port=3100' in args
    assert not any('CarlaAir.ps1' in arg for arg in args)


def test_run_output_cannot_escape_runtime(tmp_path):
    from jev_uav_track.isolated_runtime import launch_arguments
    with pytest.raises(ValueError):
        launch_arguments(tmp_path / 'task', tmp_path / 'external')


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows process ownership')
def test_closing_owner_job_terminates_its_live_process():
    import subprocess
    import sys
    from jev_uav_track.windows_job import OwnedJob
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job = OwnedJob()
        job.assign(process)
        assert process.poll() is None
        job.close()
        process.wait(timeout=5)
        assert process.returncode is not None
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows preflight')
def test_powershell_query_failure_cannot_masquerade_as_empty_state():
    import importlib.util
    import subprocess
    source = Path(__file__).resolve().parents[1] / 'scripts/probe_isolated_carla.py'
    spec = importlib.util.spec_from_file_location('probe_isolated_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(subprocess.CalledProcessError):
        module.ps_json("Write-Error 'synthetic query failure'; @{processes=@(); listeners=@()} | ConvertTo-Json")


def test_rpc_rejection_and_transport_failure_are_not_completion():
    from jev_uav_track.isolated_runtime import require_rpc_success

    class Rejected:
        def get(self):
            return False

    class Failed:
        def get(self):
            raise TimeoutError('RPC timeout')

    with pytest.raises(RuntimeError):
        require_rpc_success(Rejected())
    with pytest.raises(TimeoutError):
        require_rpc_success(Failed())


def test_command_cadence_skips_missed_slots_and_brakes_on_error():
    from jev_uav_track.isolated_runtime import command_cadence
    elapsed, emitted, brakes = [0.0], [], []

    def sleep(seconds):
        elapsed[0] += seconds

    def command(duration):
        emitted.append((elapsed[0], duration))
        elapsed[0] += 0.16  # Slower than three 50 ms slots: no catch-up burst.

    result = {}
    command_cadence(command, lambda: brakes.append(elapsed[0]), result,
                    clock=lambda: elapsed[0], sleep=sleep)
    assert len(emitted) == 7
    assert all(b[0] - a[0] >= 0.16 - 1e-9 for a, b in zip(emitted, emitted[1:]))
    assert all(start + duration <= 1.0 for start, duration in emitted)
    assert result['skipped_slots'] > 0
    assert len(brakes) == 1

    def failing(duration):
        raise TimeoutError('RPC timeout')

    result = {}
    with pytest.raises(TimeoutError):
        command_cadence(failing, lambda: brakes.append(elapsed[0]), result,
                        clock=lambda: elapsed[0], sleep=sleep)
    assert len(brakes) == 2
    assert result['commands'][0]['acknowledged'] is False
