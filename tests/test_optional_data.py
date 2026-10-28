from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

np = pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.learned_rollout import LearnedOutcomeRolloutModel
from jev_uav_track.primitives import DEFAULT_PRIMITIVES
from jev_uav_track.training import (
    RolloutTrainingConfig,
    LinearOutcomeModel,
    build_training_dataset,
    build_training_dataset_from_episodes,
    fit_outcome_model,
    fit_outcome_model_cross_episode,
    feature_mask_for_ablation,
)


def make_synthetic_episode(path):
    frame_count = 12
    state_count = 24
    image_timestamps = np.arange(frame_count, dtype=np.float64) * 0.2
    state_timestamps = np.arange(state_count, dtype=np.float64) * 0.1
    positions = np.column_stack(
        [np.linspace(0.0, 3.0, state_count), np.zeros(state_count), np.ones(state_count)]
    ).astype(np.float32)
    camera_positions = np.column_stack(
        [np.zeros(state_count), np.zeros(state_count), np.full(state_count, 5.0)]
    ).astype(np.float32)
    state_indices = np.arange(0, state_count, 2, dtype=np.int64)
    with h5py.File(path, "w") as h5:
        h5.attrs["schema"] = "synthetic-test"
        h5.attrs["validation_status"] = "PASS"
        h5.attrs["minimum_static_clearance_m"] = 6.0
        h5.create_dataset("observation/images/cam_high", data=np.zeros((frame_count, 4, 5, 3), dtype=np.uint8))
        h5.create_dataset("state/positions", data=positions)
        h5.create_dataset("action/camera_positions", data=camera_positions)
        h5.create_dataset("timestamp/image_timestamps", data=image_timestamps)
        h5.create_dataset("timestamp/position_timestamps", data=state_timestamps)
        h5.create_dataset("image_state/state_index", data=state_indices)
        h5.create_dataset("image_state/actor_ids", data=np.full(frame_count, 17, dtype=np.int32))
        h5.create_dataset("visibility/target_visible", data=np.ones(frame_count, dtype=bool))
        h5.create_dataset("visibility/target_in_frame", data=np.ones(frame_count, dtype=bool))
        h5.create_dataset("visibility/target_occluded", data=np.zeros(frame_count, dtype=bool))
        h5.create_dataset("visibility/target_pixel_height", data=np.full(frame_count, 30.0, dtype=np.float32))
        h5.create_dataset("action/camera_target_distance", data=np.full(frame_count, 5.0, dtype=np.float32))


@pytest.mark.parametrize("fault", ["future", "stale", "missing_time", "missing_index", "nan", "duplicate", "fractional_index"])
def test_adapter_rejects_unproven_time_alignment(tmp_path, fault):
    from jev_uav_track.data.hdf5_adapter import HDF5SchemaError
    path = tmp_path / "bad_time.hdf5"
    make_synthetic_episode(path)
    with h5py.File(path, "r+") as h5:
        if fault == "missing_time":
            del h5["timestamp/image_timestamps"]
        elif fault == "missing_index":
            del h5["image_state/state_index"]
        elif fault == "future":
            h5["image_state/state_index"][0] = 1
        elif fault == "stale":
            h5["image_state/state_index"][1] = 1
        elif fault == "fractional_index":
            values = h5["image_state/state_index"][:].astype(float)
            values[0] = 0.5
            del h5["image_state/state_index"]
            h5.create_dataset("image_state/state_index", data=values)
        else:
            h5["timestamp/image_timestamps"][1] = np.nan if fault == "nan" else 0
    with pytest.raises(HDF5SchemaError):
        HDF5Episode.open(path)


def test_camera_yaw_uses_recorded_rotation_not_target_bearing(tmp_path):
    path = tmp_path / "yaw.hdf5"
    make_synthetic_episode(path)
    with h5py.File(path, "r+") as h5:
        h5.create_dataset("action/camera_rotations", data=np.tile([11, 37, 82], (24, 1)))
    assert HDF5Episode.open(path).tracking_state(2).uav.yaw_deg == 37


