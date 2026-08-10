"""Restore Direct V2 low meshes to the authoritative high-mesh transform.

The topology agent may alter an object's presentation transform after generating
the low mesh.  That presentation transform must never leak into the authoritative
Blend/FBX used by UV and baking.  This adapter restores the high object's exact
world linear transform and aligns world-space centers without changing low mesh
vertices, edges, faces or topology.  It then fails closed unless the dimensions
remain within the delivery limit and FBX readback preserves the aligned bounds.
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
from mathutils import Matrix, Vector

SCHEMA_VERSION = "retopology_coordinate_restoration.v3"
MODE = "high_world_linear_aabb_center_and_fbx_meter"
FBX_UNIT_CONTRACT_SCHEMA_VERSION = "retopology_fbx_units.v1"
FBX_UNIT_SCALE_FACTOR_CENTIMETERS = 100.0
MAXIMUM_DIMENSION_RELATIVE_ERROR = 0.05


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
    return [[float(matrix[row][column]) for column in range(size)] for row in range(size)]


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
            [float(obj.matrix_world[row][column]) for column in range(4)] for row in range(4)
        ]
    return signature


def require_static_mesh(name: str, role: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH" or len(obj.data.polygons) <= 0:
        raise RuntimeError(f"{role} mesh is missing or empty: {name}")
    if role == "low":
        if obj.parent is not None:
            raise RuntimeError(
                f"generated low has a parent and cannot be translated safely: {name}"
            )
        if len(obj.constraints):
            raise RuntimeError(
                f"generated low has constraints and cannot be translated safely: {name}"
            )
        animation = obj.animation_data
        if animation is not None and (
            animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)
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


def max_matrix_delta(left: list[list[float]], right: list[list[float]]) -> float:
    return max(
        abs(left[row][column] - right[row][column]) for row in range(3) for column in range(3)
    )


def dimension_relative_errors(
    high_dimensions: list[float], low_dimensions: list[float], tolerance: float
) -> list[float]:
    return [
        abs(low_dimensions[index] - high_dimensions[index])
        / max(abs(high_dimensions[index]), tolerance)
        for index in range(3)
    ]


def nearly_equal(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            nearly_equal(a, b, tolerance) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, int | float) and isinstance(right, int | float):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def fbx_double_property(path: Path, name: str) -> float:
    """Read one Blender binary-FBX ``Properties70/P`` double property.

    This deliberately validates the bytes written to the exchange file instead
    of trusting the scene setting used before export.  The exact string-property
    encoding avoids confusing ``UnitScaleFactor`` with
    ``OriginalUnitScaleFactor``.
    """

    encoded_name = name.encode("ascii")
    pattern = (
        b"S"
        + struct.pack("<I", len(encoded_name))
        + encoded_name
        + b"S"
        + struct.pack("<I", len(b"double"))
        + b"double"
        + b"S"
        + struct.pack("<I", len(b"Number"))
        + b"Number"
        + b"S"
        + struct.pack("<I", 0)
        + b"D"
    )
    payload = path.read_bytes()
    positions: list[int] = []
    offset = 0
    while True:
        found = payload.find(pattern, offset)
        if found < 0:
            break
        positions.append(found)
        offset = found + 1
    if len(positions) != 1:
        raise RuntimeError(f"FBX property {name} is missing or ambiguous")
    value_offset = positions[0] + len(pattern)
    if value_offset + 8 > len(payload):
        raise RuntimeError(f"FBX property {name} is truncated")
    value = float(struct.unpack_from("<d", payload, value_offset)[0])
    if not math.isfinite(value):
        raise RuntimeError(f"FBX property {name} is not finite")
    return value


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
    # GLB/glTF and browser rendering use metres.  Blender's default
    # FBX_SCALE_NONE bakes a 100x centimetre conversion into raw coordinates,
    # while common browser FBX loaders consume those coordinates without unit
    # compensation.  FBX_SCALE_UNITS keeps raw coordinates in metres and writes
    # UnitScaleFactor=100 (one FBX unit is one metre), which both Blender and
    # browser consumers interpret at the same physical size.
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"
    bpy.ops.export_scene.fbx(
        filepath=str(output_fbx),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        use_space_transform=True,
        bake_space_transform=False,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="AUTO",
    )
    if not output_fbx.is_file() or output_fbx.stat().st_size <= 0:
        raise RuntimeError("FBX export did not create a non-empty file")
    unit_scale_factor = fbx_double_property(output_fbx, "UnitScaleFactor")
    original_unit_scale_factor = fbx_double_property(output_fbx, "OriginalUnitScaleFactor")
    if not math.isclose(
        unit_scale_factor,
        FBX_UNIT_SCALE_FACTOR_CENTIMETERS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ) or not math.isclose(
        original_unit_scale_factor,
        FBX_UNIT_SCALE_FACTOR_CENTIMETERS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise RuntimeError(
            "FBX browser unit contract failed: "
            f"UnitScaleFactor={unit_scale_factor:.9g}, "
            f"OriginalUnitScaleFactor={original_unit_scale_factor:.9g}"
        )

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
        "unit_contract": {
            "schema_version": FBX_UNIT_CONTRACT_SCHEMA_VERSION,
            "passed": True,
            "coordinate_unit": "meter",
            "unit_scale_factor_centimeters": unit_scale_factor,
            "original_unit_scale_factor_centimeters": original_unit_scale_factor,
            "raw_coordinates_are_meters": True,
            "global_scale": 1.0,
            "apply_unit_scale": True,
            "apply_scale_options": "FBX_SCALE_UNITS",
            "axis_forward": "-Z",
            "axis_up": "Y",
        },
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
        low_mesh_sha256 = mesh_sha256(low)
        high_world_linear = matrix_values(high, 3)
        low_world_linear_before = matrix_values(low, 3)
        high_bounds = bounds_payload([high])
        low_bounds_before = bounds_payload([low])
        transform_tolerance = max(
            1e-6,
            max(abs(value) for value in high_bounds["dimensions"]) * 1e-6,
        )
        linear_transform_tolerance = 1e-6
        linear_transform_delta = max_matrix_delta(high_world_linear, low_world_linear_before)
        linear_transform_required = linear_transform_delta > linear_transform_tolerance
        if linear_transform_required:
            matrix_world = low.matrix_world.copy()
            for row in range(3):
                for column in range(3):
                    matrix_world[row][column] = high.matrix_world[row][column]
            low.matrix_world = matrix_world
            bpy.context.view_layer.update()

        low_bounds_after_linear_restore = bounds_payload([low])
        pre_alignment_dimension_errors = dimension_relative_errors(
            high_bounds["dimensions"],
            low_bounds_after_linear_restore["dimensions"],
            transform_tolerance,
        )
        maximum_pre_alignment_dimension_error = max(pre_alignment_dimension_errors)

        envelope_scale_factors: list[float] = []
        for high_dimension, low_dimension in zip(
            high_bounds["dimensions"],
            low_bounds_after_linear_restore["dimensions"],
            strict=True,
        ):
            if abs(low_dimension) <= transform_tolerance:
                raise RuntimeError(f"generated low has a collapsed dimension: {low_name}")
            factor = float(high_dimension / low_dimension)
            if not math.isfinite(factor) or factor <= 0.0:
                raise RuntimeError(f"generated low has an invalid scale factor: {low_name}")
            envelope_scale_factors.append(factor)
        envelope_scale_required = any(
            abs(factor - 1.0) > linear_transform_tolerance for factor in envelope_scale_factors
        )
        if envelope_scale_required:
            high_center = Vector(high_bounds["center"])
            low_center = Vector(low_bounds_after_linear_restore["center"])
            envelope_scale = Matrix.Diagonal(Vector((*envelope_scale_factors, 1.0)))
            low.matrix_world = (
                Matrix.Translation(high_center)
                @ envelope_scale
                @ Matrix.Translation(-low_center)
                @ low.matrix_world
            )
            bpy.context.view_layer.update()

        low_bounds_after_envelope_restore = bounds_payload([low])
        expected_low_world_linear = matrix_values(low, 3)
        dimension_errors = dimension_relative_errors(
            high_bounds["dimensions"],
            low_bounds_after_envelope_restore["dimensions"],
            transform_tolerance,
        )
        maximum_dimension_error = max(dimension_errors)
        if maximum_dimension_error > MAXIMUM_DIMENSION_RELATIVE_ERROR:
            raise RuntimeError(
                "generated low envelope restoration did not match the source high; "
                f"{low_name}; maximum_relative_error={maximum_dimension_error:.9g}, "
                f"limit={MAXIMUM_DIMENSION_RELATIVE_ERROR:.9g}"
            )
        delta = Vector(high_bounds["center"]) - Vector(low_bounds_after_envelope_restore["center"])
        translation_required = max(abs(value) for value in delta) > transform_tolerance
        if translation_required:
            matrix_world = low.matrix_world.copy()
            matrix_world.translation = matrix_world.translation + delta
            low.matrix_world = matrix_world
            bpy.context.view_layer.update()

        low_bounds_after = bounds_payload([low])
        high_after = object_signature(high, include_translation=True)
        low_world_linear_after = matrix_values(low, 3)
        center_residual = max_vector_delta(high_bounds["center"], low_bounds_after["center"])
        dimensions_change = max_vector_delta(
            low_bounds_before["dimensions"], low_bounds_after["dimensions"]
        )
        linear_transform_changed = linear_transform_required or envelope_scale_required
        linear_transform_residual = max_matrix_delta(
            expected_low_world_linear, low_world_linear_after
        )
        if high_after != high_signature:
            raise RuntimeError(f"coordinate restoration changed the high mesh: {high_name}")
        if mesh_sha256(low) != low_mesh_sha256:
            raise RuntimeError(
                f"coordinate restoration changed low mesh topology or geometry: {low_name}"
            )
        if (
            center_residual > transform_tolerance
            or linear_transform_residual > linear_transform_tolerance
        ):
            raise RuntimeError(
                "coordinate restoration did not reproduce the high transform: "
                f"{low_name}; center_residual={center_residual:.9g}, "
                f"linear_transform_residual={linear_transform_residual:.9g}, "
                f"tolerance={transform_tolerance:.9g}"
            )
        if linear_transform_changed and translation_required:
            coordinate_action = "full_transform_restored"
        elif linear_transform_changed:
            coordinate_action = "linear_transform_restored"
        elif translation_required:
            coordinate_action = "translation_restored"
        else:
            coordinate_action = "unchanged"
        records.append(
            {
                "high_object": high_name,
                "low_object": low_name,
                "coordinate_action": coordinate_action,
                "translation_applied": (
                    vector_values(delta) if translation_required else [0.0, 0.0, 0.0]
                ),
                "world_linear_before": low_world_linear_before,
                "world_linear_after": low_world_linear_after,
                "authoritative_high_world_linear": high_world_linear,
                "linear_transform_max_abs_delta_before": linear_transform_delta,
                "linear_transform_max_abs_residual": linear_transform_residual,
                "linear_transform_tolerance": linear_transform_tolerance,
                "high_bounds": high_bounds,
                "low_bounds_before": low_bounds_before,
                "low_bounds_after_linear_restore": low_bounds_after_linear_restore,
                "low_bounds_after_envelope_restore": low_bounds_after_envelope_restore,
                "low_bounds_after": low_bounds_after,
                "center_residual": center_residual,
                "dimensions_max_abs_change": dimensions_change,
                "pre_alignment_dimension_relative_error": pre_alignment_dimension_errors,
                "pre_alignment_maximum_dimension_relative_error": (
                    maximum_pre_alignment_dimension_error
                ),
                "envelope_scale_applied": envelope_scale_required,
                "envelope_scale_factors_world": envelope_scale_factors,
                "high_low_dimension_relative_error": dimension_errors,
                "high_low_maximum_dimension_relative_error": maximum_dimension_error,
                "maximum_dimension_relative_error_limit": (MAXIMUM_DIMENSION_RELATIVE_ERROR),
                "transform_tolerance": transform_tolerance,
                "high_preserved": True,
                "low_mesh_preserved": True,
                "low_rotation_scale_preserved": not linear_transform_changed,
                "low_rotation_scale_restored": linear_transform_changed,
            }
        )
        low_objects.append(low)

    blend_translation_changed = any(
        record["coordinate_action"] in {"translation_restored", "full_transform_restored"}
        for record in records
    )
    blend_linear_transform_changed = any(
        record["coordinate_action"] in {"linear_transform_restored", "full_transform_restored"}
        for record in records
    )
    blend_transform_changed = blend_translation_changed or blend_linear_transform_changed
    aligned_union_bounds = bounds_payload(low_objects)
    if blend_transform_changed:
        args.output_blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output_blend), check_existing=False)
    output_blend_sha256 = file_sha256(args.output_blend)
    fbx = export_and_read_back(args.output_fbx, low_objects, aligned_union_bounds)
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "passed": True,
        "allowed_change": "generated_low_object_world_transform_only",
        "maximum_dimension_relative_error": MAXIMUM_DIMENSION_RELATIVE_ERROR,
        "input_blend_sha256": input_blend_sha256,
        "output_blend_sha256": output_blend_sha256,
        "source_high_preserved": True,
        "blend_translation_changed": blend_translation_changed,
        "blend_linear_transform_changed": blend_linear_transform_changed,
        "blend_transform_changed": blend_transform_changed,
        "pairs": records,
        "fbx_readback": fbx,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
