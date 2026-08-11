from __future__ import annotations

import json
from pathlib import Path

from packages.asset_processing.codex_job_launcher import (
    normalize_generation_report,
    persist_refreshed_auth,
)


def write_source_manifest(tmp_path: Path) -> None:
    (tmp_path / "source-manifest.json").write_text(
        json.dumps({"prepared_high_object": "SOURCE_HIGH"}), encoding="utf-8"
    )


def test_normalizes_known_v230_objects_alias_and_preserves_raw_report(
    tmp_path: Path,
) -> None:
    write_source_manifest(tmp_path)
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
    write_source_manifest(tmp_path)
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
    write_source_manifest(tmp_path)
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
    write_source_manifest(tmp_path)
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


def test_canonicalizes_existing_asset_high_to_prepared_source(tmp_path: Path) -> None:
    write_source_manifest(tmp_path)
    report = {
        "status": "generated_for_user_inspection",
        "assets": [
            {
                "high_object": "original_model_name",
                "low_object": "SOURCE_LOW",
                "faces": 100,
                "triangles": 200,
                "method_decision": "semantic_reconstruction",
                "actual_plugin_use": False,
            }
        ],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is True
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"][0]["high_object"] == "SOURCE_HIGH"


def test_v300_coordinate_authority_fields_survive_normalization(tmp_path: Path) -> None:
    write_source_manifest(tmp_path)
    report = {
        "status": "generated_for_user_inspection",
        "assets": [
            {
                "high_object": "SOURCE_HIGH",
                "low_object": "SOURCE_LOW",
                "faces": 100,
                "triangles": 200,
                "method_decision": "semantic_reconstruction",
                "actual_plugin_use": "none",
                "coordinate_space": "source_high_local",
                "coordinate_authority": "high_object_matrix_world",
                "presentation_offset_applied": False,
            }
        ],
    }
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert normalize_generation_report(tmp_path) is True
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"][0]["coordinate_space"] == "source_high_local"
    assert normalized["assets"][0]["coordinate_authority"] == "high_object_matrix_world"
    assert normalized["assets"][0]["presentation_offset_applied"] is False


def test_reconstructs_assets_from_read_only_blend_inspection(tmp_path: Path) -> None:
    write_source_manifest(tmp_path)
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "plan.json").write_text(
        json.dumps({"method_decision": "per_component_hybrid"}), encoding="utf-8"
    )
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps({"status": "generated_for_user_inspection"}), encoding="utf-8")

    def inspect(_job_dir: Path, high_object: str) -> list[dict[str, object]]:
        assert high_object == "SOURCE_HIGH"
        return [{"low_object": "SOURCE_LOW", "faces": 80, "triangles": 160}]

    assert normalize_generation_report(tmp_path, delivery_inspector=inspect) is True
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"] == [
        {
            "high_object": "SOURCE_HIGH",
            "low_object": "SOURCE_LOW",
            "faces": 80,
            "triangles": 160,
            "method_decision": "per_component_hybrid",
            "actual_plugin_use": None,
        }
    ]
    assert normalized["gpu_control_compatibility"]["blend_inspection_used"] is True


def test_direct_blend_reconstructs_assets_from_read_only_source_and_delivery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "generation_report.json"
    path.write_text(json.dumps({"status": "generated_for_user_inspection"}), encoding="utf-8")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "plan.json").write_text(
        json.dumps({"method_decision": "semantic_reconstruction"}), encoding="utf-8"
    )

    def inspect_source(job_dir: Path) -> str:
        assert job_dir == tmp_path
        return "synthetic_high"

    def inspect_delivery(job_dir: Path, high_object: str) -> list[dict[str, object]]:
        assert job_dir == tmp_path
        assert high_object == "synthetic_high"
        return [
            {
                "low_object": "synthetic_high_LOW",
                "faces": 6,
                "triangles": 12,
            }
        ]

    assert (
        normalize_generation_report(
            tmp_path,
            delivery_inspector=inspect_delivery,
            source_inspector=inspect_source,
        )
        is True
    )
    normalized = json.loads(path.read_text(encoding="utf-8"))
    assert normalized["assets"] == [
        {
            "high_object": "synthetic_high",
            "low_object": "synthetic_high_LOW",
            "faces": 6,
            "triangles": 12,
            "method_decision": "semantic_reconstruction",
            "actual_plugin_use": None,
        }
    ]
    assert normalized["gpu_control_compatibility"]["blend_inspection_used"] is True


def test_persists_rotated_task_auth_to_node_private_source(tmp_path: Path) -> None:
    source = tmp_path / "persistent" / "auth.json"
    source.parent.mkdir()
    source.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"account_id": "account-1", "refresh_token": "old"},
            }
        ),
        encoding="utf-8",
    )
    source.chmod(0o600)
    task = tmp_path / "task" / "auth.json"
    task.parent.mkdir()
    refreshed = {
        "auth_mode": "chatgpt",
        "tokens": {"account_id": "account-1", "refresh_token": "new"},
    }
    task.write_text(json.dumps(refreshed), encoding="utf-8")

    from packages.asset_processing.codex_job_launcher import sha256

    assert persist_refreshed_auth(source, task, sha256(source), source) == "updated"
    assert json.loads(source.read_text(encoding="utf-8")) == refreshed
    assert source.stat().st_mode & 0o777 == 0o600


def test_auth_writeback_never_overwrites_concurrent_operator_update(
    tmp_path: Path,
) -> None:
    source = tmp_path / "auth.json"
    source.write_text(json.dumps({"credential": "old"}), encoding="utf-8")
    from packages.asset_processing.codex_job_launcher import sha256

    original_sha256 = sha256(source)
    task = tmp_path / "task-auth.json"
    task.write_text(json.dumps({"credential": "refreshed"}), encoding="utf-8")
    source.write_text(json.dumps({"credential": "operator-new"}), encoding="utf-8")

    assert persist_refreshed_auth(source, task, original_sha256, source) == "source_changed"
    assert json.loads(source.read_text(encoding="utf-8")) == {"credential": "operator-new"}


def test_auth_writeback_rejects_account_identity_change(tmp_path: Path) -> None:
    source = tmp_path / "auth.json"
    source.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "account-1"}}),
        encoding="utf-8",
    )
    task = tmp_path / "task-auth.json"
    task.write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "account-2"}}),
        encoding="utf-8",
    )
    from packages.asset_processing.codex_job_launcher import sha256

    assert persist_refreshed_auth(source, task, sha256(source), source) == "identity_mismatch"
