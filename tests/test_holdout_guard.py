"""Reserved-map contamination regressions using metadata and synthetic HDF only."""
import json
from pathlib import Path
from dataclasses import replace

import pytest

from jev_uav_track.split_integrity import validate_split_integrity
from jev_uav_track.training import (
    build_training_dataset, build_training_dataset_from_episodes,
    fit_outcome_model_cross_episode, fit_outcome_model,
)


@pytest.fixture
def episode(tmp_path):
    from test_optional_data import make_synthetic_episode
    from jev_uav_track.data import HDF5Episode
    path = tmp_path / 'synthetic.hdf5'
    make_synthetic_episode(path)
    return HDF5Episode.open(path, load_images=False)


@pytest.mark.parametrize('path,metadata', [
    ('Town03_seed1.hdf5', {}),
    ('folder/TOWN3/sample.hdf5', {}),
    (r'C:\data\Town03_Opt\copy.hdf5', {}),
    ('renamed.hdf5', {'town': 'Town03'}),
    ('renamed.hdf5', {'map_name': '/Game/Carla/Maps/Town03_Opt'}),
    ('renamed.hdf5', {'episode_metadata': {'scenario': {'map': 'Town3'}}}),
    ('renamed.hdf5', {'episode_metadata_json': '{"town": "Town03"}'}),
    ('Town02_seed1.hdf5', {'town': 'Town03'}),
])
def test_reserved_episode_rejected_before_accessing_state(episode, path, metadata):
    episode = replace(episode, path=Path(path), metadata={**episode.metadata, **metadata})
    with pytest.raises(ValueError, match='reserved.*Town03'):
        build_training_dataset(episode)


def split_entry(**changes):
    return {'path': 'renamed.hdf5', 'seed': 1, 'sha256': 'a' * 64,
            'validation_status': 'PASS', 'split': 'train', 'town': 'Town03', **changes}


@pytest.mark.parametrize('split', ['train', 'validation', 'development_stress', 'calibration', None])
def test_reserved_map_cannot_be_assigned_a_development_or_unknown_role(split):
    with pytest.raises(ValueError, match='reserved.*Town03'):
        validate_split_integrity([split_entry(split=split)])


def test_reserved_map_final_test_manifest_remains_auditable():
    validate_split_integrity([split_entry(split='final_test')])


@pytest.mark.parametrize('town', ['Town02', 'Town30', 'Town031', 'Downtown03'])
def test_unreserved_maps_are_not_rejected_by_substring(town):
    validate_split_integrity([split_entry(town=town, path=f'{town}_seed1.hdf5')])


def test_bulk_and_cross_episode_preflight_all_inputs_before_state_access(episode):
    dev = replace(episode, path=Path('Town02.hdf5'))
    heldout = replace(episode, path=Path('Town03.hdf5'))
    with pytest.raises(ValueError, match='reserved.*Town03'):
        build_training_dataset_from_episodes([dev, heldout])
    with pytest.raises(ValueError, match='reserved.*Town03'):
        fit_outcome_model_cross_episode([dev], [heldout])


def test_prebuilt_dataset_with_reserved_provenance_cannot_be_fitted(episode):
    dataset = replace(build_training_dataset(episode), episode_paths=('Town03_seed1.hdf5',))
    with pytest.raises(ValueError, match='reserved.*Town03'):
        fit_outcome_model(dataset)


def test_adapter_can_audit_reserved_hdf_but_training_rejects_metadata(tmp_path):
    h5py = pytest.importorskip('h5py')
    from test_optional_data import make_synthetic_episode
    from jev_uav_track.data import HDF5Episode
    path = tmp_path / 'renamed.hdf5'
    make_synthetic_episode(path)
    with h5py.File(path, 'r+') as h5:
        h5.attrs['episode_metadata_json'] = json.dumps({'scenario': {'town': 'Town03'}})
    episode = HDF5Episode.open(path, load_images=False)
    assert episode.frame_count == 12
    assert episode.images is None
    with pytest.raises(ValueError, match='reserved.*Town03'):
        build_training_dataset(episode)


