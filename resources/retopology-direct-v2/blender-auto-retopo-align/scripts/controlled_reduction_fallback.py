#!/usr/bin/env python3
"""Build one high-derived low with an adaptive shape-preservation budget.

This is a generated-low construction tool, not an alignment or delivery-review
stage.  It never modifies SOURCE_HIGH, never creates or re-layouts UVs, and
saves exactly one authoritative low.  Temporary density probes exist only in
memory so a fixed percentage cannot leave large sources needlessly dense.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-blend", required=True)
    parser.add_argument("--output-blend", required=True)
    parser.add_argument("--generation-report", required=True)
    parser.add_argument("--high-object", default="SOURCE_HIGH")
    parser.add_argument("--work-object", default="SOURCE_HIGH_NORMALIZED_WORK")
    parser.add_argument("--low-object", default="NEXTGEN_SOURCE_LOW")
    parser.add_argument("--ratio", type=float)
    parser.add_argument("--asset-class", default="mixed_layered")
    parser.add_argument("--prefer-normalized-work", action="store_true")
    return parser.parse_args(argv)


def adaptive_initial_ratio(face_count: int) -> float:
    """Choose an absolute density budget instead of retaining a fixed percent."""

    target_faces = round(2000.0 + 18.0 * math.sqrt(max(face_count, 1)))
    target_faces = max(3000, min(30000, target_faces))
    return max(0.01, min(0.65, target_faces / max(face_count, 1)))


def candidate_ratios(initial_ratio: float, *, explicit: bool) -> list[float]:
    if explicit:
        return [initial_ratio]
    ratios = [initial_ratio]
    for multiplier in (1.55, 2.35):
        ratio = min(0.50, initial_ratio * multiplier)
        if ratio > ratios[-1] + 1e-6:
            ratios.append(ratio)
    return ratios


def sampled_vertex_coordinates(obj: bpy.types.Object, limit: int = 6000) -> list[Vector]:
    vertices = obj.data.vertices
    if len(vertices) <= limit:
        return [vertex.co.copy() for vertex in vertices]
    step = len(vertices) / float(limit)
    return [vertices[min(len(vertices) - 1, int(index * step))].co.copy() for index in range(limit)]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def local_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    coordinates = [vertex.co for vertex in obj.data.vertices]
    return (
        Vector(tuple(min(coordinate[axis] for coordinate in coordinates) for axis in range(3))),
        Vector(tuple(max(coordinate[axis] for coordinate in coordinates) for axis in range(3))),
    )


def shape_preservation_metrics(high: bpy.types.Object, low: bpy.types.Object) -> dict[str, float | bool]:
    low_vertices = [vertex.co.copy() for vertex in low.data.vertices]
    low_polygons = [tuple(polygon.vertices) for polygon in low.data.polygons]
    tree = BVHTree.FromPolygons(low_vertices, low_polygons, all_triangles=False)
    distances: list[float] = []
    for point in sampled_vertex_coordinates(high):
        nearest = tree.find_nearest(point)
        if nearest is None or nearest[0] is None:
            distances.append(math.inf)
        else:
            distances.append(float(nearest[3]))

    high_minimum, high_maximum = local_bounds(high)
    low_minimum, low_maximum = local_bounds(low)
    high_dimensions = high_maximum - high_minimum
    low_dimensions = low_maximum - low_minimum
    diagonal = max(high_dimensions.length, 1e-12)
    dimension_error_ratio = max(
        abs(low_dimensions[axis] - high_dimensions[axis]) / max(abs(high_dimensions[axis]), 1e-12)
        for axis in range(3)
    )
    center_error_ratio = (
        ((low_minimum + low_maximum) * 0.5 - (high_minimum + high_maximum) * 0.5).length
        / diagonal
    )
    p95_ratio = percentile(distances, 0.95) / diagonal
    p99_ratio = percentile(distances, 0.99) / diagonal
    maximum_ratio = max(distances, default=math.inf) / diagonal
    # This is a construction-time density selector, not a delivery gate.  If a
    # sparse probe exceeds the budget, the next denser probe is used; the last
    # valid mesh is still delivered without adding a publication review stage.
    within_shape_budget = (
        p95_ratio <= 0.004
        and p99_ratio <= 0.015
        and dimension_error_ratio <= 0.015
        and center_error_ratio <= 0.005
    )
    return {
        "p95_surface_error_ratio": p95_ratio,
        "p99_surface_error_ratio": p99_ratio,
        "maximum_surface_error_ratio": maximum_ratio,
        "dimension_error_ratio": dimension_error_ratio,
        "center_error_ratio": center_error_ratio,
        "within_shape_budget": within_shape_budget,
    }


def build_reduced_copy(
    source: bpy.types.Object,
    high: bpy.types.Object,
    low_name: str,
    ratio: float,
) -> bpy.types.Object:
    low = source.copy()
    low.data = source.data.copy()
    low.name = low_name
    bpy.context.scene.collection.objects.link(low)
    low.matrix_world = high.matrix_world.copy()

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    modifier = low.modifiers.new(name="CONTROLLED_REDUCTION_FALLBACK", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    modifier.use_symmetry = False
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return low


def mesh_fingerprint(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    digest.update(struct.pack("<QQ", len(obj.data.vertices), len(obj.data.polygons)))
    for vertex in obj.data.vertices:
        digest.update(struct.pack("<ddd", *vertex.co))
    for polygon in obj.data.polygons:
        digest.update(struct.pack("<I", len(polygon.vertices)))
        for vertex_index in polygon.vertices:
            digest.update(struct.pack("<I", vertex_index))
    return digest.hexdigest()


def remove_degenerate_faces(obj: bpy.types.Object) -> int:
    mesh = obj.data
    mesh.update(calc_edges=True)
    diagonal = math.sqrt(sum(component * component for component in obj.dimensions))
    area_epsilon = max(diagonal * diagonal * 1e-14, 1e-18)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        broken = [face for face in bm.faces if not math.isfinite(face.calc_area()) or face.calc_area() <= area_epsilon]
        if broken:
            bmesh.ops.delete(bm, geom=broken, context="FACES")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update(calc_edges=True)
    return len(broken)


def assign_display_material(obj: bpy.types.Object) -> None:
    obj.data.materials.clear()
    material = bpy.data.materials.get("NEXTGEN_OPAQUE_ORANGE")
    if material is None:
        material = bpy.data.materials.new("NEXTGEN_OPAQUE_ORANGE")
        material.diffuse_color = (1.0, 0.23, 0.02, 1.0)
    obj.data.materials.append(material)
    obj.color = (1.0, 0.23, 0.02, 1.0)
    for polygon in obj.data.polygons:
        polygon.material_index = 0


def main() -> None:
    args = parse_args()
    if args.ratio is not None and not 0.0 < args.ratio < 1.0:
        raise RuntimeError("controlled reduction ratio must be between zero and one")

    bpy.ops.wm.open_mainfile(filepath=str(Path(args.input_blend).resolve()))
    high = bpy.data.objects.get(args.high_object)
    if high is None or high.type != "MESH":
        raise RuntimeError(f"high mesh not found: {args.high_object}")
    high_fingerprint_before = mesh_fingerprint(high)
    high_faces = len(high.data.polygons)

    work = bpy.data.objects.get(args.work_object)
    source = (
        work
        if args.prefer_normalized_work and work is not None and work.type == "MESH"
        else high
    )
    source_name = source.name

    initial_ratio = args.ratio if args.ratio is not None else adaptive_initial_ratio(high_faces)
    density_attempts: list[dict[str, object]] = []
    low: bpy.types.Object | None = None
    selected_ratio = initial_ratio
    for index, ratio in enumerate(candidate_ratios(initial_ratio, explicit=args.ratio is not None)):
        if low is not None:
            bpy.data.objects.remove(low, do_unlink=True)
        low = build_reduced_copy(source, high, args.low_object, ratio)
        metrics = shape_preservation_metrics(high, low)
        density_attempts.append(
            {
                "ratio": ratio,
                "faces": len(low.data.polygons),
                **metrics,
            }
        )
        selected_ratio = ratio
        if metrics["within_shape_budget"] or index == len(
            candidate_ratios(initial_ratio, explicit=args.ratio is not None)
        ) - 1:
            break

    if low is None:
        raise RuntimeError("RETOPOLOGY_TOPOLOGY_INVALID: reduction did not produce a mesh")

    removed_degenerate_faces = remove_degenerate_faces(low)
    low_faces = len(low.data.polygons)
    if low_faces <= 0 or low_faces >= high_faces:
        raise RuntimeError(
            "RETOPOLOGY_TOPOLOGY_INVALID: controlled reduction did not create a lower-face mesh"
        )
    if any(not math.isfinite(component) for vertex in low.data.vertices for component in vertex.co):
        raise RuntimeError("RETOPOLOGY_TOPOLOGY_INVALID: non-finite low vertex")

    assign_display_material(low)
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj not in {high, low}:
            bpy.data.objects.remove(obj, do_unlink=True)
    high.hide_set(False)
    high.hide_render = False
    low.hide_set(False)
    low.hide_render = False

    if mesh_fingerprint(high) != high_fingerprint_before:
        raise RuntimeError("RETOPOLOGY_SOURCE_MUTATED: SOURCE_HIGH changed during fallback")

    output_blend = Path(args.output_blend).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)

    report = {
        "status": "generated_for_user_inspection",
        "assets": [
            {
                "high_object": high.name,
                "low_object": low.name,
                "faces": low_faces,
                "triangles": sum(max(1, len(polygon.vertices) - 2) for polygon in low.data.polygons),
                "uv_layers": len(low.data.uv_layers),
                "method_decision": "controlled_direct_reduction",
                "region_method_map": {
                    "whole_source_fallback": {
                        "method": "controlled_direct_reduction",
                        "asset_class": args.asset_class,
                        "connectivity_class": "fused_or_region_boundary_unsafe",
                        "boundary_evidence": (
                            "region separation was unsafe; connectivity is not used as the "
                            "asset complexity classification"
                        ),
                        "source": source_name,
                        "requested_ratio": args.ratio,
                        "selected_ratio": selected_ratio,
                    }
                },
                "actual_plugin_use": {
                    "used": False,
                    "plugins": [],
                    "note": "deterministic Blender Decimate fallback on a fresh high-derived copy",
                },
                "coordinate_space": "source_high_local",
                "coordinate_authority": "high_object_matrix_world",
                "presentation_offset_applied": False,
                "density_selection": {
                    "mode": "explicit" if args.ratio is not None else "adaptive_shape_budget",
                    "attempts": density_attempts,
                    "selected_ratio": selected_ratio,
                    "selected_faces": low_faces,
                    "delivery_gate_added": False,
                },
            }
        ],
        "source_preserved": True,
        "zero_area_faces": 0,
        "removed_degenerate_faces": removed_degenerate_faces,
        "review_status": "user_inspection_required",
        "uv_policy": "preserve_existing_no_generation_or_relayout",
    }
    report_path = Path(args.generation_report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "LI3D_CONTROLLED_REDUCTION_FALLBACK_OK:"
        + json.dumps(
            {
                "high_faces": high_faces,
                "low_faces": low_faces,
                "ratio": selected_ratio,
                "asset_class": args.asset_class,
                "density_attempts": len(density_attempts),
                "source": source_name,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
