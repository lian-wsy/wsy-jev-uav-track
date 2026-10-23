from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
h5py = pytest.importorskip("h5py")

from jev_uav_track.data import HDF5Episode
from jev_uav_track.evaluation import OfflineEvaluationConfig, evaluate_episode
from jev_uav_track.training import (
    RolloutTrainingConfig,
    LinearOutcomeModel,
    build_training_dataset,
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
    state_indices = np.arange(1, state_count, 2, dtype=np.int64)
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


def test_hdf5_adapter_and_training_round_trip(tmp_path) -> None:
    episode_path = tmp_path / "episode.hdf5"
    make_synthetic_episode(episode_path)
    episode = HDF5Episode.open(episode_path)
    assert episode.frame_count == 12
    assert episode.images is None
    assert episode.tracking_state(0).environment["target_actor_id"] == 17

    config = RolloutTrainingConfig(horizon_frames=2, stride=1, max_samples=8)
    dataset = build_training_dataset(episode, config)
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
