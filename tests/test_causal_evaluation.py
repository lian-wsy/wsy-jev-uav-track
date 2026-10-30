"""A causal model must not silently regain hidden truth during evaluation."""
from dataclasses import replace

import pytest

from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.training import RolloutTrainingConfig, build_training_dataset, fit_outcome_model
from test_causal_training import episode_fixture


def causal_model(episode):
    cfg = RolloutTrainingConfig(stride=1, input_mode='causal_visible')
    return fit_outcome_model(build_training_dataset(episode, cfg), cfg)[0]


def test_causal_model_does_not_invent_safety_telemetry_in_evaluation(tmp_path):
    episode = episode_fixture(tmp_path)
    report = evaluate_episode(episode, OfflineEvaluationConfig(max_frames=4),
                              outcome_model=causal_model(episode))
    assert report['metrics']['evaluated_frames'] == 0
    assert report['metrics']['skipped_frames'] == 3
    assert report['input_mode'] == 'causal_visible'
    assert [r['frame_index'] for r in report['blocked_records']] == [0, 5, 10]
    assert all('battery_percent' in r['missing_safety_measurements']
               for r in report['blocked_records'])


def test_hidden_or_future_truth_does_not_change_causal_decisions_or_history(tmp_path):
    episode = episode_fixture(tmp_path)
    changed = replace(episode, target_positions=episode.target_positions.copy(),
                      target_velocities=episode.target_velocities.copy(),
                      target_ranges_m=episode.target_ranges_m.copy(),
                      target_actor_ids=episode.target_actor_ids.copy())
    changed.target_positions[[0, 1, 4, 5]] += 1000
    changed.target_ranges_m[[0, 1, 4, 5]] += 2000
    changed.target_actor_ids[[0, 1, 4, 5]] = 999
    changed.target_positions[8:] += 4000
    changed.target_velocities[:] = -3000
    # Only this offline ablation removes the gate to inspect selector inputs.
    cfg = OfflineEvaluationConfig(start_frame=4, end_frame=8, stride=1,
                                  enable_safety_gate=False)
    model = causal_model(episode)
    left, right = [evaluate_episode(e, cfg, outcome_model=model) for e in (episode, changed)]
    fields = ['choice', 'predicted_visibility', 'predicted_range_error_m',
              'identity_confidence', 'history_identity_consistency',
              'history_target_acceleration_mps2', 'history_occlusion_fraction']
    assert len(left['records']) == 4
    for a, b in zip(left['records'], right['records']):
        assert {k: a[k] for k in fields} == {k: b[k] for k in fields}
    assert left['records'][0]['history_length'] == 5


def test_sparse_evaluation_preserves_all_preceding_observations(tmp_path):
    episode = episode_fixture(tmp_path)
    model = causal_model(episode)
    cfg = OfflineEvaluationConfig(end_frame=8, stride=1, enable_safety_gate=False)
    dense = evaluate_episode(episode, cfg, outcome_model=model)
    sparse = evaluate_episode(episode, replace(cfg, start_frame=4, stride=3), outcome_model=model)
    for sparse_row, dense_row in zip(sparse['records'], [dense['records'][4], dense['records'][7]]):
        for key in ('history_length', 'history_target_acceleration_mps2',
                    'history_identity_consistency', 'predicted_range_error_m'):
            assert sparse_row[key] == dense_row[key]


def test_missing_visibility_is_not_assumed_visible_for_causal_model(tmp_path):
    episode = episode_fixture(tmp_path)
    model = causal_model(episode)
    episode.metadata['visibility_observed'] = False
    with pytest.raises(ValueError, match='Causal training requires'):
        evaluate_episode(episode, outcome_model=model)


def test_explicit_oracle_evaluation_cannot_mislabel_causal_model(tmp_path):
    episode = episode_fixture(tmp_path)
    with pytest.raises(ValueError, match='input_mode'):
        evaluate_episode(episode, OfflineEvaluationConfig(input_mode='oracle'),
                         outcome_model=causal_model(episode))


def test_conflicting_model_input_modes_are_rejected(tmp_path):
    episode = episode_fixture(tmp_path)
    model = causal_model(episode)
    model.training_summary['config']['input_mode'] = 'oracle'
    with pytest.raises(ValueError, match='input_mode'):
        evaluate_episode(episode, outcome_model=model)