def test_d4_refuses_reserved_training_manifest_before_open_or_output(tmp_path):
    from scripts.run_d4_rollout import run
    manifest = tmp_path / 'split.json'
    manifest.write_text(json.dumps({'episodes': [split_entry(path=str(tmp_path / 'missing.hdf5'))]}))
    with pytest.raises(ValueError, match='reserved.*Town03'):
        run(manifest, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('script', ['single', 'cross', 'ablation'])
def test_training_cli_rejects_reserved_path_before_file_access(tmp_path, monkeypatch, script):
    from scripts import train_rollout_model, train_cross_episode, run_ablation_report
    output = tmp_path / 'out.npz'
    path = tmp_path / 'Town03_missing.hdf5'
    if script == 'single':
        args = ['train', '--episode', str(path), '--output', str(output)]
        main = train_rollout_model.main
    else:
        args = ['train', '--train-episode', str(tmp_path / 'also_missing.hdf5'),
                '--validation-episode', str(path), '--output', str(output)]
        main = train_cross_episode.main if script == 'cross' else run_ablation_report.main
    monkeypatch.setattr('sys.argv', args)
    with pytest.raises(ValueError, match='reserved.*Town03'):
        main()
    assert not output.exists()


def test_development_extension_rejects_reserved_candidate_instead_of_selecting_it():
    from scripts.extend_development_split import select_additions
    row = {'path': 'root/quota_grid_20261004_formal/Town03_walker_seed1.hdf5',
           'validation_status': 'PASS'}
    with pytest.raises(ValueError, match='reserved.*Town03'):
        select_additions([row], excluded_seeds=set(), limit=1)


def test_old_split_builder_rejects_reserved_development_entry():
    from scripts.freeze_experiment_split import _entry
    with pytest.raises(ValueError, match='reserved.*Town03'):
        _entry({'path': 'Town03_seed1.hdf5', 'validation_status': 'PASS', 'schema': 'synthetic'},
               split='validation', source='synthetic')


def test_development_loader_rejects_renamed_metadata_and_accepts_dev(tmp_path):
    import hashlib
    import h5py
    from test_optional_data import make_synthetic_episode
    from jev_uav_track.training import load_development_episode
    from scripts.run_d4_rollout import load_verified
    path = tmp_path / 'renamed.hdf5'
    make_synthetic_episode(path)
    assert load_development_episode(path).frame_count == 12
    with h5py.File(path, 'r+') as h5:
        h5.attrs['map_name'] = '/Game/Carla/Maps/Town03'
    for loader in (lambda: load_development_episode(path),
                   lambda: load_verified({'path': str(path), 'bytes': path.stat().st_size,
                                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, 1000)):
        with pytest.raises(ValueError, match='reserved.*Town03'):
            loader()


def test_link_alias_resolves_reserved_map_identity(tmp_path):
    from jev_uav_track.holdout import require_development_source
    target = tmp_path / 'Town03_source.hdf5'
    target.touch()
    alias = tmp_path / 'renamed.hdf5'
    try:
        alias.symlink_to(target)
    except OSError as exc:
        pytest.skip(f'Host does not permit test symlinks: {exc}')
    with pytest.raises(ValueError, match='reserved.*Town03'):
        require_development_source(alias)


def test_malformed_embedded_metadata_is_not_silently_accepted():
    from jev_uav_track.holdout import require_development_source
    with pytest.raises(ValueError, match='Malformed episode metadata'):
        require_development_source('renamed.hdf5', {'episode_metadata_json': '{broken'})


def test_descriptive_notes_are_not_treated_as_map_identity():
    from jev_uav_track.holdout import require_development_source
    require_development_source('Town02_seed1.hdf5', {'town': 'Town02',
                               'note': 'Town03 is reserved; this is development'})


def test_baseline_ablation_cannot_score_reserved_validation_without_fitting(episode, tmp_path):
    from scripts.run_ablation_report import _run_ablation
    from jev_uav_track.training import RolloutTrainingConfig
    from jev_uav_track.evaluation import OfflineEvaluationConfig
    heldout = replace(episode, metadata={**episode.metadata, 'town': 'Town03'})
    with pytest.raises(ValueError, match='reserved.*Town03'):
        _run_ablation('kinematic_baseline', [episode], [heldout],
                      RolloutTrainingConfig(), 3, OfflineEvaluationConfig(max_frames=1), tmp_path)


@pytest.mark.parametrize('reserved_group', ['train', 'validation'])
def test_recorded_comparison_preserves_manifest_identity_before_any_file_read(tmp_path, monkeypatch, reserved_group):
    from scripts.compare_recorded_rollouts import main
    manifest = tmp_path / 'split.json'
    manifest.write_text(json.dumps({'episodes': [
        split_entry(path=str(tmp_path / 'missing_train.hdf5'),
                    town='Town03' if reserved_group == 'train' else 'Town02'),
        split_entry(path=str(tmp_path / 'missing_val.hdf5'), split='validation',
                    town='Town03' if reserved_group == 'validation' else 'Town02')]}))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['compare', '--split', str(manifest), '--output', str(output)])
    with pytest.raises(ValueError, match='reserved.*Town03'):
        main()
    assert not output.exists()
