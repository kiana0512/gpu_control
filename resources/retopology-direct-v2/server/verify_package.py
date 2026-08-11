#!/usr/bin/env python3
"""Verify the merged retopology/alignment package without running Blender or Codex."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "blender-auto-retopo-align"
SKILL = ROOT / SKILL_ID
EXPECTED_SKILL_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/coordinate-restoration-contract.md",
    "references/direct-output-construction-rules.md",
    "references/execution-plan-schema.md",
    "references/learned-asset-lessons.md",
    "scripts/align_bake_models.py",
    "scripts/finalize_generated_pair.py",
    "scripts/guard_shape_authority_plan.py",
    "scripts/prepare_fbx_source.py",
    "scripts/render_alignment_views.py",
    "scripts/validate_bake_pair.py",
}
EXPECTED_SERVER_FILES = {
    "agent_prompt.md",
    "align_existing_low.py",
    "batch_retopology.py",
    "one_click_retopology.py",
    "verify_package.py",
    "worker.env.example",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_files(root: Path):
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]


def verify_manifest(errors: list[str]) -> None:
    manifest_path = ROOT / "manifest" / "FILES.sha256"
    if not manifest_path.is_file():
        errors.append("manifest/FILES.sha256 is missing")
        return
    declared: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError:
            errors.append(f"invalid manifest line: {line}")
            continue
        declared[relative] = digest
    actual_paths = {
        path.relative_to(ROOT).as_posix(): path
        for path in clean_files(ROOT)
        if path != manifest_path
    }
    if set(declared) != set(actual_paths):
        errors.append(
            "manifest file list mismatch: "
            f"missing={sorted(set(actual_paths) - set(declared))} "
            f"extra={sorted(set(declared) - set(actual_paths))}"
        )
        return
    for relative, path in actual_paths.items():
        if declared[relative] != sha256(path):
            errors.append(f"manifest hash mismatch: {relative}")


def require_tokens(path: Path, tokens: tuple[str, ...], label: str, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in text:
            errors.append(f"{label} misses {token}")


def main() -> int:
    errors: list[str] = []
    actual_skill = {path.relative_to(SKILL).as_posix() for path in clean_files(SKILL)}
    if actual_skill != EXPECTED_SKILL_FILES:
        errors.append(
            f"skill file mismatch: expected={sorted(EXPECTED_SKILL_FILES)} actual={sorted(actual_skill)}"
        )
    actual_server = {path.name for path in (ROOT / "server").iterdir() if path.is_file()}
    if actual_server != EXPECTED_SERVER_FILES:
        errors.append(
            f"server file mismatch: expected={sorted(EXPECTED_SERVER_FILES)} actual={sorted(actual_server)}"
        )

    require_tokens(
        SKILL / "SKILL.md",
        (
            "Generated low in the same job",
            "Existing external low",
            "source_high_local",
            "RETOPOLOGY_COORDINATE_MISMATCH",
            "Never hide the low",
        ),
        "skill",
        errors,
    )
    require_tokens(
        SKILL / "references" / "coordinate-restoration-contract.md",
        (
            "source_matrix_world.inverted()",
            "work_to_world",
            "presentation_offset_applied: false",
            "must not recenter",
        ),
        "coordinate contract",
        errors,
    )
    require_tokens(
        ROOT / "server" / "agent_prompt.md",
        (
            "$blender-auto-retopo-align",
            "guard_shape_authority_plan.py",
            "source_high_local",
            "high_object_matrix_world",
            "presentation_offset_applied: false",
            "不得给低模保留展示平移",
            "source_topology",
            "RETOPOLOGY_TOPOLOGY_INVALID",
            "SOURCE_HIGH_NORMALIZED_WORK",
        ),
        "agent prompt",
        errors,
    )
    require_tokens(
        ROOT / "server" / "one_click_retopology.py",
        (
            "finalize_generated_pair.py",
            "RETOPOLOGY_COORDINATE_MISMATCH",
            '"bake_alignment_status": "aligned"',
            '"status": "generated_for_user_inspection"',
            '"automatic_retry": False',
        ),
        "one-click entrypoint",
        errors,
    )
    require_tokens(
        SKILL / "scripts" / "finalize_generated_pair.py",
        (
            "source_matrix_restore",
            "topology_uv_fingerprint",
            "EXPORT_READBACK_MISMATCH",
            "opaque_yellow",
            "icp_used",
            "require_clean_topology",
            "li3d-retopology-topology-v1",
        ),
        "coordinate finalizer",
        errors,
    )
    require_tokens(
        SKILL / "scripts" / "prepare_fbx_source.py",
        (
            "SOURCE_HIGH_NORMALIZED_WORK",
            "exact_position_weld_on_work_copy",
            "source_high_unchanged",
            "normalized_work_source",
        ),
        "FBX source preparation",
        errors,
    )
    require_tokens(
        SKILL / "scripts" / "guard_shape_authority_plan.py",
        (
            "component_method_map",
            "exceptionally_complex_asset",
            "semantic_or_hybrid_would_lose_identity",
            "direct_reduction_reason",
            "DIRECT_REDUCTION_MAX_SOURCE_COMPONENTS",
            "DIRECT_REDUCTION_MAX_DUPLICATE_VERTEX_RATIO",
            "uses_normalized_work_source",
            "normalized_work_qualified",
        ),
        "method-routing guard",
        errors,
    )

    scripts = [
        ROOT / "server" / "align_existing_low.py",
        ROOT / "server" / "batch_retopology.py",
        ROOT / "server" / "one_click_retopology.py",
        ROOT / "server" / "verify_package.py",
        *sorted((SKILL / "scripts").glob("*.py")),
    ]
    for script in scripts:
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except SyntaxError as exc:
            errors.append(f"syntax error in {script}: {exc}")

    for example in sorted((ROOT / "examples").glob("*.json")):
        try:
            json.loads(example.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid example JSON {example.name}: {exc}")

    env_text = (ROOT / "server" / "worker.env.example").read_text(encoding="utf-8")
    for name in (
        "BLENDER_EXECUTABLE",
        "CODEX_BIN",
        "CODEX_EXEC_ARGS_JSON",
        "RETOPOLOGY_FBX_IMPORT_TIMEOUT_SECONDS",
        "RETOPOLOGY_TIMEOUT_SECONDS",
        "RETOPOLOGY_FINALIZE_TIMEOUT_SECONDS",
    ):
        if f"{name}=" not in env_text:
            errors.append(f"worker.env.example misses {name}")
    if "CODEX_EXEC_ARGS_JSON='[" not in env_text:
        errors.append("CODEX_EXEC_ARGS_JSON must retain valid JSON when sourced by a shell")

    largest = max((path.stat().st_size, path) for path in clean_files(ROOT))
    if largest[0] > 5 * 1024 * 1024:
        errors.append(f"unexpected large file: {largest[1]} ({largest[0]} bytes)")
    verify_manifest(errors)

    payload = {
        "ok": not errors,
        "package_version": "3.0.4",
        "skill_id": SKILL_ID,
        "skill_file_count": len(actual_skill),
        "one_click_entrypoint": str(ROOT / "server" / "one_click_retopology.py"),
        "batch_entrypoint": str(ROOT / "server" / "batch_retopology.py"),
        "external_low_entrypoint": str(ROOT / "server" / "align_existing_low.py"),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
