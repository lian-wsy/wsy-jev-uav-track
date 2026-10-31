"""These tests catch misread byte spans, unsafe inputs and false readiness."""
from pathlib import Path
import math
import runpy
import struct
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_cooked_uav_body.py'
AUDIT = runpy.run_path(str(SCRIPT))
OWNED_ASSETS = Path('D:/JevUavRuns/20261031_d6_probe01/package/CarlaUE4/Plugins/AirSim/Content')


def test_reads_exact_little_endian_triplets_from_bounded_span():
    raw = b'pad' + struct.pack('<6f', 1, 2, 3, -4, 5, -6) + b'tail'
    assert AUDIT['vectors'](raw, 3, 2) == [(1., 2., 3.), (-4., 5., -6.)]


@pytest.mark.parametrize('offset,count', [(-1, 1), (True, 1), (0, True),
                                         (0, 0), (0, -1), (0, 65537), (1, 2)])
def test_refuses_invalid_or_truncated_vector_spans(offset, count):
    with pytest.raises(ValueError):
        AUDIT['vectors'](bytes(24), offset, count)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_refuses_nonfinite_serialized_geometry(value):
    with pytest.raises(ValueError):
        AUDIT['vectors'](struct.pack('<3f', 1., value, 3.), 0, 1)


def test_summary_includes_negative_extremes_and_origin_radius():
    result = AUDIT['summarize_vertices']([(-2., 4., -4.), (1., -3., 5.)])
    assert result == {'count': 2, 'min': [-2., -3., -4.], 'max': [1., 4., 5.],
                      'radius_about_asset_origin': 6.}


@pytest.mark.parametrize('points', [[], [(1., 2.)], [(1., 2., float('nan'))],
                                   [(True, 1., 2.)]])
def test_refuses_invalid_geometry_summary(points):
    with pytest.raises(ValueError):
        AUDIT['summarize_vertices'](points)


def test_missing_release_files_cannot_produce_evidence():
    with pytest.raises(ValueError, match='asset inventory'):
        AUDIT['audit_asset_bytes']({})


def test_unknown_bytes_cannot_reuse_known_asset_offsets():
    assets = {name: b'x' * size for name, (size, _) in AUDIT['FROZEN_ASSETS'].items()}
    with pytest.raises(ValueError, match='frozen asset'):
        AUDIT['audit_asset_bytes'](assets)


@pytest.mark.skipif(not OWNED_ASSETS.exists(), reason='Exact owned release is local-only')
def test_exact_local_release_extraction_is_read_only_and_not_runtime_certification():
    before = {p: p.stat().st_mtime_ns for p in
              (OWNED_ASSETS / name for name in AUDIT['FROZEN_ASSETS'])}
    result = AUDIT['audit_asset_directory'](OWNED_ASSETS)
    assert result['status'] == 'FROZEN_COOKED_BODY_GEOMETRY_EXTRACTED'
    assert result['blueprint']['body_mesh_export'] == 32
    assert result['blueprint']['mesh_asset'] == '/AirSim/Models/QuadRotor1/Quadrotor1'
    assert result['serialized_convex']['count'] == 32
    assert result['serialized_convex']['min'] == [-50.267356872558594, -50.28471755981445, -19.16818618774414]
    assert result['serialized_convex']['max'] == [50.250003814697266, 50.28471755981445, 12.530989646911621]
    assert [row['count'] for row in result['cooked_convex']] == [32, 32]
    assert result['cooked_triangles']['count'] == 6484
    assert result['cooked_triangles']['triangle_count'] == 12765
    assert result['cooked_triangles']['all_triangle_indices_in_range'] is True
    assert result['build_scale3d_metadata'] == [12., 12., 12.]
    assert result['convex_element_transform'] == {'rotation_xyzw': [0., 0., 0., 1.],
                                                'translation': [0., 0., 0.],
                                                'scale3d': [1., 1., 1.]}
    assert result['maximum_nearest_raw_to_cooked_error_asset_units'] < 0.000008
    assert math.isclose(result['serialized_convex']['radius_about_asset_origin'], 61.48739257961295)
    assert result['control_ready'] is False
    assert result['runtime_collision_envelope_m'] is None
    assert result['new_pass_episodes'] == 0
    assert all(p.stat().st_mtime_ns == stamp for p, stamp in before.items())


def test_cli_preserves_existing_evidence_without_reading_missing_source(tmp_path):
    output = tmp_path / 'report.json'
    output.write_bytes(b'existing evidence')
    result = subprocess.run([sys.executable, str(SCRIPT), '--asset-root', str(tmp_path / 'missing'),
                             '--output', str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    assert output.read_bytes() == b'existing evidence'
    assert 'output already exists' in result.stderr
