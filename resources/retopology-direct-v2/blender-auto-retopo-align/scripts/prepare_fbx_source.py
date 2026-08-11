#!/usr/bin/env python3
"""Import one static model into one task-local authoritative high object."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import traceback

import bmesh
import bpy
import mathutils


HIGH_OBJECT_NAME = "SOURCE_HIGH"
HIGH_MESH_NAME = "SOURCE_HIGH_MESH"
NORMALIZED_WORK_OBJECT_NAME = "SOURCE_HIGH_NORMALIZED_WORK"
NORMALIZED_WORK_MESH_NAME = "SOURCE_HIGH_NORMALIZED_WORK_MESH"
DIRECT_REDUCTION_MAX_NORMALIZED_COMPONENTS = 512
SAFE_AUXILIARY_TYPES = {"EMPTY", "CAMERA", "LIGHT"}
SUPPORTED_SOURCE_EXTENSIONS = {".fbx", ".glb", ".gltf", ".obj"}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(values)


def sha256_file(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matrix_rows(matrix: mathutils.Matrix) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def world_bounds(mesh_object: bpy.types.Object) -> dict[str, list[float]]:
    corners = [
        mesh_object.matrix_world @ mathutils.Vector(corner)
        for corner in mesh_object.bound_box
    ]
    minimum = [min(corner[index] for corner in corners) for index in range(3)]
    maximum = [max(corner[index] for corner in corners) for index in range(3)]
    return {
        "min": minimum,
        "max": maximum,
        "size": [maximum[index] - minimum[index] for index in range(3)],
    }


def combined_world_bounds(mesh_objects: list[bpy.types.Object]) -> dict[str, list[float]]:
    corners = [
        mesh_object.matrix_world @ mathutils.Vector(corner)
        for mesh_object in mesh_objects
        for corner in mesh_object.bound_box
    ]
    minimum = [min(corner[index] for corner in corners) for index in range(3)]
    maximum = [max(corner[index] for corner in corners) for index in range(3)]
    return {
        "min": minimum,
        "max": maximum,
        "size": [maximum[index] - minimum[index] for index in range(3)],
    }


def bounds_match(before: dict[str, list[float]], after: dict[str, list[float]]) -> bool:
    values = before["min"] + before["max"] + after["min"] + after["max"]
    tolerance = max(1.0, *(abs(value) for value in values)) * 1e-5
    return all(
        abs(before[key][index] - after[key][index]) <= tolerance
        for key in ("min", "max")
        for index in range(3)
    )


def source_topology(mesh_object: bpy.types.Object) -> dict[str, int | float | bool]:
    """Record fragmentation evidence without changing the authoritative high."""

    mesh = mesh_object.data
    parent = list(range(len(mesh.vertices)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        left = find(first)
        right = find(second)
        if left != right:
            parent[right] = left

    edge_face_counts: dict[tuple[int, int], int] = {}
    polygon_vertices: set[int] = set()
    zero_area_faces = 0
    finite_coordinates = True
    for vertex in mesh.vertices:
        finite_coordinates = finite_coordinates and all(
            math.isfinite(float(value)) for value in vertex.co
        )
    diagonal = max(float(mesh_object.dimensions.length), 1.0e-9)
    area_tolerance = diagonal * diagonal * 1.0e-12
    duplicate_tolerance = max(diagonal * 1.0e-8, 1.0e-10)
    coordinate_keys = {
        tuple(int(round(float(value) / duplicate_tolerance)) for value in vertex.co)
        for vertex in mesh.vertices
    }
    for polygon in mesh.polygons:
        indices = [int(index) for index in polygon.vertices]
        polygon_vertices.update(indices)
        zero_area_faces += int(float(polygon.area) <= area_tolerance)
        if indices:
            anchor = indices[0]
            for index in indices[1:]:
                union(anchor, index)
        for offset, first in enumerate(indices):
            second = indices[(offset + 1) % len(indices)]
            edge = tuple(sorted((first, second)))
            edge_face_counts[edge] = edge_face_counts.get(edge, 0) + 1
    edge_vertices = {int(index) for edge in mesh.edges for index in edge.vertices}
    used_vertices = polygon_vertices | edge_vertices
    components = len({find(index) for index in used_vertices}) if used_vertices else 0
    duplicate_vertices = len(mesh.vertices) - len(coordinate_keys)
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        inconsistent_orientation_edges = sum(
            edge.is_manifold and not edge.is_contiguous for edge in editable.edges
        )
    finally:
        editable.free()
    return {
        "finite_coordinates": finite_coordinates,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "face_components": components,
        "boundary_edges": sum(count == 1 for count in edge_face_counts.values()),
        "multi_face_nonmanifold_edges": sum(
            count > 2 for count in edge_face_counts.values()
        ),
        "loose_edges": sum(
            tuple(sorted((int(edge.vertices[0]), int(edge.vertices[1]))))
            not in edge_face_counts
            for edge in mesh.edges
        ),
        "loose_vertices": len(mesh.vertices) - len(used_vertices),
        "duplicate_vertices": duplicate_vertices,
        "duplicate_vertex_ratio": (
            float(duplicate_vertices) / float(len(mesh.vertices))
            if mesh.vertices
            else 0.0
        ),
        "zero_area_faces": zero_area_faces,
        "inconsistent_orientation_edges": inconsistent_orientation_edges,
    }


def build_normalized_work_copy(
    high_object: bpy.types.Object,
    original_topology: dict[str, int | float | bool],
    original_bounds: dict[str, list[float]],
) -> tuple[bpy.types.Object | None, dict]:
    """Build a qualified exact-weld work copy while preserving SOURCE_HIGH."""

    duplicate_vertices = int(original_topology["duplicate_vertices"])
    if duplicate_vertices <= 0:
        return None, {
            "created": False,
            "qualified": False,
            "reason": "source has no duplicate-position vertices",
        }

    work_object = high_object.copy()
    work_object.data = high_object.data.copy()
    work_object.name = NORMALIZED_WORK_OBJECT_NAME
    work_object.data.name = NORMALIZED_WORK_MESH_NAME
    work_object.parent = None
    work_object.matrix_world = high_object.matrix_world.copy()
    bpy.context.collection.objects.link(work_object)

    diagonal = max(float(work_object.dimensions.length), 1.0e-9)
    weld_tolerance = max(diagonal * 1.0e-8, 1.0e-10)
    editable = bmesh.new()
    try:
        editable.from_mesh(work_object.data)
        bmesh.ops.remove_doubles(
            editable,
            verts=list(editable.verts),
            dist=weld_tolerance,
        )
        editable.to_mesh(work_object.data)
    finally:
        editable.free()
    work_object.data.update()

    normalized_topology = source_topology(work_object)
    normalized_bounds = world_bounds(work_object)
    polygon_count_preserved = (
        normalized_topology["polygons"] == original_topology["polygons"]
    )
    world_bounds_preserved = bounds_match(original_bounds, normalized_bounds)
    qualified = bool(
        normalized_topology["finite_coordinates"]
        and polygon_count_preserved
        and world_bounds_preserved
        and normalized_topology["face_components"]
        <= DIRECT_REDUCTION_MAX_NORMALIZED_COMPONENTS
        and normalized_topology["boundary_edges"] == 0
        and normalized_topology["multi_face_nonmanifold_edges"] == 0
        and normalized_topology["loose_edges"] == 0
        and normalized_topology["loose_vertices"] == 0
        and normalized_topology["duplicate_vertices"] == 0
        and normalized_topology["zero_area_faces"] == 0
        and normalized_topology["inconsistent_orientation_edges"] == 0
    )
    evidence = {
        "created": True,
        "qualified": qualified,
        "object_name": NORMALIZED_WORK_OBJECT_NAME,
        "mesh_data_name": NORMALIZED_WORK_MESH_NAME,
        "method": "exact_position_weld_on_work_copy",
        "weld_tolerance": weld_tolerance,
        "source_high_unchanged": True,
        "polygon_count_preserved": polygon_count_preserved,
        "world_bounds_preserved": world_bounds_preserved,
        "vertices_removed": int(original_topology["vertices"])
        - int(normalized_topology["vertices"]),
        "topology": normalized_topology,
        "world_bounds": normalized_bounds,
    }
    if not qualified:
        bpy.data.objects.remove(work_object, do_unlink=True)
        return None, evidence

    work_object["li3d_role"] = "retopology_normalized_work"
    work_object["li3d_source_high"] = HIGH_OBJECT_NAME
    work_object.hide_set(True)
    work_object.hide_viewport = True
    work_object.hide_render = True
    return work_object, evidence


def write_json_atomic(file_path: str, payload: dict) -> None:
    partial = f"{file_path}.partial"
    with open(partial, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(partial, file_path)


def save_blend_atomic(file_path: str) -> None:
    partial = f"{file_path}.partial.blend"
    if os.path.exists(partial):
        os.remove(partial)
    result = bpy.ops.wm.save_as_mainfile(
        filepath=partial,
        check_existing=False,
        compress=False,
    )
    if "FINISHED" not in result or not os.path.isfile(partial):
        raise RuntimeError("Blender did not create the prepared Blend.")
    os.replace(partial, file_path)


def main() -> None:
    arguments = parse_arguments()
    input_path = os.path.abspath(arguments.input)
    output_path = os.path.abspath(arguments.output)
    manifest_path = os.path.abspath(arguments.manifest)
    source_format = os.path.splitext(input_path)[1].lower()
    if source_format not in SUPPORTED_SOURCE_EXTENSIONS:
        raise RuntimeError("Input must be an FBX, GLB, GLTF, or OBJ file.")
    if not os.path.isfile(input_path) or os.path.getsize(input_path) == 0:
        raise RuntimeError("Input model does not exist or is empty.")
    if os.path.splitext(output_path)[1].lower() != ".blend":
        raise RuntimeError("Output must use the .blend suffix.")
    if os.path.exists(output_path):
        raise RuntimeError("Refusing to overwrite an existing prepared Blend.")
    if os.path.normcase(input_path) == os.path.normcase(output_path):
        raise RuntimeError("Input and output paths must differ.")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    before_ids = {obj.as_pointer() for obj in bpy.context.scene.objects}
    if source_format == ".fbx":
        import_result = bpy.ops.import_scene.fbx(filepath=input_path)
    elif source_format in {".glb", ".gltf"}:
        import_result = bpy.ops.import_scene.gltf(filepath=input_path)
    else:
        import_result = bpy.ops.wm.obj_import(filepath=input_path)
    if "FINISHED" not in import_result:
        raise RuntimeError("Blender source importer did not finish.")
    imported = [
        obj for obj in bpy.context.scene.objects if obj.as_pointer() not in before_ids
    ]
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("Source model does not contain a Mesh object.")

    unsafe_meshes = [
        obj.name
        for obj in mesh_objects
        if obj.modifiers or obj.constraints or obj.data.shape_keys is not None
    ]
    if unsafe_meshes:
        raise RuntimeError(
            "Source model contains modified, constrained, or shape-key Mesh objects that "
            "cannot be joined safely: " + ", ".join(unsafe_meshes[:8])
        )
    auxiliary_objects = [obj for obj in imported if obj.type != "MESH"]
    unsafe_auxiliary = [
        obj.name
        for obj in auxiliary_objects
        if obj.type not in SAFE_AUXILIARY_TYPES
        or (obj.type == "EMPTY" and obj.instance_type != "NONE")
    ]
    if unsafe_auxiliary:
        raise RuntimeError(
            "Source model contains armatures, instances, or unsupported non-Mesh objects: "
            + ", ".join(unsafe_auxiliary[:8])
        )

    source_meshes = [
        {
            "name": obj.name,
            "mesh_data_name": obj.data.name,
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "world_matrix": matrix_rows(obj.matrix_world),
            "world_bounds": world_bounds(obj),
        }
        for obj in mesh_objects
    ]
    before_bounds = combined_world_bounds(mesh_objects)
    vertex_count = sum(len(obj.data.vertices) for obj in mesh_objects)
    polygon_count = sum(len(obj.data.polygons) for obj in mesh_objects)

    for obj in mesh_objects:
        world_matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = world_matrix
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False

    bpy.ops.object.select_all(action="DESELECT")
    high_object = mesh_objects[0]
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = high_object
    if len(mesh_objects) > 1:
        join_result = bpy.ops.object.join()
        if "FINISHED" not in join_result:
            raise RuntimeError("Blender could not join the source Mesh parts.")
    for obj in auxiliary_objects:
        bpy.data.objects.remove(obj, do_unlink=True)

    if len(high_object.data.vertices) != vertex_count:
        raise RuntimeError("Joining source Mesh parts changed the vertex count.")
    if len(high_object.data.polygons) != polygon_count:
        raise RuntimeError("Joining source Mesh parts changed the polygon count.")
    after_bounds = world_bounds(high_object)
    if not bounds_match(before_bounds, after_bounds):
        raise RuntimeError("Joining source Mesh parts changed their world-space bounds.")

    high_object.name = HIGH_OBJECT_NAME
    high_object.data.name = HIGH_MESH_NAME
    if high_object.name != HIGH_OBJECT_NAME:
        raise RuntimeError(f"Could not assign the high object name {HIGH_OBJECT_NAME}.")
    input_sha256 = sha256_file(input_path)
    high_object["li3d_role"] = "high"
    source_format_name = source_format.removeprefix(".")
    high_object["li3d_source_format"] = source_format_name
    high_object["li3d_source_sha256"] = input_sha256
    high_object["li3d_source_mesh_names"] = json.dumps(
        [item["name"] for item in source_meshes], ensure_ascii=False
    )
    original_topology = source_topology(high_object)
    normalized_work_object, normalized_work_source = build_normalized_work_copy(
        high_object,
        original_topology,
        after_bounds,
    )
    if source_topology(high_object) != original_topology:
        raise RuntimeError("Creating the normalized work copy changed SOURCE_HIGH.")

    scene = bpy.context.scene
    scene["li3d_retopology_source_format"] = source_format_name
    scene["li3d_retopology_high_object"] = HIGH_OBJECT_NAME
    scene["li3d_retopology_source_sha256"] = input_sha256
    if normalized_work_object is not None:
        scene["li3d_retopology_normalized_work_object"] = (
            NORMALIZED_WORK_OBJECT_NAME
        )
    bpy.ops.object.select_all(action="DESELECT")
    high_object.select_set(True)
    bpy.context.view_layer.objects.active = high_object

    manifest = {
        "schema": "li3d-retopology-static-source-v4",
        "blender_version": bpy.app.version_string,
        "source_format": source_format_name,
        "source_filepath": input_path,
        "source_sha256": input_sha256,
        "prepared_blend_filepath": output_path,
        "prepared_high_object": HIGH_OBJECT_NAME,
        "prepared_mesh_data": HIGH_MESH_NAME,
        "source_mesh_count": len(source_meshes),
        "source_meshes": source_meshes,
        "removed_auxiliary_objects": [
            {"name": obj.name, "type": obj.type} for obj in auxiliary_objects
        ],
        "joined_meshes": len(source_meshes) > 1,
        "vertices": len(high_object.data.vertices),
        "polygons": len(high_object.data.polygons),
        "source_topology": original_topology,
        "normalized_work_source": normalized_work_source,
        "world_matrix": matrix_rows(high_object.matrix_world),
        "world_bounds": after_bounds,
    }
    save_blend_atomic(output_path)
    write_json_atomic(manifest_path, manifest)
    print(
        "LI3D_FBX_PREP_OK:"
        + json.dumps(
            {
                "high_object": HIGH_OBJECT_NAME,
                "meshes": len(source_meshes),
                "vertices": manifest["vertices"],
                "polygons": manifest["polygons"],
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"LI3D_FBX_PREP_ERROR:{error}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
