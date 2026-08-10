from copy import deepcopy

from packages.gpu_control_core.assets import (
    retopology_coordinate_dimension_evidence_valid,
    retopology_fbx_meter_evidence_valid,
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


def test_fbx_meter_evidence_rejects_legacy_centimeter_baked_coordinates() -> None:
    evidence = {
        "fbx_readback": {
            "passed": True,
            "unit_contract": {
                "schema_version": "retopology_fbx_units.v1",
                "passed": True,
                "coordinate_unit": "meter",
                "unit_scale_factor_centimeters": 100.0,
                "original_unit_scale_factor_centimeters": 100.0,
                "raw_coordinates_are_meters": True,
                "global_scale": 1.0,
                "apply_unit_scale": True,
                "apply_scale_options": "FBX_SCALE_UNITS",
                "axis_forward": "-Z",
                "axis_up": "Y",
            },
        }
    }
    assert retopology_fbx_meter_evidence_valid(evidence) is True

    evidence["fbx_readback"]["unit_contract"]["unit_scale_factor_centimeters"] = 1.0
    evidence["fbx_readback"]["unit_contract"]["apply_scale_options"] = "FBX_SCALE_NONE"
    assert retopology_fbx_meter_evidence_valid(evidence) is False
