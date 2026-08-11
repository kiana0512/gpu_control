#!/usr/bin/env python3
"""Finalize same-job retopology coordinates without changing topology or UVs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import traceback
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector


HIGH_PREFIX = "ALIGN_HIGH_"
LOW_PREFIX = "ALIGN_LOW_"
LOW_MATERIAL = "MAT_LOW_OPAQUE_YELLOW"
REQUIRED_COORDINATE_SPACE = "source_high_local"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-blend", required=True)
    parser.add_argument("--generation-report", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report")
    parser.add_argument("--max-size-error-ratio", type=float, default=0.15)
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def matrix_rows(matrix: Matrix) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def matrix_max_error(left: Matrix, right: Matrix) -> float:
    return max(abs(float(left[row][col] - right[row][col])) for row in range(4) for col in range(4))


def determinant_sign(matrix: Matrix) -> int:
    determinant = float(matrix.to_3x3().determinant())
    if abs(determinant) <= 1e-12:
        raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: singular object matrix")
    return 1 if determinant > 0.0 else -1


def mesh_objects(objects: Iterable[bpy.types.Object]) -> list[bpy.types.Object]:
    return [obj for obj in objects if obj.type == "MESH"]


def world_bounds(objects: Iterable[bpy.types.Object]) -> dict[str, list[float]]:
    points = [obj.matrix_world @ vertex.co for obj in mesh_objects(objects) for vertex in obj.data.vertices]
    if not points:
        raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: empty mesh role")
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    center = (minimum + maximum) * 0.5
    size = maximum - minimum
    return {
        "minimum": [float(value) for value in minimum],
        "maximum": [float(value) for value in maximum],
        "center": [float(value) for value in center],
        "size": [float(value) for value in size],
    }


def vector(value: list[float]) -> Vector:
    return Vector(tuple(float(item) for item in value))


def topology_uv_fingerprint(objects: Iterable[bpy.types.Object]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for obj in mesh_objects(objects):
        mesh = obj.data
        digest = hashlib.sha256()
        for polygon in mesh.polygons:
            digest.update(struct.pack("<I", len(polygon.vertices)))
            for vertex_index in polygon.vertices:
                digest.update(struct.pack("<I", int(vertex_index)))
        uv_digest = hashlib.sha256()
        uv_layers = []
        for layer in mesh.uv_layers:
            uv_layers.append(layer.name)
            uv_digest.update(layer.name.encode("utf-8"))
            for loop in layer.data:
                uv_digest.update(struct.pack("<2d", float(loop.uv.x), float(loop.uv.y)))
        records.append(
            {
                "object": obj.name,
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
                "loops": len(mesh.loops),
                "topology_hash": digest.hexdigest(),
                "uv_layers": uv_layers,
                "uv_hash": uv_digest.hexdigest(),
                "material_slots": [slot.material.name if slot.material else None for slot in obj.material_slots],
                "vertex_groups": [group.name for group in obj.vertex_groups],
                "shape_keys": [block.name for block in mesh.shape_keys.key_blocks] if mesh.shape_keys else [],
            }
        )
    return {"object_count": len(records), "meshes": records}


def structure_summary(objects: Iterable[bpy.types.Object]) -> dict[str, Any]:
    records = []
    for obj in mesh_objects(objects):
        mesh = obj.data
        records.append(
            {
                "vertices": len(mesh.vertices),
                "polygons": len(mesh.polygons),
                "loops": len(mesh.loops),
                "polygon_sizes": sorted(len(polygon.vertices) for polygon in mesh.polygons),
                "uv_layer_count": len(mesh.uv_layers),
                "material_slot_count": len(obj.material_slots),
            }
        )
    return {"object_count": len(records), "meshes": records}


def topology_metrics(obj: bpy.types.Object) -> dict[str, Any]:
    """Measure bake-safety defects without changing the generated low."""

    mesh = obj.data
    diagonal = max(float(obj.dimensions.length), 1.0e-9)
    area_tolerance = diagonal * diagonal * 1.0e-12
    duplicate_tolerance = max(diagonal * 1.0e-8, 1.0e-10)
    coordinate_keys = {
        tuple(int(round(float(value) / duplicate_tolerance)) for value in vertex.co)
        for vertex in mesh.vertices
    }
    face_keys = [
        tuple(sorted(int(index) for index in polygon.vertices))
        for polygon in mesh.polygons
    ]
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        editable.verts.ensure_lookup_table()
        editable.edges.ensure_lookup_table()
        editable.faces.ensure_lookup_table()
        boundary_edges = sum(len(edge.link_faces) == 1 for edge in editable.edges)
        multi_face_nonmanifold_edges = sum(
            len(edge.link_faces) > 2 for edge in editable.edges
        )
        loose_edges = sum(not edge.link_faces for edge in editable.edges)
        loose_vertices = sum(not vertex.link_edges for vertex in editable.verts)
        inconsistent_orientation_edges = sum(
            edge.is_manifold and not edge.is_contiguous for edge in editable.edges
        )
        remaining = set(editable.faces)
        face_component_sizes: list[int] = []
        while remaining:
            seed = remaining.pop()
            stack = [seed]
            size = 0
            while stack:
                face = stack.pop()
                size += 1
                for edge in face.edges:
                    for linked in edge.link_faces:
                        if linked in remaining:
                            remaining.remove(linked)
                            stack.append(linked)
            face_component_sizes.append(size)
    finally:
        editable.free()
    face_edges = max(len(mesh.edges) - loose_edges, 0)
    return {
        "object": obj.name,
        "finite_coordinates": all(
            math.isfinite(float(value))
            for vertex in mesh.vertices
            for value in vertex.co
        ),
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "triangles": sum(max(len(polygon.vertices) - 2, 1) for polygon in mesh.polygons),
        "uv_layers": len(mesh.uv_layers),
        "boundary_edges": boundary_edges,
        "boundary_edge_ratio": (
            float(boundary_edges) / float(face_edges) if face_edges else 0.0
        ),
        "multi_face_nonmanifold_edges": multi_face_nonmanifold_edges,
        "loose_edges": loose_edges,
        "loose_vertices": loose_vertices,
        "duplicate_vertices": len(mesh.vertices) - len(coordinate_keys),
        "duplicate_faces": len(face_keys) - len(set(face_keys)),
        "degenerate_faces": sum(
            float(polygon.area) <= area_tolerance for polygon in mesh.polygons
        ),
        "inconsistent_orientation_edges": inconsistent_orientation_edges,
        "face_components": len(face_component_sizes),
        "tiny_face_components": sum(size <= 4 for size in face_component_sizes),
    }


def topology_failures(
    high: dict[str, Any],
    low: dict[str, Any],
    *,
    require_unique_vertex_positions: bool,
) -> list[str]:
    failures: list[str] = []
    if low["faces"] <= 0:
        failures.append("EMPTY_LOW")
    if low["faces"] >= high["faces"]:
        failures.append("LOW_FACE_COUNT_NOT_BELOW_HIGH")
    if not low["finite_coordinates"]:
        failures.append("NON_FINITE_COORDINATES")
    if low["uv_layers"] < 1:
        failures.append("MISSING_UV")
    for field in (
        "boundary_edges",
        "multi_face_nonmanifold_edges",
        "loose_edges",
        "loose_vertices",
        "duplicate_faces",
        "degenerate_faces",
        "inconsistent_orientation_edges",
    ):
        if low[field]:
            failures.append(f"{field.upper()}={low[field]}")
    if require_unique_vertex_positions and low["duplicate_vertices"]:
        failures.append(f"DUPLICATE_VERTICES={low['duplicate_vertices']}")
    return failures


def require_clean_topology(
    highs: list[bpy.types.Object],
    lows: list[bpy.types.Object],
    *,
    stage: str,
    require_unique_vertex_positions: bool,
) -> dict[str, Any]:
    if len(highs) != len(lows) or not highs:
        raise RuntimeError("RETOPOLOGY_TOPOLOGY_INVALID: bake pair count mismatch")
    records = []
    all_failures: list[str] = []
    for index, (high, low) in enumerate(zip(highs, lows, strict=True)):
        high_metrics = topology_metrics(high)
        low_metrics = topology_metrics(low)
        failures = topology_failures(
            high_metrics,
            low_metrics,
            require_unique_vertex_positions=require_unique_vertex_positions,
        )
        records.append(
            {
                "pair": index,
                "high": high_metrics,
                "low": low_metrics,
                "failures": failures,
            }
        )
        all_failures.extend(f"pair_{index}:{failure}" for failure in failures)
    result = {
        "stage": stage,
        "passed": not all_failures,
        "require_unique_vertex_positions": require_unique_vertex_positions,
        "pairs": records,
        "failures": all_failures,
    }
    if all_failures:
        raise RuntimeError(
            "RETOPOLOGY_TOPOLOGY_INVALID: "
            + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        )
    return result


def ensure_opaque_low_display(objects: Iterable[bpy.types.Object]) -> bool:
    material = bpy.data.materials.get(LOW_MATERIAL)
    if material is None:
        material = bpy.data.materials.new(LOW_MATERIAL)
        material.diffuse_color = (1.0, 0.24, 0.015, 1.0)
        material.use_nodes = True
        node = material.node_tree.nodes.get("Principled BSDF") if material.node_tree else None
        if node is not None:
            node.inputs["Base Color"].default_value = (1.0, 0.18, 0.01, 1.0)
            node.inputs["Alpha"].default_value = 1.0
            node.inputs["Roughness"].default_value = 0.55
    added = False
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.display_type = "SOLID"
        obj.show_in_front = False
        obj.show_wire = True
        obj.show_all_edges = True
        obj.color = (1.0, 0.24, 0.015, 1.0)
        if len(obj.material_slots) == 0:
            obj.data.materials.append(material)
            added = True
    return added


def configure_high_display(objects: Iterable[bpy.types.Object]) -> None:
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.display_type = "SOLID"
        obj.show_in_front = False
        obj.show_wire = False
        obj.color = (0.28, 0.32, 0.38, 1.0)


def normalize_low_to_high(low: bpy.types.Object, high: bpy.types.Object) -> dict[str, Any]:
    original_high = high.matrix_world.copy()
    original_low = low.matrix_world.copy()
    high_sign = determinant_sign(original_high)
    low_sign = determinant_sign(original_low)
    conversion = original_high.inverted_safe() @ original_low
    low.data = low.data.copy()
    low.data.transform(conversion)
    low.parent = None
    low.matrix_world = original_high.copy()
    bpy.context.view_layer.update()

    high_before = world_bounds([high])
    low_before = world_bounds([low])
    delta_world = vector(high_before["center"]) - vector(low_before["center"])
    delta_local = original_high.inverted_safe().to_3x3() @ delta_world
    if delta_local.length > 0.0:
        for vertex in low.data.vertices:
            vertex.co += delta_local
        low.data.update()
        bpy.context.view_layer.update()

    high_after = world_bounds([high])
    low_after = world_bounds([low])
    reference = max(vector(high_after["size"]).length, 1e-12)
    center_error = (vector(high_after["center"]) - vector(low_after["center"])).length / reference
    size_error = (vector(high_after["size"]) - vector(low_after["size"])).length / reference
    return {
        "source_high_matrix_world": matrix_rows(original_high),
        "source_low_matrix_world": matrix_rows(original_low),
        "candidate_to_source_local": matrix_rows(conversion),
        "removed_center_offset_world": [float(value) for value in delta_world],
        "matrix_error_after": matrix_max_error(low.matrix_world, high.matrix_world),
        "center_error_ratio": float(center_error),
        "size_error_ratio": float(size_error),
        "high_determinant_sign": high_sign,
        "low_determinant_sign_before": low_sign,
        "low_determinant_sign_after": determinant_sign(low.matrix_world),
        "high_bounds": high_after,
        "low_bounds": low_after,
    }


def reverse_faces(mesh: bpy.types.Mesh) -> None:
    editable = bmesh.new()
    editable.from_mesh(mesh)
    bmesh.ops.reverse_faces(editable, faces=list(editable.faces))
    editable.to_mesh(mesh)
    editable.free()
    mesh.update()


def bake_world_transforms(objects: Iterable[bpy.types.Object]) -> None:
    for obj in objects:
        world = obj.matrix_world.copy()
        reflected = determinant_sign(world) < 0
        obj.parent = None
        obj.data = obj.data.copy()
        obj.data.transform(world)
        obj.matrix_world = Matrix.Identity(4)
        if reflected:
            reverse_faces(obj.data)
        obj.data.update()


def export_fbx(objects: list[bpy.types.Object], path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    result = bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=False,
        bake_anim=False,
        add_leaf_bones=False,
        apply_unit_scale=True,
        use_space_transform=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="AUTO",
    )
    if "FINISHED" not in result or not path.is_file():
        raise RuntimeError(f"FBX export failed: {path}")


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_fbx(path: Path) -> list[bpy.types.Object]:
    before = {obj.as_pointer() for obj in bpy.context.scene.objects}
    result = bpy.ops.import_scene.fbx(filepath=str(path))
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX readback failed: {path}")
    return mesh_objects(obj for obj in bpy.context.scene.objects if obj.as_pointer() not in before)


def bounds_error(actual: dict[str, list[float]], expected: dict[str, list[float]], reference: float) -> float:
    return max(
        (vector(actual[field]) - vector(expected[field])).length / reference
        for field in ("center", "size")
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def save_blend(path: Path) -> None:
    partial = path.with_name(path.stem + ".partial.blend")
    if partial.exists():
        partial.unlink()
    result = bpy.ops.wm.save_as_mainfile(filepath=str(partial), check_existing=False, compress=False)
    if "FINISHED" not in result or not partial.is_file():
        raise RuntimeError("Blender did not create the aligned Blend")
    os.replace(partial, path)


def main() -> int:
    args = parse_arguments()
    input_blend = Path(args.input_blend).expanduser().resolve()
    generation_report_path = Path(args.generation_report).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve() if args.report else output_dir / "bake_alignment_report.json"
    if not input_blend.is_file() or input_blend.suffix.lower() != ".blend":
        raise RuntimeError("Input must be an existing Blend file")
    if not generation_report_path.is_file():
        raise RuntimeError("generation_report.json is missing")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"Refusing to use non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    generation_report = json.loads(generation_report_path.read_text(encoding="utf-8"))
    assets = generation_report.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("generation report has no assets")
    bpy.ops.wm.open_mainfile(filepath=str(input_blend), load_ui=False)

    pairs: list[tuple[bpy.types.Object, bpy.types.Object, dict[str, Any]]] = []
    used_names: set[str] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise RuntimeError(f"generation report asset {index} is invalid")
        if asset.get("coordinate_space") != REQUIRED_COORDINATE_SPACE:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: coordinate_space must be source_high_local")
        if asset.get("coordinate_authority") != "high_object_matrix_world":
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: coordinate authority is missing")
        if asset.get("presentation_offset_applied") is not False:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: server low retained a presentation offset")
        high = bpy.data.objects.get(str(asset.get("high_object", "")))
        low = bpy.data.objects.get(str(asset.get("low_object", "")))
        if high is None or low is None or high.type != "MESH" or low.type != "MESH" or high == low:
            raise RuntimeError(f"RETOPOLOGY_COORDINATE_MISMATCH: cannot resolve pair {index}")
        if high.name in used_names or low.name in used_names:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: object appears in multiple pairs")
        used_names.update((high.name, low.name))
        pairs.append((high, low, asset))

    keep = {obj.as_pointer() for pair in pairs for obj in pair[:2]}
    for obj in list(bpy.context.scene.objects):
        if obj.as_pointer() not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)

    coordinate_records = []
    topology_before = topology_uv_fingerprint(low for _, low, _ in pairs)
    for index, (high, low, _) in enumerate(pairs):
        record = normalize_low_to_high(low, high)
        if record["matrix_error_after"] > 1e-5 or record["center_error_ratio"] > 1e-5:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: matrix or center gate failed")
        if record["size_error_ratio"] > args.max_size_error_ratio:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: generated low size gate failed")
        if record["high_determinant_sign"] != record["low_determinant_sign_after"]:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: handedness changed")
        high.name = f"{HIGH_PREFIX}{index:03d}"
        low.name = f"{LOW_PREFIX}{index:03d}"
        record["delivered_high_name"] = high.name
        record["delivered_low_name"] = low.name
        coordinate_records.append(record)

    high_objects = [high for high, _, _ in pairs]
    low_objects = [low for _, low, _ in pairs]
    low_material_added = ensure_opaque_low_display(low_objects)
    configure_high_display(high_objects)
    bpy.context.scene["li3d_coordinate_authority"] = "high_object_matrix_world"
    bpy.context.scene["li3d_alignment_mode"] = "source_matrix_restore"
    bpy.context.scene["li3d_low_display"] = "opaque_yellow"

    bake_world_transforms(high_objects + low_objects)
    topology_after_bake = topology_uv_fingerprint(low_objects)
    if topology_before["meshes"] != topology_after_bake["meshes"]:
        # Object names and optional presentation material may differ; compare invariant fields below.
        invariant_fields = ("vertices", "edges", "polygons", "loops", "topology_hash", "uv_layers", "uv_hash", "vertex_groups", "shape_keys")
        before_records = topology_before["meshes"]
        after_records = topology_after_bake["meshes"]
        if len(before_records) != len(after_records) or any(
            any(before.get(field) != after.get(field) for field in invariant_fields)
            for before, after in zip(before_records, after_records, strict=True)
        ):
            raise RuntimeError("LOW_TOPOLOGY_OR_UV_CHANGED")

    topology_validation = {
        "schema": "li3d-retopology-topology-v1",
        "generated_blend": require_clean_topology(
            high_objects,
            low_objects,
            stage="generated_blend_after_coordinate_bake",
            require_unique_vertex_positions=True,
        ),
    }

    expected_high_bounds = world_bounds(high_objects)
    expected_low_bounds = world_bounds(low_objects)
    expected_low_structure = structure_summary(low_objects)
    blend_path = output_dir / "bake_alignment.blend"
    high_fbx = output_dir / "bake_high.fbx"
    low_fbx = output_dir / "bake_low.fbx"
    save_blend(blend_path)
    export_fbx(high_objects, high_fbx)
    export_fbx(low_objects, low_fbx)

    bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)
    blend_high = sorted([obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name.startswith(HIGH_PREFIX)], key=lambda item: item.name)
    blend_low = sorted([obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name.startswith(LOW_PREFIX)], key=lambda item: item.name)
    topology_after_blend_readback = topology_uv_fingerprint(blend_low)
    if topology_after_blend_readback != topology_after_bake:
        raise RuntimeError("LOW_TOPOLOGY_OR_UV_CHANGED")
    topology_validation["blend_readback"] = require_clean_topology(
        blend_high,
        blend_low,
        stage="saved_blend_readback",
        require_unique_vertex_positions=True,
    )

    clear_scene()
    readback_high = import_fbx(high_fbx)
    actual_high_bounds = world_bounds(readback_high)
    readback_high = sorted(readback_high, key=lambda item: item.name)
    readback_low = import_fbx(low_fbx)
    actual_low_bounds = world_bounds(readback_low)
    readback_low = sorted(readback_low, key=lambda item: item.name)
    actual_low_structure = structure_summary(readback_low)
    topology_validation["fbx_readback"] = require_clean_topology(
        readback_high,
        readback_low,
        stage="fresh_fbx_readback",
        # FBX may split a used vertex at a legitimate UV or normal seam.  The
        # generated Blend is authoritative for duplicate positions; fresh FBX
        # still must contain no unused vertices, loose edges, or invalid faces.
        require_unique_vertex_positions=False,
    )
    topology_validation["passed"] = True
    reference = max(vector(expected_high_bounds["size"]).length, 1e-12)
    high_error = bounds_error(actual_high_bounds, expected_high_bounds, reference)
    low_error = bounds_error(actual_low_bounds, expected_low_bounds, reference)
    readback_pass = high_error <= 1e-5 and low_error <= 1e-5 and actual_low_structure == expected_low_structure
    if not readback_pass:
        raise RuntimeError("EXPORT_READBACK_MISMATCH")

    report = {
        "schema": "li3d-auto-retopo-align-v1",
        "pass": True,
        "transform_only_alignment": True,
        "alignment_mode": "source_matrix_restore",
        "coordinate_authority": "high",
        "icp_used": False,
        "topology_or_uv_edited": False,
        "low_material_added_when_empty": low_material_added,
        "low_display": "opaque_yellow",
        "pairs": coordinate_records,
        "low_fingerprint_before": topology_before,
        "low_fingerprint_after_bake": topology_after_bake,
        "low_fingerprint_after_blend_readback": topology_after_blend_readback,
        "topology_uv_unchanged": True,
        "topology_validation": topology_validation,
        "fbx_readback": {
            "pass": True,
            "high_center_size_error_ratio": float(high_error),
            "low_center_size_error_ratio": float(low_error),
            "tolerance": 1e-5,
            "low_structure_match": True,
            "expected_low_structure": expected_low_structure,
            "actual_low_structure": actual_low_structure,
        },
        "outputs": {
            "blend": str(blend_path),
            "high_fbx": str(high_fbx),
            "low_fbx": str(low_fbx),
        },
    }
    write_json(report_path, report)
    print("LI3D_AUTO_RETOPO_ALIGN_OK:" + json.dumps({"pairs": len(pairs), "report": str(report_path)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001
        print(f"LI3D_AUTO_RETOPO_ALIGN_ERROR:{error}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
