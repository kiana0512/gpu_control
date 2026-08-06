from __future__ import annotations

import json
from pathlib import Path

from packages.asset_processing.codex_job_launcher import normalize_generation_report


def test_normalizes_known_v230_objects_alias_and_preserves_raw_report(
    tmp_path: Path,
) -> None:
    report = {
        "status": "generated_for_user_inspection",
        "objects": [
            {
                "high_name": "SOURCE_HIGH",
                "low_name": "SOURCE_LOW",
                "high_faces": 1000,
                "high_triangles": 2000,
                "low_faces": 120,
                "low_triangles": 240,
                "method_decision": "semantic_reconstruction",
                "actual_plugin_use": [],
            }
        ],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is True
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"] == [
        {
            "high_object": "SOURCE_HIGH",
            "low_object": "SOURCE_LOW",
            "faces": 120,
            "triangles": 240,
            "method_decision": "semantic_reconstruction",
            "actual_plugin_use": [],
        }
    ]
    original = json.loads(
        (tmp_path / "generation_report.original.json").read_text(encoding="utf-8")
    )
    assert original == report


def test_incomplete_objects_alias_remains_fail_closed(tmp_path: Path) -> None:
    report = {
        "status": "generated_for_user_inspection",
        "objects": [{"high_name": "SOURCE_HIGH"}],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is False
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "generation_report.original.json").exists()


def test_missing_diagnostic_counters_are_explicit_null_not_delivery_failure(
    tmp_path: Path,
) -> None:
    report = {
        "status": "generated_for_user_inspection",
        "objects": [
            {
                "high_name": "SOURCE_HIGH",
                "low_name": "SOURCE_LOW",
                "method_decision": "semantic_reconstruction",
            }
        ],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is True
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"] == [
        {
            "high_object": "SOURCE_HIGH",
            "low_object": "SOURCE_LOW",
            "faces": None,
            "triangles": None,
            "method_decision": "semantic_reconstruction",
            "actual_plugin_use": None,
        }
    ]
    assert normalized["gpu_control_compatibility"]["missing_diagnostics"] == [
        {
            "low_object": "SOURCE_LOW",
            "fields": ["faces", "triangles", "actual_plugin_use"],
        }
    ]


def test_missing_delivery_identity_still_fails_closed(tmp_path: Path) -> None:
    report = {
        "status": "generated_for_user_inspection",
        "objects": [
            {
                "high_name": "SOURCE_HIGH",
                "method_decision": "semantic_reconstruction",
                "low_faces": 100,
                "low_triangles": 200,
            }
        ],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is False
    assert json.loads(path.read_text(encoding="utf-8")) == report