def test_hdf5_adapter_and_training_round_trip(tmp_path) -> None:
    episode_path = tmp_path / "episode.hdf5"
    make_synthetic_episode(episode_path)
    episode = HDF5Episode.open(episode_path)
    assert episode.frame_count == 12
    assert episode.images is None
    assert episode.tracking_state(0).environment["target_actor_id"] == 17

    config = RolloutTrainingConfig(horizon_frames=2, stride=1, max_samples=8)
    dataset = build_training_dataset(episode, config)
    assert dataset.resolved_rollout_horizon_s == pytest.approx(0.4)
    model, metrics = fit_outcome_model(dataset, config)
    assert metrics["sample_count"] == 8
    model_path = tmp_path / "model.npz"
    model.save(model_path)
    loaded = LinearOutcomeModel.load(model_path)
    prediction = loaded.predict_state(episode.tracking_state(0), (0.0, 0.0, 0.0))
    assert set(prediction) == {
        "visibility_probability",
        "image_center_error",
        "range_error_m",
        "covariance_trace",
    }

    masked_model, _ = fit_outcome_model(
        dataset,
        config,
        feature_mask=feature_mask_for_ablation("no_action"),
    )
    masked_path = tmp_path / "masked_model.npz"
    masked_model.save(masked_path)
    masked_loaded = LinearOutcomeModel.load(masked_path)
    assert not any(masked_loaded.input_mask[-4:])

    report = evaluate_episode(
        episode,
        OfflineEvaluationConfig(max_frames=4, stride=2),
        outcome_model=loaded,
    )
    json.dumps(report)
    assert report["metrics"]["evaluated_frames"] == 4
    assert report["records"][0]["history_length"] == 1

    module_report = evaluate_episode(
        episode,
        OfflineEvaluationConfig(
            max_frames=4,
            stride=2,
            minimum_obstacle_margin_m=6.5,
            enable_safety_gate=False,
            use_short_history=False,
            use_identity_confidence=False,
        ),
        outcome_model=loaded,
    )
    assert module_report["metrics"]["evaluated_frames"] == 4
    assert module_report["metrics"]["unsafe_selected_rate"] is not None
    assert all(record["identity_confidence"] == 0.5 for record in module_report["records"])
    assert all(record["history_enabled"] is False for record in module_report["records"])
    assert all(record["history_length"] == 0 for record in module_report["records"])

    guarded = LearnedOutcomeRolloutModel(
        loaded,
        max_feature_zscore=1e-6,
    )
    guarded_action = guarded.simulate(DEFAULT_PRIMITIVES[0], episode.tracking_state(0))
    assert guarded_action.metadata["learned_model_ood"] is True
    assert guarded_action.metadata["model"] == "kinematic-rollout-ood-fallback"

    blended = LearnedOutcomeRolloutModel(loaded, prediction_blend=0.5)
    blended_action = blended.simulate(DEFAULT_PRIMITIVES[0], episode.tracking_state(0))
    assert blended_action.metadata["prediction_blend"] == 0.5

    state_guard = LearnedOutcomeRolloutModel(
        loaded,
        max_state_feature_zscore=1e-6,
    )
    state_guard_action = state_guard.simulate(DEFAULT_PRIMITIVES[0], episode.tracking_state(0))
    assert state_guard_action.metadata["learned_model_ood_reason"] == "state_feature_zscore"

    residual_config = RolloutTrainingConfig(
        horizon_frames=2,
        stride=1,
        max_samples=8,
        target_mode="baseline_residual",
    )
    residual_dataset = build_training_dataset(episode, residual_config)
    assert residual_dataset.target_mode == "baseline_residual"
    inferred_residual_model, _ = fit_outcome_model(residual_dataset)
    assert inferred_residual_model.target_mode == "baseline_residual"
    with pytest.raises(ValueError, match="does not match dataset"):
        fit_outcome_model(residual_dataset, RolloutTrainingConfig(horizon_frames=2))
    residual_model, _ = fit_outcome_model(residual_dataset, residual_config)
    assert residual_model.target_mode == "baseline_residual"
    residual_path = tmp_path / "residual_model.npz"
    residual_model.save(residual_path)
    residual_loaded = LinearOutcomeModel.load(residual_path)
    assert residual_loaded.target_mode == "baseline_residual"
    residual_action = LearnedOutcomeRolloutModel(residual_loaded).simulate(
        DEFAULT_PRIMITIVES[0], episode.tracking_state(0)
    )
    assert residual_action.metadata["prediction_target_mode"] == "baseline_residual"
    with pytest.raises(ValueError, match="target_mode"):
        build_training_dataset(
            episode,
            RolloutTrainingConfig(horizon_frames=2, target_mode="invalid"),
        )


@pytest.mark.parametrize("mode", ["absolute", "baseline_residual"])
def test_saved_model_controls_inference_duration(tmp_path, mode):
    from jev_uav_track.rollout import KinematicRolloutModel, RolloutConfig
    path = tmp_path / "timing.hdf5"
    make_synthetic_episode(path)
    episode = HDF5Episode.open(path)
    config = RolloutTrainingConfig(horizon_frames=3, target_mode=mode)
    model, _ = fit_outcome_model(build_training_dataset(episode, config), config)
    model.save(tmp_path / "timing.npz")
    loaded = LinearOutcomeModel.load(tmp_path / "timing.npz")
    action = LearnedOutcomeRolloutModel(loaded).simulate(
        DEFAULT_PRIMITIVES[1], episode.tracking_state(0)
    )
    assert action.duration_s == pytest.approx(0.6)
    assert action.trajectory[-1].time_s == pytest.approx(0.6)
    assert action.trajectory[-1].position_m[0] == pytest.approx(1.5)
    with pytest.raises(ValueError, match="Baseline horizon"):
        LearnedOutcomeRolloutModel(loaded, baseline=KinematicRolloutModel(RolloutConfig(horizon_s=1.5)))
    loaded.training_summary = {}
    with pytest.raises(ValueError, match="retrain"):
        LearnedOutcomeRolloutModel(loaded)


