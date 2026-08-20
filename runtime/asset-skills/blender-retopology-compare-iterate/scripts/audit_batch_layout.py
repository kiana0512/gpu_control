from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--low-prefix", default="NEXTGEN_")
    parser.add_argument("--low-suffix", default="_FINAL")
    parser.add_argument("--expected", type=int, default=0)
    parser.add_argument("--row-offset", nargs=3, type=float, default=(0.0, 0.0, 0.0))
    parser.add_argument("--source-high-property", default="source_high")
    parser.add_argument("--asset-id-property", default="asset_id")
    parser.add_argument(
        "--expected-asset-ids",
        nargs="*",
        default=None,
        help="Explicit manifest order, for example H01 H02 H03",
    )
    parser.add_argument("--role-key", default="codex_role")
    parser.add_argument("--role-value", default="LOW")
    parser.add_argument("--tolerance", type=float, default=1.0e-5)
    parser.add_argument(
        "--baseline",
        help="Earlier v3 layout audit captured before presentation translation",
    )
    parser.add_argument(
        "--strict-final",
        action="store_true",
        help="Require ordered asset IDs, baseline comparison, static self-contained visible lows, no overlap, and no N-gons",
    )
    parser.add_argument("--require-static", action="store_true")
    parser.add_argument("--require-no-dependencies", action="store_true")
    parser.add_argument("--require-visible", action="store_true")
    parser.add_argument("--reject-low-overlap", action="store_true")
    parser.add_argument("--reject-ngons", action="store_true")
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def world_bounds(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    points = [evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box]
    if not points:
        raise RuntimeError(f"Empty mesh: {obj.name}")
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    return minimum, maximum, (minimum + maximum) * 0.5, maximum - minimum


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def rounded_vector(values, digits=9):
    return [round(float(value), digits) for value in values]


def rounded_matrix(matrix, digits=9):
    return [rounded_vector(row, digits) for row in matrix]


def mesh_fingerprint(mesh):
    signature = {
        "vertices": [rounded_vector(vertex.co) for vertex in mesh.vertices],
        "edges": [list(edge.vertices) for edge in mesh.edges],
        "polygons": [
            {
                "vertices": list(polygon.vertices),
                "material_index": polygon.material_index,
                "use_smooth": polygon.use_smooth,
            }
            for polygon in mesh.polygons
        ],
    }
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def animation_summary(data_block):
    animation_data = getattr(data_block, "animation_data", None)
    if animation_data is None:
        return {
            "has_animation_data": False,
            "action": None,
            "nla_tracks": [],
            "drivers": [],
        }
    return {
        "has_animation_data": True,
        "action": animation_data.action.name_full if animation_data.action else None,
        "nla_tracks": [track.name for track in animation_data.nla_tracks],
        "drivers": [
            {
                "data_path": driver.data_path,
                "array_index": driver.array_index,
            }
            for driver in animation_data.drivers
        ],
    }


def layer_collection_render_paths(root):
    result = {}

    def visit(layer_collection, blocked):
        collection = layer_collection.collection
        blocked_here = bool(
            blocked
            or layer_collection.exclude
            or layer_collection.hide_viewport
            or layer_collection.holdout
            or layer_collection.indirect_only
            or collection.hide_render
        )
        result.setdefault(collection.name_full, []).append(not blocked_here)
        for child in layer_collection.children:
            visit(child, blocked_here)

    visit(root, False)
    return result


def is_visible(obj, scene, view_layer, collection_paths):
    try:
        visible_get = bool(obj.visible_get(view_layer=view_layer))
    except (RuntimeError, TypeError):
        visible_get = not obj.hide_viewport
    try:
        hidden_get = bool(obj.hide_get(view_layer=view_layer))
    except (RuntimeError, TypeError):
        hidden_get = bool(obj.hide_viewport)
    collection_names = sorted(collection.name_full for collection in obj.users_collection)
    active_collection_path = any(
        any(collection_paths.get(collection_name, [])) for collection_name in collection_names
    )
    in_scene = obj.name in scene.objects
    in_view_layer = obj.name in view_layer.objects
    visible_camera = bool(getattr(obj, "visible_camera", True))
    render_eligible = bool(
        in_scene
        and in_view_layer
        and active_collection_path
        and not obj.hide_render
        and visible_camera
    )
    return {
        "viewport_visible_get": visible_get,
        "viewport_hide_get": hidden_get,
        "hide_viewport": bool(obj.hide_viewport),
        "hide_render": bool(obj.hide_render),
        "visible_camera": visible_camera,
        "in_scene": in_scene,
        "in_view_layer": in_view_layer,
        "collections": collection_names,
        "active_collection_render_path": active_collection_path,
        "render_eligible": render_eligible,
        "passed": bool(
            render_eligible and visible_get and not hidden_get and not obj.hide_viewport
        ),
    }


def evaluated_triangle_count(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        evaluated_mesh.calc_loop_triangles()
        return len(evaluated_mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()


def max_abs_delta(left, right):
    if left is None or right is None:
        return None
    left_flat = [value for row in left for value in row] if left and isinstance(left[0], list) else left
    right_flat = [value for row in right for value in row] if right and isinstance(right[0], list) else right
    if len(left_flat) != len(right_flat):
        return None
    return max((abs(float(a) - float(b)) for a, b in zip(left_flat, right_flat)), default=0.0)


def aabb_overlap(bounds_a, bounds_b, tolerance):
    overlap = [
        min(bounds_a[1][axis], bounds_b[1][axis])
        - max(bounds_a[0][axis], bounds_b[0][axis])
        for axis in range(3)
    ]
    return all(value > tolerance for value in overlap), overlap


def main():
    args = parse_args()
    if args.strict_final:
        if not args.expected_asset_ids:
            raise RuntimeError("--strict-final requires --expected-asset-ids in manifest order")
        if not args.baseline:
            raise RuntimeError("--strict-final requires --baseline from the aligned candidate state")
        args.require_static = True
        args.require_no_dependencies = True
        args.require_visible = True
        args.reject_low_overlap = True
        args.reject_ngons = True

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    scene.frame_set(scene.frame_current)
    view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    collection_paths = layer_collection_render_paths(view_layer.layer_collection)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    offset = Vector(tuple(args.row_offset))
    expected_asset_ids = args.expected_asset_ids or []
    expected_index = {asset_id: index for index, asset_id in enumerate(expected_asset_ids)}
    candidate_objects = [
        obj
        for obj in scene.objects
        if obj.type == "MESH"
        and obj.name.startswith(args.low_prefix)
        and obj.name.endswith(args.low_suffix)
    ]
    lows = sorted(
        candidate_objects,
        key=(
            lambda obj: (
                expected_index.get(str(obj.get(args.asset_id_property, "")), len(expected_index)),
                natural_key(obj.name),
            )
            if expected_asset_ids
            else natural_key(obj.name)
        ),
    )

    baseline_records = {}
    if args.baseline:
        baseline_payload = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline_records = {
            record["low"]: record for record in baseline_payload.get("records", [])
        }

    records = []
    global_failures = []
    if args.expected and len(lows) != args.expected:
        global_failures.append(f"expected_{args.expected}_lows_got_{len(lows)}")
    suffix_pattern = re.compile(re.escape(args.low_suffix) + r"\.\d{3}$")
    auto_suffixed = sorted(
        obj.name
        for obj in scene.objects
        if obj.type == "MESH"
        and obj.name.startswith(args.low_prefix)
        and suffix_pattern.search(obj.name)
    )
    if auto_suffixed:
        global_failures.append("automatic_name_suffix:" + ",".join(auto_suffixed))

    asset_ids = [str(low.get(args.asset_id_property, "")) for low in lows]
    if expected_asset_ids:
        missing = [asset_id for asset_id in expected_asset_ids if asset_id not in asset_ids]
        unexpected = [asset_id for asset_id in asset_ids if asset_id not in expected_index]
        duplicates = sorted({asset_id for asset_id in asset_ids if asset_ids.count(asset_id) > 1})
        if missing:
            global_failures.append("missing_asset_ids:" + ",".join(missing))
        if unexpected:
            global_failures.append("unexpected_asset_ids:" + ",".join(unexpected))
        if duplicates:
            global_failures.append("duplicate_asset_ids:" + ",".join(duplicates))

    source_highs = [str(low.get(args.source_high_property, "")) for low in lows]
    duplicate_highs = sorted({name for name in source_highs if name and source_highs.count(name) > 1})
    if duplicate_highs:
        global_failures.append("duplicate_source_highs:" + ",".join(duplicate_highs))

    for low in lows:
        failures = []
        high_name = str(low.get(args.source_high_property, ""))
        asset_id = str(low.get(args.asset_id_property, ""))
        high = scene.objects.get(high_name)
        if high is None or high.type != "MESH":
            failures.append("missing_paired_high")
            center_error = None
            high_dimensions = None
            high_bounds = None
        else:
            high_minimum, high_maximum, high_center, high_dimensions_vector = world_bounds(
                high, depsgraph
            )
            _, _, low_center, _ = world_bounds(low, depsgraph)
            center_error = float((low_center - (high_center + offset)).length)
            high_dimensions = [float(value) for value in high_dimensions_vector]
            high_bounds = {
                "minimum": rounded_vector(high_minimum, 6),
                "maximum": rounded_vector(high_maximum, 6),
            }
            if center_error > args.tolerance and not args.baseline:
                failures.append("row_center")

        mesh = low.data
        low_minimum, low_maximum, low_center, low_dimensions_vector = world_bounds(
            low, depsgraph
        )
        mesh.calc_loop_triangles()
        triangles_evaluated = evaluated_triangle_count(low, depsgraph)
        ngons = sum(len(polygon.vertices) > 4 for polygon in mesh.polygons)
        object_animation = animation_summary(low)
        mesh_animation = animation_summary(mesh)
        shape_key_animation = animation_summary(mesh.shape_keys) if mesh.shape_keys else animation_summary(None)
        object_drivers = len(object_animation["drivers"])
        mesh_drivers = len(mesh_animation["drivers"])
        shape_key_drivers = len(shape_key_animation["drivers"])
        visibility = is_visible(low, scene, view_layer, collection_paths)
        linear_matrix = rounded_matrix(low.matrix_world.to_3x3())
        world_translation = rounded_vector(low.matrix_world.translation)
        determinant = round(float(low.matrix_world.to_3x3().determinant()), 9)
        fingerprint = mesh_fingerprint(mesh)
        dependencies = {
            "parent": low.parent is not None,
            "constraints": len(low.constraints),
            "modifiers": [modifier.type for modifier in low.modifiers],
            "shape_keys": mesh.shape_keys is not None,
            "object_drivers": object_drivers,
            "mesh_drivers": mesh_drivers,
            "shape_key_drivers": shape_key_drivers,
            "object_library": low.library is not None,
            "mesh_library": mesh.library is not None,
            "object_library_indirect": bool(low.is_library_indirect),
            "mesh_library_indirect": bool(mesh.is_library_indirect),
            "object_library_override": low.override_library is not None,
            "mesh_library_override": mesh.override_library is not None,
        }

        if args.require_static and (
            object_animation["has_animation_data"]
            or mesh_animation["has_animation_data"]
            or shape_key_animation["has_animation_data"]
        ):
            failures.append("animation_data")
        if args.require_no_dependencies and (
            dependencies["parent"]
            or dependencies["constraints"]
            or dependencies["modifiers"]
            or dependencies["shape_keys"]
            or dependencies["object_drivers"]
            or dependencies["mesh_drivers"]
            or dependencies["shape_key_drivers"]
            or dependencies["object_library"]
            or dependencies["mesh_library"]
            or dependencies["object_library_indirect"]
            or dependencies["mesh_library_indirect"]
            or dependencies["object_library_override"]
            or dependencies["mesh_library_override"]
        ):
            failures.append("unresolved_dependencies")
        if args.require_visible and not visibility["passed"]:
            failures.append("visibility")
        if args.reject_ngons and ngons:
            failures.append("ngons")
        role = low.get(args.role_key)
        if args.role_value and role != args.role_value:
            failures.append("role_metadata")

        baseline = baseline_records.get(low.name) if args.baseline else None
        baseline_comparison = None
        if args.baseline:
            if baseline is None:
                failures.append("missing_baseline_record")
            else:
                fingerprint_match = baseline.get("mesh_fingerprint") == fingerprint
                dimension_delta = max_abs_delta(
                    baseline.get("low_dimensions"), rounded_vector(low_dimensions_vector)
                )
                matrix_delta = max_abs_delta(
                    baseline.get("matrix_world_3x3"), linear_matrix
                )
                baseline_center = baseline.get("low_bounds", {}).get("center")
                baseline_translation = baseline.get("matrix_world_translation")
                expected_center = (
                    [float(baseline_center[axis]) + float(offset[axis]) for axis in range(3)]
                    if baseline_center is not None
                    else None
                )
                expected_translation = (
                    [
                        float(baseline_translation[axis]) + float(offset[axis])
                        for axis in range(3)
                    ]
                    if baseline_translation is not None
                    else None
                )
                center_translation_delta = max_abs_delta(
                    expected_center, rounded_vector(low_center)
                )
                origin_translation_delta = max_abs_delta(
                    expected_translation, world_translation
                )
                baseline_comparison = {
                    "mesh_fingerprint_match": fingerprint_match,
                    "dimension_max_abs_delta": dimension_delta,
                    "matrix_3x3_max_abs_delta": matrix_delta,
                    "expected_aabb_center": expected_center,
                    "aabb_center_translation_max_abs_delta": center_translation_delta,
                    "expected_world_translation": expected_translation,
                    "world_translation_max_abs_delta": origin_translation_delta,
                }
                if not fingerprint_match:
                    failures.append("mesh_fingerprint_changed")
                if dimension_delta is None or dimension_delta > args.tolerance:
                    failures.append("dimensions_changed")
                if matrix_delta is None or matrix_delta > args.tolerance:
                    failures.append("linear_transform_changed")
                if (
                    center_translation_delta is None
                    or center_translation_delta > args.tolerance
                ):
                    failures.append("aabb_translation_changed")
                if (
                    origin_translation_delta is None
                    or origin_translation_delta > args.tolerance
                ):
                    failures.append("origin_translation_changed")

        records.append(
            {
                "low": low.name,
                "asset_id": asset_id,
                "high": high_name,
                "center_error": center_error,
                "high_dimensions": high_dimensions,
                "high_bounds": high_bounds,
                "low_bounds": {
                    "minimum": rounded_vector(low_minimum, 6),
                    "maximum": rounded_vector(low_maximum, 6),
                    "center": rounded_vector(low_center, 6),
                },
                "low_dimensions": rounded_vector(low_dimensions_vector),
                "matrix_world_3x3": linear_matrix,
                "matrix_world_translation": world_translation,
                "matrix_3x3_determinant": determinant,
                "mesh_fingerprint": fingerprint,
                "faces": len(mesh.polygons),
                "triangles_base": len(mesh.loop_triangles),
                "triangles_evaluated": triangles_evaluated,
                "ngons": ngons,
                "object_animation_data": object_animation,
                "mesh_animation_data": mesh_animation,
                "shape_key_animation_data": shape_key_animation,
                "visibility": visibility,
                "role": role,
                "dependencies": dependencies,
                "baseline_comparison": baseline_comparison,
                "failures": failures,
                "passed": not failures,
            }
        )

    if args.reject_low_overlap:
        for index, left in enumerate(records):
            left_bounds = (left["low_bounds"]["minimum"], left["low_bounds"]["maximum"])
            for right in records[index + 1 :]:
                right_bounds = (right["low_bounds"]["minimum"], right["low_bounds"]["maximum"])
                intersects, overlap = aabb_overlap(left_bounds, right_bounds, args.tolerance)
                if intersects:
                    failure = f"low_aabb_overlap:{left['low']}:{right['low']}:{rounded_vector(overlap, 6)}"
                    global_failures.append(failure)
                    left["failures"].append("low_aabb_overlap")
                    right["failures"].append("low_aabb_overlap")
        for record in records:
            record["passed"] = not record["failures"]

    if args.baseline:
        current_names = {record["low"] for record in records}
        extra_baseline = sorted(set(baseline_records) - current_names, key=natural_key)
        if extra_baseline:
            global_failures.append("missing_current_lows_from_baseline:" + ",".join(extra_baseline))

    report = {
        "schema": "blender-retopology-batch-layout-v3",
        "blend": bpy.data.filepath,
        "low_prefix": args.low_prefix,
        "low_suffix": args.low_suffix,
        "expected": args.expected,
        "expected_asset_ids": expected_asset_ids,
        "row_offset": [float(value) for value in offset],
        "tolerance": args.tolerance,
        "baseline": args.baseline,
        "scene_state": {
            "frame_current": bpy.context.scene.frame_current,
            "frame_start": bpy.context.scene.frame_start,
            "frame_end": bpy.context.scene.frame_end,
            "fps": bpy.context.scene.render.fps,
            "auto_key": bool(bpy.context.scene.tool_settings.use_keyframe_insert_auto),
            "view_layer": bpy.context.view_layer.name,
        },
        "global_failures": global_failures,
        "records": records,
        "totals": {
            "lows": len(records),
            "faces": sum(record["faces"] for record in records),
            "triangles_evaluated": sum(record["triangles_evaluated"] for record in records),
            "failed_lows": sum(not record["passed"] for record in records),
        },
        "mechanical_layout_pass": not global_failures and all(record["passed"] for record in records),
        "visual_quality_not_evaluated": True,
    }
    report["passed"] = report["mechanical_layout_pass"]
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "BATCH_LAYOUT_AUDIT",
        json.dumps(
            {
                "lows": report["totals"]["lows"],
                "faces": report["totals"]["faces"],
                "triangles_evaluated": report["totals"]["triangles_evaluated"],
                "failed_lows": report["totals"]["failed_lows"],
                "passed": report["passed"],
                "output": str(output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not report["passed"]:
        raise RuntimeError(f"Batch layout audit failed; see {output}")


if __name__ == "__main__":
    main()
