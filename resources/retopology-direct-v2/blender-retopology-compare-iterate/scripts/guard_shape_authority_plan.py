#!/usr/bin/env python3
"""Guard pre-generation shape authority without inspecting generated geometry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ALLOWED_METHODS = {
    "controlled_direct_reduction",
    "semantic_reconstruction",
    "per_component_hybrid",
}
ALLOWED_CORRESPONDENCE = {
    "measured_local_sections",
    "bounded_surface_projection",
    "fresh_high_derived_cage",
    "per_component_hybrid",
}
ALLOWED_CONSTANT_PROVENANCE = {"high_measurement", "topology_density_only"}
ALLOWED_COMPONENT_METHODS = {
    "semantic_reconstruction",
    "controlled_direct_reduction",
    "qualified_remeshing",
    "fresh_high_derived_cage",
}


def nonempty_list(value: object) -> bool:
    return isinstance(value, list) and bool(value)


def guard(plan: object) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"], warnings

    if plan.get("output_behavior") != "save_and_stop":
        errors.append("output_behavior must be save_and_stop")
    if plan.get("user_inspects_result") is not True:
        errors.append("user_inspects_result must be true")
    post_actions = plan.get("automatic_post_generation_actions")
    if post_actions != []:
        errors.append("automatic_post_generation_actions must be an empty list")

    method = plan.get("method_decision")
    if method not in ALLOWED_METHODS:
        errors.append(f"unsupported method_decision: {method!r}")

    source = plan.get("source_identity")
    if not isinstance(source, dict):
        errors.append("source_identity must be an object")
    else:
        for key in ("blend_filepath", "object_name", "mesh_data_name"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                errors.append(f"source_identity.{key} must be a non-empty string")
        if source.get("measurement_space") != "high_local":
            errors.append("source_identity.measurement_space must be high_local")
        matrix = source.get("matrix_world")
        if not isinstance(matrix, list) or len(matrix) != 16 or not all(
            isinstance(value, (int, float)) for value in matrix
        ):
            errors.append("source_identity.matrix_world must contain 16 numbers")

    shape = plan.get("shape_authority")
    if not isinstance(shape, dict):
        return errors + ["shape_authority must be an object"], warnings

    required = {
        "authority",
        "global_registration_inputs",
        "local_profile_sections",
        "feature_controls",
        "openings",
        "component_evidence",
        "surface_correspondence_method",
        "template_constants",
        "uses_only_global_bounds",
        "fixed_geometry_proportions_from_template",
    }
    missing = sorted(required - set(shape))
    if missing:
        errors.append("shape_authority missing: " + ", ".join(missing))

    if shape.get("authority") != "high_poly_only":
        errors.append("shape_authority.authority must be high_poly_only")
    if not nonempty_list(shape.get("global_registration_inputs")):
        errors.append("global_registration_inputs must be recorded")
    if shape.get("uses_only_global_bounds") is not False:
        errors.append("global bounds cannot be the only shape input")
    if shape.get("fixed_geometry_proportions_from_template") is not False:
        errors.append("fixed template proportions cannot control geometry")
    if shape.get("surface_correspondence_method") not in ALLOWED_CORRESPONDENCE:
        errors.append("surface_correspondence_method is not high-derived")

    sections = shape.get("local_profile_sections")
    if not nonempty_list(sections):
        errors.append("at least one current-high local profile section is required")
        sections = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"local_profile_sections[{index}] must be an object")
            continue
        if section.get("coordinate_space") != "high_local":
            errors.append(f"local_profile_sections[{index}] must use high_local")
        if section.get("source") != "high_measurement":
            errors.append(f"local_profile_sections[{index}] must come from high_measurement")
        if not nonempty_list(section.get("controlling_views")):
            errors.append(f"local_profile_sections[{index}] lacks controlling_views")

    features = shape.get("feature_controls")
    if not isinstance(features, list):
        errors.append("feature_controls must be present, even when empty")
        features = []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            errors.append(f"feature_controls[{index}] must be an object")
            continue
        if feature.get("decision") == "omit":
            if feature.get("authority") != "user_approved":
                errors.append(f"feature_controls[{index}] omission lacks user approval")
        elif feature.get("authority") != "high_measurement":
            errors.append(f"feature_controls[{index}] is not controlled by the high")
        if not nonempty_list(feature.get("controlling_views")):
            errors.append(f"feature_controls[{index}] lacks controlling_views")
        measurements = feature.get("measurements")
        if not isinstance(measurements, dict) or not measurements:
            errors.append(f"feature_controls[{index}] lacks high-derived measurements")

    openings = shape.get("openings")
    if not isinstance(openings, list):
        errors.append("openings must be present, even when empty")
        openings = []
    for index, opening in enumerate(openings):
        if not isinstance(opening, dict):
            errors.append(f"openings[{index}] must be an object")
            continue
        if opening.get("authority") != "high_measurement":
            errors.append(f"openings[{index}] is not controlled by the high")
        if not opening.get("boundary_measurement"):
            errors.append(f"openings[{index}] lacks boundary_measurement")
        if not nonempty_list(opening.get("controlling_views")):
            errors.append(f"openings[{index}] lacks controlling_views")

    components = shape.get("component_evidence")
    if not nonempty_list(components):
        errors.append("component_evidence is required")
        components = []
    component_ids = {
        item.get("component_id")
        for item in components
        if isinstance(item, dict) and item.get("component_id")
    }
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            errors.append(f"component_evidence[{index}] must be an object")
            continue
        if not component.get("component_id") or not component.get("evidence"):
            errors.append(f"component_evidence[{index}] requires component_id and evidence")

    decisions = plan.get("component_decisions")
    if not isinstance(decisions, list) or not decisions:
        errors.append("component_decisions must contain at least one evidence-backed decision")
        decisions = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"component_decisions[{index}] must be an object")
            continue
        if decision.get("evidence_id") not in component_ids:
            errors.append(f"component_decisions[{index}] lacks component evidence")

    constants = shape.get("template_constants")
    if not isinstance(constants, list):
        errors.append("template_constants must be present, even when empty")
        constants = []
    for index, constant in enumerate(constants):
        if not isinstance(constant, dict):
            errors.append(f"template_constants[{index}] must be an object")
            continue
        if constant.get("provenance") not in ALLOWED_CONSTANT_PROVENANCE:
            errors.append(f"template_constants[{index}] has invalid provenance")

    count_policy = plan.get("count_evidence_policy")
    required_false = {
        "fixed_face_count_is_shape_evidence",
        "fixed_component_count_is_shape_evidence",
        "budget_or_count_can_satisfy_shape_gate",
    }
    if not isinstance(count_policy, dict):
        errors.append("count_evidence_policy is required")
    else:
        for key in sorted(required_false):
            if count_policy.get(key) is not False:
                errors.append(f"count_evidence_policy.{key} must be false")

    if method == "controlled_direct_reduction":
        evidence = plan.get("direct_reduction_evidence")
        if not isinstance(evidence, dict):
            errors.append("direct_reduction_evidence is required")
        else:
            if evidence.get("structurally_complex") is not True:
                errors.append("direct reduction requires structurally_complex=true")
            if evidence.get("integrated_continuous_object") is not True:
                errors.append("direct reduction requires integrated_continuous_object=true")
            if evidence.get("fresh_high_duplicate") is not True:
                errors.append("direct reduction must start from a fresh high duplicate")
            if evidence.get("structural_subregions_checked") is not True:
                errors.append("direct reduction requires structural_subregions_checked=true")
            if evidence.get("structured_shell_or_assembly_absent") is not True:
                errors.append("direct reduction cannot cover a structured shell or assembly")
            if evidence.get("joined_source_state_used_as_integration_evidence") is not False:
                errors.append("joined SOURCE_HIGH state cannot be used as integration evidence")

    if method == "per_component_hybrid":
        method_map = plan.get("component_method_map")
        if not isinstance(method_map, list) or len(method_map) < 2:
            errors.append("per-component hybrid requires at least two component_method_map entries")
            method_map = []
        routed_methods: set[str] = set()
        for index, route in enumerate(method_map):
            if not isinstance(route, dict):
                errors.append(f"component_method_map[{index}] must be an object")
                continue
            if route.get("evidence_id") not in component_ids:
                errors.append(f"component_method_map[{index}] lacks component evidence")
            if not isinstance(route.get("boundary_measurement"), str) or not route[
                "boundary_measurement"
            ].strip():
                errors.append(f"component_method_map[{index}] lacks boundary_measurement")
            component_method = route.get("method")
            if component_method not in ALLOWED_COMPONENT_METHODS:
                errors.append(f"component_method_map[{index}] has unsupported method")
            else:
                routed_methods.add(component_method)
        if "semantic_reconstruction" not in routed_methods:
            errors.append("per-component hybrid requires a semantic-reconstruction region")
        if not routed_methods.intersection(
            {"controlled_direct_reduction", "qualified_remeshing", "fresh_high_derived_cage"}
        ):
            errors.append("per-component hybrid requires a high-derived organic region")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)], "warnings": []}, ensure_ascii=False))
        return 1

    errors, warnings = guard(plan)
    print(
        json.dumps(
            {"ok": not errors, "errors": errors, "warnings": warnings},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
