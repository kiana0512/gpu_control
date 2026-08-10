from copy import deepcopy

from packages.gpu_control_core.assets import (
    retopology_coordinate_dimension_evidence_valid,
)


def valid_evidence() -> dict:
    return {
        "maximum_dimension_relative_error": 0.05,
        "pairs": [
            {
                "high_low_dimension_relative_error": [0.01, 0.02, 0.015],
                "high_low_maximum_dimension_relative_error": 0.02,
                "maximum_dimension_relative_error_limit": 0.05,
            }
        ],
    }


def test_dimension_evidence_accepts_only_bounded_consistent_measurements() -> None:
    evidence = valid_evidence()

    assert retopology_coordinate_dimension_evidence_valid(evidence) is True

    for replacement in (0.051, -0.001, float("nan"), True):
        invalid = deepcopy(evidence)
        invalid["pairs"][0]["high_low_dimension_relative_error"][1] = replacement
        assert retopology_coordinate_dimension_evidence_valid(invalid) is False


def test_dimension_evidence_rejects_missing_or_forged_maximum() -> None:
    missing = valid_evidence()
    missing["pairs"] = []
    assert retopology_coordinate_dimension_evidence_valid(missing) is False

    forged = valid_evidence()
    forged["pairs"][0]["high_low_maximum_dimension_relative_error"] = 0.01
    assert retopology_coordinate_dimension_evidence_valid(forged) is False
