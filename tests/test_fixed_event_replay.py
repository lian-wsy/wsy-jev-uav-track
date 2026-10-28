from copy import deepcopy
import pytest
from scripts.evaluate_fixed_event_replay import evaluate


def batch():
    records = [{"timestamp_s": i / 5, "annotated_visible": i != 5,
                "annotated_in_frame": True, "annotated_occluded": False,
                "proxy_visible": i != 5, "tracked_actor_id": 7, "ground_truth_actor_id": 7}
               for i in range(61)]
    return {"clients": {"mock": {"episodes": [{"episode": "test.hdf5", "records": records}]}}}


def test_method_early_termination_is_failure_not_exclusion():
    reference = batch()
    method = deepcopy(reference)
    method["clients"]["mock"]["episodes"][0]["records"] = []
    result = evaluate(reference, {"terminated": method})["methods"]["terminated+mock"]
    assert result["eligible_events"] == 1
    assert result["episode_equal_recovery_rate"] == 0
    assert result["episode_equal_restricted_mean_s"] == 10


def test_different_reference_annotations_cannot_be_compared():
    reference = batch()
    method = deepcopy(reference)
    method["clients"]["mock"]["episodes"][0]["records"][5]["annotated_visible"] = True
    with pytest.raises(ValueError, match="reference"):
        evaluate(reference, {"mismatch": method})
