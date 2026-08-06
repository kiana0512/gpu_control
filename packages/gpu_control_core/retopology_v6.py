"""Immutable runtime-resource and contract checks for Retopology V6."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import jsonschema

POLICY_ID = "li3d-retopology-v6"
POLICY_VERSION = "6.0.1"
# Updated together with resources/retopology-v6/RUNTIME_FILES.sha256.
POLICY_SHA256 = "e7b24c93c11d550ac9fedd167ff23f9ddd70cba4db014caaf2e157cddeafb266"
RUNTIME_MANIFEST = "RUNTIME_FILES.sha256"

ALLOWED_PRODUCTION_METHODS = frozenset(
    {"semantic_reconstruction", "hybrid_per_component"}
)
ALLOWED_COMPONENT_METHODS = frozenset(
    {
        "semantic_reconstruction",
        "reuse_clean_source_component",
        "normal_map_only",
        "omit_noncritical_micro_detail",
    }
)
FORBIDDEN_GENERATOR_PATTERN = re.compile(
    r"(?i)(?:[\"'](?:DECIMATE|REMESH)[\"']|"
    r"decimate_collapse|quadriflow_remesh|voxel_remesh|remesh_voxel_size)"
)


class RetopologyV6ResourceError(RuntimeError):
    pass


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_runtime_resources(root: Path) -> dict[str, str]:
    """Fail closed unless every approved V6 runtime file has its frozen hash."""

    root = root.resolve()
    manifest = root / RUNTIME_MANIFEST
    if not manifest.is_file() or manifest.is_symlink():
        raise RetopologyV6ResourceError("Retopology V6 runtime manifest is missing")
    verified: dict[str, str] = {}
    for line_number, raw_line in enumerate(manifest.read_text("utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError as exc:
            raise RetopologyV6ResourceError(
                f"invalid Retopology V6 manifest line {line_number}"
            ) from exc
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise RetopologyV6ResourceError(
                f"invalid Retopology V6 digest on line {line_number}"
            )
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RetopologyV6ResourceError(
                f"unsafe Retopology V6 manifest path on line {line_number}"
            )
        candidate = root / relative_path
        if not candidate.is_file() or candidate.is_symlink():
            raise RetopologyV6ResourceError(f"missing V6 runtime file: {relative}")
        actual = sha256_path(candidate)
        if actual != expected:
            raise RetopologyV6ResourceError(f"V6 runtime hash mismatch: {relative}")
        verified[relative] = actual
    required = {
        "config/retopology-policy-v6.json",
        "contracts/retopology-plan-v6.schema.json",
        "contracts/retopology-result-v6.schema.json",
        "prompts/formal-retopology-agent.md",
        "prompts/automatic-qa-agent.md",
        "skill/blender-retopology-compare-iterate/SKILL.md",
        "skill/blender-retopology-compare-iterate/scripts/audit_pair.py",
        "skill/blender-retopology-compare-iterate/scripts/audit_topology_flow.py",
    }
    missing = required.difference(verified)
    if missing:
        raise RetopologyV6ResourceError(
            f"V6 runtime manifest omits required files: {sorted(missing)}"
        )
    if verified["config/retopology-policy-v6.json"] != POLICY_SHA256:
        raise RetopologyV6ResourceError("V6 policy identity does not match the approved contract")
    return verified


def load_contract(root: Path, filename: str) -> dict[str, Any]:
    allowed = {
        "retopology-plan-v6.schema.json",
        "retopology-request-v6.schema.json",
        "retopology-result-v6.schema.json",
    }
    if filename not in allowed:
        raise RetopologyV6ResourceError("unapproved Retopology V6 contract name")
    payload = json.loads((root / "contracts" / filename).read_text("utf-8"))
    if not isinstance(payload, dict):
        raise RetopologyV6ResourceError(f"invalid JSON Schema object: {filename}")
    jsonschema.Draft202012Validator.check_schema(payload)
    return payload


def validate_contract_payload(root: Path, filename: str, payload: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(load_contract(root, filename)).validate(payload)


def assert_structured_retopology_plan(payload: dict[str, Any]) -> None:
    """Reject any production plan that selects direct reduction or an unapproved method."""

    method = payload.get("method")
    if method not in ALLOWED_PRODUCTION_METHODS:
        raise RetopologyV6ResourceError(
            f"RETOPOLOGY_V6_DIRECT_REDUCTION_FORBIDDEN: plan method {method!r}"
        )
    component_decisions = payload.get("component_decisions")
    if not isinstance(component_decisions, list):
        raise RetopologyV6ResourceError("Retopology V6 component plan is missing")
    for index, component in enumerate(component_decisions):
        component_method = component.get("method") if isinstance(component, dict) else None
        if component_method not in ALLOWED_COMPONENT_METHODS:
            raise RetopologyV6ResourceError(
                "RETOPOLOGY_V6_DIRECT_REDUCTION_FORBIDDEN: "
                f"component {index} method {component_method!r}"
            )


def assert_no_forbidden_generator_scripts(workspace: Path) -> None:
    """Fail closed when Agent-authored Blender scripts use reduction/remesh generators."""

    for script_path in sorted(workspace.glob("*.py")):
        if not script_path.is_file() or script_path.is_symlink():
            continue
        source = script_path.read_text("utf-8", errors="replace")
        match = FORBIDDEN_GENERATOR_PATTERN.search(source)
        if match is not None:
            raise RetopologyV6ResourceError(
                "RETOPOLOGY_V6_DIRECT_REDUCTION_FORBIDDEN: "
                f"{script_path.name} contains {match.group(0)!r}"
            )
