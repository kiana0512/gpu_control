from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy


def blender_args(argv: list[str] | None = None) -> list[str]:
    values = list(sys.argv if argv is None else argv)
    return values[values.index("--") + 1 :] if "--" in values else values[1:]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit game-low topology flow quality")
    parser.add_argument("--objects", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--minimum-angle-warning", type=float, default=10.0)
    parser.add_argument("--minimum-angle-hard", type=float, default=5.0)
    parser.add_argument("--under-angle-ratio-max", type=float, default=0.02)
    parser.add_argument("--aspect-warning", type=float, default=6.0)
    parser.add_argument("--aspect-hard", type=float, default=20.0)
    parser.add_argument("--over-aspect-ratio-max", type=float, default=0.05)
    parser.add_argument("--valence-warning", type=int, default=8)
    parser.add_argument("--valence-hard", type=int, default=10)
    parser.add_argument("--adjacent-area-ratio-warning", type=float, default=6.0)
    return parser.parse_args(blender_args(argv))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def triangle_quality(points) -> tuple[float, float, float]:
    edges = [
        (points[1] - points[0]).length,
        (points[2] - points[1]).length,
        (points[0] - points[2]).length,
    ]
    longest = max(edges)
    area = ((points[1] - points[0]).cross(points[2] - points[0])).length * 0.5
    if area <= 1.0e-14 or longest <= 1.0e-14:
        return 0.0, float("inf"), area
    angles = []
    for corner in range(3):
        first = points[(corner + 1) % 3] - points[corner]
        second = points[(corner + 2) % 3] - points[corner]
        denominator = max(first.length * second.length, 1.0e-20)
        cosine = max(-1.0, min(1.0, first.dot(second) / denominator))
        angles.append(math.degrees(math.acos(cosine)))
    shortest_altitude = 2.0 * area / longest
    return min(angles), longest / max(shortest_altitude, 1.0e-20), area


def polygon_area(points) -> float:
    if len(points) < 3:
        return 0.0
    origin = points[0]
    return sum(
        ((points[index] - origin).cross(points[index + 1] - origin)).length * 0.5
        for index in range(1, len(points) - 1)
    )


def audit_object(obj, thresholds: dict[str, float | int]) -> dict:
    if obj.type != "MESH":
        return {
            "object": obj.name,
            "quantitative_pass": False,
            "failures": ["object_is_not_mesh"],
            "warnings": [],
        }

    mesh = obj.data
    matrix = obj.matrix_world
    explicit_angles: list[float] = []
    explicit_aspects: list[float] = []
    explicit_areas: list[float] = []
    polygon_areas: dict[int, float] = {}
    degenerate_faces = 0
    edge_faces: dict[tuple[int, int], list[int]] = {}

    for polygon in mesh.polygons:
        indices = list(polygon.vertices)
        points = [matrix @ mesh.vertices[index].co for index in indices]
        area = polygon_area(points)
        polygon_areas[polygon.index] = area
        if area <= 1.0e-14:
            degenerate_faces += 1
        for offset, first in enumerate(indices):
            second = indices[(offset + 1) % len(indices)]
            edge = (min(first, second), max(first, second))
            edge_faces.setdefault(edge, []).append(polygon.index)
        if len(indices) == 3:
            angle, aspect, triangle_area = triangle_quality(points)
            explicit_angles.append(angle)
            explicit_aspects.append(aspect)
            explicit_areas.append(triangle_area)

    valence = [0] * len(mesh.vertices)
    for edge in mesh.edges:
        valence[edge.vertices[0]] += 1
        valence[edge.vertices[1]] += 1

    adjacent_area_ratios: list[float] = []
    for face_indices in edge_faces.values():
        if len(face_indices) != 2:
            continue
        first_area = polygon_areas[face_indices[0]]
        second_area = polygon_areas[face_indices[1]]
        smaller = min(first_area, second_area)
        larger = max(first_area, second_area)
        if smaller > 1.0e-14:
            adjacent_area_ratios.append(larger / smaller)

    triangle_count = len(explicit_angles)
    under_angle = sum(
        value < thresholds["minimum_angle_warning"] for value in explicit_angles
    )
    over_aspect = sum(
        value > thresholds["aspect_warning"] for value in explicit_aspects
    )
    under_angle_ratio = under_angle / max(1, triangle_count)
    over_aspect_ratio = over_aspect / max(1, triangle_count)
    maximum_valence = max(valence) if valence else 0
    finite_aspects = [value for value in explicit_aspects if math.isfinite(value)]

    failures: list[str] = []
    warnings: list[str] = []
    if degenerate_faces:
        failures.append(f"degenerate_faces:{degenerate_faces}")
    if explicit_angles and min(explicit_angles) < thresholds["minimum_angle_hard"]:
        failures.append("explicit_triangle_minimum_angle_below_hard_limit")
    if under_angle_ratio > thresholds["under_angle_ratio_max"]:
        failures.append("too_many_explicit_triangles_below_warning_angle")
    if finite_aspects and max(finite_aspects) > thresholds["aspect_hard"]:
        failures.append("explicit_triangle_aspect_above_hard_limit")
    if over_aspect_ratio > thresholds["over_aspect_ratio_max"]:
        failures.append("too_many_explicit_triangles_above_warning_aspect")
    if maximum_valence > thresholds["valence_hard"]:
        failures.append("vertex_valence_above_hard_limit")

    if under_angle:
        warnings.append(f"explicit_triangles_below_warning_angle:{under_angle}")
    if over_aspect:
        warnings.append(f"explicit_triangles_above_warning_aspect:{over_aspect}")
    high_valence = sum(value > thresholds["valence_warning"] for value in valence)
    if high_valence:
        warnings.append(f"vertices_above_warning_valence:{high_valence}")
    area_jump_count = sum(
        value > thresholds["adjacent_area_ratio_warning"]
        for value in adjacent_area_ratios
    )
    if area_jump_count:
        warnings.append(f"adjacent_polygon_area_jumps_require_reason:{area_jump_count}")

    return {
        "object": obj.name,
        "topology": {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "explicit_triangles": triangle_count,
            "quads": sum(len(polygon.vertices) == 4 for polygon in mesh.polygons),
            "ngons": sum(len(polygon.vertices) > 4 for polygon in mesh.polygons),
        },
        "explicit_triangle_quality": {
            "minimum_angle_degrees": rounded(min(explicit_angles) if explicit_angles else None),
            "angle_p01": rounded(percentile(explicit_angles, 0.01)),
            "angle_p05": rounded(percentile(explicit_angles, 0.05)),
            "below_warning_count": under_angle,
            "below_warning_ratio": rounded(under_angle_ratio),
            "aspect_p50": rounded(percentile(finite_aspects, 0.50)),
            "aspect_p95": rounded(percentile(finite_aspects, 0.95)),
            "maximum_aspect": rounded(max(finite_aspects) if finite_aspects else None),
            "above_warning_count": over_aspect,
            "above_warning_ratio": rounded(over_aspect_ratio),
        },
        "vertex_valence": {
            "maximum": maximum_valence,
            "above_warning_count": high_valence,
        },
        "adjacent_polygon_area_ratio": {
            "p95": rounded(percentile(adjacent_area_ratios, 0.95)),
            "p99": rounded(percentile(adjacent_area_ratios, 0.99)),
            "maximum": rounded(max(adjacent_area_ratios) if adjacent_area_ratios else None),
            "above_warning_count": area_jump_count,
        },
        "degenerate_faces": degenerate_faces,
        "quantitative_pass": not failures,
        "failures": failures,
        "warnings": warnings,
        "manual_wire_review_required": [
            "visible slivers and radial fans",
            "poles on silhouette or smooth highlight paths",
            "wrinkle-following or uniformly noisy density",
            "abrupt face-size changes without construction reason",
            "exact hidden or thin-cap location for any requested exception",
        ],
        "wire_distribution_pass": None,
    }


def main(argv: list[str] | None = None) -> dict:
    args = parse_args(argv)
    thresholds = {
        "minimum_angle_warning": args.minimum_angle_warning,
        "minimum_angle_hard": args.minimum_angle_hard,
        "under_angle_ratio_max": args.under_angle_ratio_max,
        "aspect_warning": args.aspect_warning,
        "aspect_hard": args.aspect_hard,
        "over_aspect_ratio_max": args.over_aspect_ratio_max,
        "valence_warning": args.valence_warning,
        "valence_hard": args.valence_hard,
        "adjacent_area_ratio_warning": args.adjacent_area_ratio_warning,
    }
    records = []
    missing = []
    for name in args.objects:
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
        else:
            records.append(audit_object(obj, thresholds))

    quantitative_pass = not missing and all(
        record["quantitative_pass"] for record in records
    )
    payload = {
        "schema": "blender-retopology-topology-flow-v1",
        "blend": bpy.data.filepath,
        "thresholds": thresholds,
        "missing_objects": missing,
        "records": records,
        "quantitative_pass": quantitative_pass,
        "wire_distribution_pass": None,
        "notes": [
            "wire_distribution_pass remains null until close visible wire inspection is recorded",
            "thresholds apply to explicit triangle polygons, not invisible render triangulation inside quads",
            "adjacent area jumps are warnings requiring curvature or construction explanation",
            "the script cannot identify hidden faces or validate a claimed thin-cap exception",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RETOPOLOGY_TOPOLOGY_FLOW_AUDIT", json.dumps(payload, ensure_ascii=False))
    if args.strict and not quantitative_pass:
        failures = [f"missing:{name}" for name in missing]
        for record in records:
            failures.extend(f"{record['object']}:{item}" for item in record["failures"])
        raise RuntimeError("; ".join(failures))
    return payload


if __name__ == "__main__":
    main()
