import copy
import importlib.util
import json
from pathlib import Path
import pytest


def verifier():
    path = Path(__file__).resolve().parents[1] / 'scripts/check_live_belief_evidence.py'
    spec = importlib.util.spec_from_file_location('belief_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence():
    path = Path(__file__).resolve().parents[1] / 'docs/experiments/evidence/live_belief_20261028/belief03.json'
    return json.loads(path.read_text(encoding='utf-8'))


def test_recorded_live_inputs_reproduce_beliefs_rollouts_and_safety_rejections():
    result = verifier().verify(evidence(), strict_text=False)
    assert result['verified_frames'] == 16
    assert result['visible_frames'] == 12
    assert result['missing_frames'] == 4
    assert result['verified_candidate_predictions'] == 128
    assert result['verified_safety_rejections'] == 32
    if result['text_differences']:
        assert result['status'] == 'NUMERIC_ONLY_TEXT_MISMATCH'
        with pytest.raises(ValueError, match='original Python runtime'):
            verifier().verify(evidence())
    else:
        assert result['status'] == 'PASS'


@pytest.mark.parametrize('mutation', ['missing_recovery', 'hidden_truth', 'state', 'candidate', 'cleanup', 'queries', 'small_error', 'nan', 'actor_id'])
def test_replay_does_not_accept_partial_sequence_or_tampered_results(mutation):
    data = copy.deepcopy(evidence())
    probe = data['api_probe']['contract']['belief_contract']
    if mutation == 'missing_recovery':
        probe['frames'] = probe['frames'][:8]
    elif mutation == 'hidden_truth':
        probe['frames'][4]['target_measurement_world'] = [1, 2, 3]
    elif mutation == 'state':
        probe['frames'][3]['state']['target']['range_m'] += 1
    elif mutation == 'candidate':
        probe['frames'][1]['candidates'][0]['outcome']['visibility_probability'] = .123
    elif mutation == 'cleanup':
        data['controller']['cleanup_confirmed'] = False
    elif mutation == 'small_error':
        probe['frames'][1]['candidates'][0]['outcome']['range_error_m'] += 1e-6
    elif mutation == 'nan':
        probe['frames'][1]['candidates'][0]['outcome']['range_error_m'] = float('nan')
    elif mutation == 'actor_id':
        probe['frames'][0]['state']['environment']['target_actor_id'] += 1e-11
    else:
        probe['target_pose_queries'] += 1
    with pytest.raises(ValueError):
        verifier().verify(data, strict_text=False)


def test_float_comparison_allows_only_roundoff_not_discrete_changes():
    equal = verifier().numerically_equal
    assert equal({'range': 5.96147249528306}, {'range': 5.961472495283061})
    assert not equal({'visible': True}, {'visible': 1})
    assert not equal([float('nan')], [float('nan')])
    assert not equal([1], [1.000001])
    assert not equal([113], [113.00000000001])
    assert not equal([113.00000000001], [113])
