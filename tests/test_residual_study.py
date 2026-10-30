import numpy as np
import pytest


def test_nonlinear_model_learns_curvature_and_roundtrips(tmp_path):
    from jev_uav_track.residual_study import fit_predictor, StudyPredictor
    x = np.linspace(-2, 2, 200)[:, None]
    y = x**2
    query = np.linspace(-1.9, 1.9, 51)[:, None]
    linear = fit_predictor(x, y, 'linear')
    curved = fit_predictor(x, y, 'rff64')
    assert np.mean((curved.predict(query) - query**2)**2) < .25 * np.mean((linear.predict(query) - query**2)**2)
    curved.save(tmp_path / 'model.npz')
    np.testing.assert_array_equal(curved.predict(query), StudyPredictor.load(tmp_path / 'model.npz').predict(query))
    np.testing.assert_array_equal(curved.mean, x.mean(axis=0))
    assert not curved.supported(np.array([[10.]]))[0]


def test_calibration_disables_harmful_and_keeps_helpful_outputs():
    from jev_uav_track.residual_study import fit_predictor, calibrate_shrinkage
    x = np.linspace(-1, 1, 20)[:, None]
    model = fit_predictor(x, np.ones((20, 2)), 'linear')
    blend, report = calibrate_shrinkage(model, [(x, np.column_stack([np.ones(20), -np.ones(20)]))])
    np.testing.assert_array_equal(blend, [1., 0.])
    assert report['coverage_guarantee'] is False


def test_calibration_equal_weights_episodes_not_frames_and_supports_zero():
    from jev_uav_track.residual_study import fit_predictor, calibrate_shrinkage
    model = fit_predictor(np.array([[-1.], [1.]]), np.ones((2, 1)), 'linear')
    # Small helpful episode must not be drowned out by long harmful episode.
    blend, _ = calibrate_shrinkage(model, [(np.zeros((1, 1)), np.ones((1, 1))),
        (np.zeros((100, 1)), -np.ones((100, 1)))])
    assert blend[0] == 0.
    blend, _ = calibrate_shrinkage(model, [(np.array([[10.]]), np.ones((1, 1)))])
    assert blend[0] == 0.  # Unsupported correction has zero benefit; tie selects zero.


def test_invalid_fit_and_empty_calibration_reject():
    from jev_uav_track.residual_study import fit_predictor, calibrate_shrinkage
    with pytest.raises(ValueError):
        fit_predictor(np.array([[np.nan]]), np.ones((1, 1)), 'linear')
    model = fit_predictor(np.array([[-1.], [1.]]), np.ones((2, 1)), 'linear')
    with pytest.raises(ValueError):
        calibrate_shrinkage(model, [])
