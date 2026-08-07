"""Restore Direct V2 low meshes to their high-mesh world centers before delivery.

The topology agent may translate its generated low mesh for presentation.  That
presentation transform must never leak into the authoritative Blend/FBX used by
UV and baking.  This adapter changes translation only, saves the aligned Blend,
exports the requested lows, and fails closed unless FBX readback preserves the
aligned world-space bounds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

SCHEMA_VERSION = "retopology_coordinate_restoration.v1"
MODE = "translation_only_world_aabb_center"


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-blend", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--generation-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vector_values(value: Vector) -> list[float]:
    return [float(value[index]) for index in range(3)]


def matrix_values(obj: bpy.types.Object, size: int) -> list[list[float]]:
    matrix = obj.matrix_world
    return [
        [float(matrix[row][column]) for column in range(size)]
        for row in range(size)
    ]


def mesh_sha256(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    mesh = obj.data
    digest.update(struct.pack("!QQQ", len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for vertex in mesh.vertices:
        digest.update(struct.pack("!ddd", *[float(value) for value in vertex.co]))
    for edge in mesh.edges:
        digest.update(struct.pack("!II", *edge.vertices))
    for polygon in mesh.polygons:
        digest.update(struct.pack("!I", len(polygon.vertices)))
        for vertex_index in polygon.vertices:
            digest.update(struct.pack("!I", vertex_index))
    return digest.hexdigest()


def object_signature(obj: bpy.types.Object, *, include_translation: bool) -> dict[str, Any]:
    signature: dict[str, Any] = {
        "mesh_sha256": mesh_sha256(obj),
        "world_linear_3x3": matrix_values(obj, 3),
        "world_determinant": float(obj.matrix_world.to_3x3().determinant()),
    }
    if include_translation:
        signature["world_matrix_4x4"] = [
            [float(obj.matrix_world[row][column]) for column in range(4)]
            for row in range(4)
        ]
    return signature


def require_static_mesh(name: str, role: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH" or len(obj.data.polygons) <= 0:
        raise RuntimeError(f"{role} mesh is missing or empty: {name}")
    if role == "low":
        if obj.parent is not None:
            raise RuntimeError(f"generated low has a parent and cannot be translated safely: {name}")
        if len(obj.constraints):
            raise RuntimeError(f"generated low has constraints and cannot be translated safely: {name}")
        animation = obj.animation_data
        if animation is not None and (
            animation.action is not None
            or len(animation.drivers)
            or len(animation.nla_tracks)
        ):
            raise RuntimeError(
                f"generated low has transform animation and cannot be translated safely: {name}"
            )
    return obj


def world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points: list[Vector] = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    if not points:
        raise RuntimeError("cannot measure empty world bounds")
    minimum = Vector(min(point[axis] for point in points) for axis in range(3))
    maximum = Vector(max(point[axis] for point in points) for axis in range(3))
    return minimum, maximum


def bounds_payload(objects: list[bpy.types.Object]) -> dict[str, list[float]]:
    minimum, maximum = world_bounds(objects)
    center = (minimum + maximum) * 0.5
    dimensions = maximum - minimum
    return {
        "minimum": vector_values(minimum),
        "maximum": vector_values(maximum),
        "center": vector_values(center),
        "dimensions": vector_values(dimensions),
    }


def max_vector_delta(left: list[float], right: list[float]) -> float:
    return max(abs(left[index] - right[index]) for index in range(3))


def nearly_equal(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            nearly_equal(a, b, tolerance) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, int | float) and isinstance(right, int | float):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def load_pairs(path: Path) -> list[tuple[str, str]]:
    payload = json.loads(path.read_text("utf-8"))
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("generation report has no asset pairs")
    pairs: list[tuple[str, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("generation report contains an invalid asset pair")
        high_name = asset.get("high_object")
        low_name = asset.get("low_object")
        if not isinstance(high_name, str) or not high_name:
            raise RuntimeError("generation report has an invalid high object name")
        if not isinstance(low_name, str) or not low_name or low_name == high_name:
            raise RuntimeError("generation report has an invalid low object name")
        pairs.append((high_name, low_name))
    if len(set(pairs)) != len(pairs) or len({low for _, low in pairs}) != len(pairs):
        raise RuntimeError("generation report contains duplicate coordinate pairs")
    return pairs


def export_and_read_back(
    output_fbx: Path,
    low_objects: list[bpy.types.Object],
    expected_bounds: dict[str, list[float]],
) -> dict[str, Any]:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in low_objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = low_objects[0]
    output_fbx.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(output_fbx),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="AUTO",
    )
    if not output_fbx.is_file() or output_fbx.stat().st_size <= 0:
        raise RuntimeError("FBX export did not create a non-empty file")

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.fbx(filepath=str(output_fbx))
    imported = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not imported or any(len(obj.data.polygons) <= 0 for obj in imported):
        raise RuntimeError("FBX readback has no non-empty mesh")
    actual_bounds = bounds_payload(imported)
    expected_dimensions = expected_bounds["dimensions"]
    scale = max(max(abs(value) for value in expected_dimensions), 1.0)
    tolerance = max(1e-5, scale * 1e-4)
    center_delta = max_vector_delta(expected_bounds["center"], actual_bounds["center"])
    dimensions_delta = max_vector_delta(expected_dimensions, actual_bounds["dimensions"])
    passed = center_delta <= tolerance and dimensions_delta <= tolerance
    if not passed:
        raise RuntimeError(
            "FBX readback changed aligned bounds: "
            f"center_delta={center_delta:.9g}, dimensions_delta={dimensions_delta:.9g}, "
            f"tolerance={tolerance:.9g}"
        )
    return {
        "passed": True,
        "sha256": file_sha256(output_fbx),
        "size_bytes": output_fbx.stat().st_size,
        "mesh_object_count": len(imported),
        "expected_bounds": expected_bounds,
        "readback_bounds": actual_bounds,
        "center_max_abs_delta": center_delta,
        "dimensions_max_abs_delta": dimensions_delta,
        "tolerance": tolerance,
    }


def main() -> None:
    args = arguments()
    if not args.output_blend.is_file() or args.output_blend.stat().st_size <= 0:
        raise RuntimeError("loaded delivery Blend is missing")
    pairs = load_pairs(args.generation_report)
    input_blend_sha256 = file_sha256(args.output_blend)
    records: list[dict[str, Any]] = []
    low_objects: list[bpy.types.Object] = []

    for high_name, low_name in pairs:
        high = require_static_mesh(high_name, "high")
        low = require_static_mesh(low_name, "low")
        high_signature = object_signature(high, include_translation=True)
        low_signature = object_signature(low, include_translation=False)
        high_bounds = bounds_payload([high])
        low_bounds_before = bounds_payload([low])
        delta = Vector(high_bounds["center"]) - Vector(low_bounds_before["center"])
        transform_tolerance = max(
            1e-6,
            max(abs(value) for value in high_bounds["dimensions"]) * 1e-6,
        )
        translation_required = max(abs(value) for value in delta) > transform_tolerance
        if translation_required:
            matrix_world = low.matrix_world.copy()
            matrix_world.translation = matrix_world.translation + delta
            low.matrix_world = matrix_world
            bpy.context.view_layer.update()

        low_bounds_after = bounds_payload([low])
        high_after = object_signature(high, include_translation=True)
        low_after = object_signature(low, include_translation=False)
        center_residual = max_vector_delta(
            high_bounds["center"], low_bounds_after["center"]
        )
        dimension_delta = max_vector_delta(
            low_bounds_before["dimensions"], low_bounds_after["dimensions"]
        )
        if high_after != high_signature:
            raise RuntimeError(f"coordinate restoration changed the high mesh: {high_name}")
        if not nearly_equal(low_after, low_signature):
            raise RuntimeError(
                f"coordinate restoration changed low geometry, rotation, or scale: {low_name}"
            )
        if center_residual > transform_tolerance or dimension_delta > transform_tolerance:
            raise RuntimeError(
                "coordinate restoration did not produce a translation-only match: "
                f"{low_name}; center_residual={center_residual:.9g}, "
                f"dimension_delta={dimension_delta:.9g}, "
                f"tolerance={transform_tolerance:.9g}"
            )
        records.append(
            {
                "high_object": high_name,
                "low_object": low_name,
                "coordinate_action": (
                    "translation_restored" if translation_required else "unchanged"
                ),
                "translation_applied": (
                    vector_values(delta) if translation_required else [0.0, 0.0, 0.0]
                ),
                "high_bounds": high_bounds,
                "low_bounds_before": low_bounds_before,
                "low_bounds_after": low_bounds_after,
                "center_residual": center_residual,
                "dimensions_max_abs_delta": dimension_delta,
                "transform_tolerance": transform_tolerance,
                "high_preserved": True,
                "low_mesh_preserved": True,
                "low_rotation_scale_preserved": True,
            }
        )
        low_objects.append(low)

    blend_translation_changed = any(
        record["coordinate_action"] == "translation_restored" for record in records
    )
    aligned_union_bounds = bounds_payload(low_objects)
    if blend_translation_changed:
        args.output_blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output_blend), check_existing=False)
    output_blend_sha256 = file_sha256(args.output_blend)
    fbx = export_and_read_back(args.output_fbx, low_objects, aligned_union_bounds)
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "passed": True,
        "allowed_change": "generated_low_world_translation_only",
        "input_blend_sha256": input_blend_sha256,
        "output_blend_sha256": output_blend_sha256,
        "source_high_preserved": True,
        "blend_translation_changed": blend_translation_changed,
        "pairs": records,
        "fbx_readback": fbx,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