@pytest.mark.parametrize("fault", ["nan", "duplicate", "reverse", "gap"])
def test_training_rejects_bad_time_even_outside_sample_stride(tmp_path, fault):
    path = tmp_path / "timing.hdf5"
    make_synthetic_episode(path)
    episode = HDF5Episode.open(path)
    if fault == "nan":
        episode.image_timestamps[3] = float("nan")
    elif fault == "duplicate":
        episode.image_timestamps[3] = episode.image_timestamps[2]
    elif fault == "reverse":
        episode.image_timestamps[3] = episode.image_timestamps[2] - 0.01
    else:
        episode.image_timestamps[3:] += 0.2
    with pytest.raises(ValueError, match="timestamps|inconsistent future horizons"):
        build_training_dataset(episode, RolloutTrainingConfig(horizon_frames=2, stride=4))


def test_concatenation_rejects_different_episode_time_scales(tmp_path):
    episodes = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.hdf5"
        make_synthetic_episode(path)
        episodes.append(HDF5Episode.open(path))
    episodes[1].image_timestamps *= 2
    with pytest.raises(ValueError, match="inconsistent future horizons"):
        build_training_dataset_from_episodes(episodes, RolloutTrainingConfig(horizon_frames=2))


def test_training_rejects_rollout_horizon_mismatch(tmp_path) -> None:
    episode_path = tmp_path / "episode.hdf5"
    make_synthetic_episode(episode_path)
    episode = HDF5Episode.open(episode_path)
    with pytest.raises(ValueError, match="does not match episode timestamps"):
        build_training_dataset(
            episode,
            RolloutTrainingConfig(
                horizon_frames=5,
                rollout_horizon_s=1.5,
            ),
        )


def test_velocity_estimate_never_uses_future_rows():
    from jev_uav_track.data.hdf5_adapter import _finite_difference
    values = np.asarray([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    before = _finite_difference(values, [0, 1, 2], np)
    values[2] = (1000, 1000, 1000)
    after = _finite_difference(values, [0, 1, 2], np)
    np.testing.assert_array_equal(before[:2], after[:2])
    np.testing.assert_array_equal(before[0], [0, 0, 0])


@pytest.mark.parametrize("status", ["FAIL", "PENDING", None, ""])
@pytest.mark.parametrize("target_mode", ["absolute", "baseline_residual"])
def test_training_rejects_unverified_before_reading_states(status, target_mode):
    # A metadata-only adapter ensures rejected episodes never reach sample access.
    episode = SimpleNamespace(
        path="unverified.hdf5",
        metadata={} if status is None else {"validation_status": status},
        tracking_state=Mock(side_effect=AssertionError("must not read states")),
    )
    config = RolloutTrainingConfig(target_mode=target_mode)
    with pytest.raises(ValueError, match="requires validation_status=PASS"):
        build_training_dataset(episode, config)
    with pytest.raises(ValueError, match="requires validation_status=PASS"):
        build_training_dataset_from_episodes([episode], config)
    episode.tracking_state.assert_not_called()


def test_cross_episode_split_rejects_overlap_and_reports_holdout(tmp_path) -> None:
    train_path = tmp_path / "train.hdf5"
    validation_path = tmp_path / "validation.hdf5"
    make_synthetic_episode(train_path)
    make_synthetic_episode(validation_path)
    train_episode = HDF5Episode.open(train_path)
    validation_episode = HDF5Episode.open(validation_path)
    config = RolloutTrainingConfig(horizon_frames=2, stride=1, max_samples=6)

    model, metrics = fit_outcome_model_cross_episode(
        [train_episode],
        [validation_episode],
        config=config,
        max_samples_per_episode=6,
    )
    assert metrics["split"] == "cross_episode"
    assert metrics["train_episode_count"] == 1
    assert metrics["validation_episode_count"] == 1
    assert metrics["validation_count"] == 6
    assert model.training_summary["split"] == "cross_episode"

    with pytest.raises(ValueError, match="overlap"):
        fit_outcome_model_cross_episode(
            [train_episode],
            [train_episode],
            config=config,
        )
