import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml


class WorkflowError(ValueError):
    """Workflow manifest, template or binding validation failed."""


@dataclass(frozen=True)
class WorkflowManifest:
    workflow_key: str
    version: str
    template_file: str
    parameter_schema: dict[str, Any]
    bindings: dict[str, str]
    allowed_class_types: frozenset[str]
    required_models: tuple[str, ...]
    required_custom_nodes: tuple[str, ...]
    min_vram_mb: int
    timeout_seconds: int
    node_labels: dict[str, str]
    output_nodes: tuple[str, ...]
    enabled: bool

    @classmethod
    def load(cls, path: Path) -> "WorkflowManifest":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise WorkflowError("workflow manifest must be an object")
        required = {
            "workflow_key",
            "version",
            "template_file",
            "parameter_schema",
            "bindings",
            "allowed_class_types",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise WorkflowError(f"workflow manifest missing: {', '.join(missing)}")
        return cls(
            workflow_key=str(raw["workflow_key"]),
            version=str(raw["version"]),
            template_file=str(raw["template_file"]),
            parameter_schema=dict(raw["parameter_schema"]),
            bindings={str(k): str(v) for k, v in dict(raw["bindings"]).items()},
            allowed_class_types=frozenset(str(v) for v in raw["allowed_class_types"]),
            required_models=tuple(str(v) for v in raw.get("required_models", [])),
            required_custom_nodes=tuple(str(v) for v in raw.get("required_custom_nodes", [])),
            min_vram_mb=int(raw.get("min_vram_mb", 0)),
            timeout_seconds=int(raw.get("timeout_seconds", 900)),
            node_labels={str(k): str(v) for k, v in dict(raw.get("node_labels", {})).items()},
            output_nodes=tuple(str(v) for v in raw.get("output_nodes", [])),
            enabled=bool(raw.get("enabled", False)),
        )


def validate_api_workflow(template: dict[str, Any], allowed_class_types: frozenset[str]) -> None:
    if not template or "nodes" in template or "links" in template:
        raise WorkflowError("必须使用 Export Workflow (API) 导出的对象格式，不能使用 UI 保存格式")
    for node_id, node in template.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise WorkflowError("API workflow nodes must be object entries")
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or class_type not in allowed_class_types:
            raise WorkflowError(f"node {node_id} class_type is not allowed")
        if not isinstance(node.get("inputs", {}), dict):
            raise WorkflowError(f"node {node_id} inputs must be an object")


def _parse_binding(path: str) -> tuple[str, str]:
    parts = path.split(".")
    if len(parts) < 3 or parts[1] != "inputs" or any(not p or p.startswith("_") for p in parts):
        raise WorkflowError(f"unsafe binding path: {path}")
    if len(parts) != 3:
        raise WorkflowError("bindings may only target <node_id>.inputs.<name>")
    return parts[0], parts[2]


def render_workflow(
    manifest: WorkflowManifest, template: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    try:
        jsonschema.validate(parameters, manifest.parameter_schema)
    except jsonschema.ValidationError as exc:
        raise WorkflowError(f"workflow parameters are invalid: {exc.message}") from exc
    validate_api_workflow(template, manifest.allowed_class_types)
    unknown = set(parameters) - set(manifest.bindings)
    if unknown:
        raise WorkflowError(f"parameters have no binding: {', '.join(sorted(unknown))}")
    rendered = copy.deepcopy(template)
    for parameter, value in parameters.items():
        node_id, input_name = _parse_binding(manifest.bindings[parameter])
        node = rendered.get(node_id)
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise WorkflowError(f"binding node does not exist: {node_id}")
        node["inputs"][input_name] = value
    validate_api_workflow(rendered, manifest.allowed_class_types)
    return rendered


def template_digest(template: dict[str, Any]) -> str:
    raw = json.dumps(template, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def validated_node_vram_requirement(
    *,
    declared_min_vram_mb: int,
    reported_labels: dict[str, Any],
    node_id: str | None,
    workflow_key: str | None,
    workflow_version: str | None,
    template_sha256: str | None,
) -> int:
    """Use only an explicitly recorded acceptance for this exact node/template.

    These profiles are control-plane inventory, never accepted from a Node
    Agent heartbeat. A new workflow revision, GPU replacement, missing proof,
    or malformed profile falls back to the unchanged workflow declaration.
    """
    if not all((node_id, workflow_key, workflow_version, template_sha256)):
        return declared_min_vram_mb
    profiles = reported_labels.get("validated_vram_profiles")
    if not isinstance(profiles, dict):
        return declared_min_vram_mb
    profile = profiles.get(workflow_key)
    if not isinstance(profile, dict):
        return declared_min_vram_mb
    if (
        profile.get("status") != "PASSED"
        or profile.get("node_id") != node_id
        or profile.get("version") != workflow_version
        or profile.get("template_sha256") != template_sha256
        or not reported_labels.get("gpu_uuid")
        or profile.get("gpu_uuid") != reported_labels.get("gpu_uuid")
    ):
        return declared_min_vram_mb
    for field in ("evidence_sha256", "runtime_profile_sha256"):
        digest = profile.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            return declared_min_vram_mb
    minimum = profile.get("min_vram_mb")
    if type(minimum) is not int or not 12000 <= minimum <= declared_min_vram_mb:
        return declared_min_vram_mb
    return minimum


def node_compatibility_reasons(
    *,
    min_vram_mb: int,
    required_labels: dict[str, Any],
    allowed_class_types: list[str] | set[str] | frozenset[str],
    total_vram_mb: int,
    reported_labels: dict[str, Any],
    node_id: str | None = None,
    workflow_key: str | None = None,
    workflow_version: str | None = None,
    template_sha256: str | None = None,
) -> list[str]:
    """Return fail-closed workflow compatibility reasons for one node."""
    reasons: list[str] = []
    effective_minimum = validated_node_vram_requirement(
        declared_min_vram_mb=min_vram_mb,
        reported_labels=reported_labels,
        node_id=node_id,
        workflow_key=workflow_key,
        workflow_version=workflow_version,
        template_sha256=template_sha256,
    )
    if total_vram_mb < effective_minimum:
        reasons.append(f"vram {total_vram_mb}MB < required {effective_minimum}MB")
    for key, value in required_labels.items():
        if str(reported_labels.get(key)) != str(value):
            reasons.append(f"label {key} must equal {value}")
    raw_classes = reported_labels.get("comfy_class_types")
    if not isinstance(raw_classes, list):
        reasons.append("ComfyUI class inventory unavailable")
    else:
        available = {str(value) for value in raw_classes}
        missing = sorted(set(allowed_class_types) - available)
        if missing:
            reasons.append("missing ComfyUI classes: " + ", ".join(missing))
    return reasons
