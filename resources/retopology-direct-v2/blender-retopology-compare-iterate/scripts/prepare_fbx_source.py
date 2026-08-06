#!/usr/bin/env python3
"""Import a static-mesh FBX into one task-local authoritative high object."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback

import bpy
import mathutils


HIGH_OBJECT_NAME = "SOURCE_HIGH"
HIGH_MESH_NAME = "SOURCE_HIGH_MESH"
SAFE_AUXILIARY_TYPES = {"EMPTY", "CAMERA", "LIGHT"}


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
    if os.path.splitext(input_path)[1].lower() != ".fbx":
        raise RuntimeError("Input must be an FBX file.")
    if not os.path.isfile(input_path) or os.path.getsize(input_path) == 0:
        raise RuntimeError("Input FBX does not exist or is empty.")
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
    import_result = bpy.ops.import_scene.fbx(filepath=input_path)
    if "FINISHED" not in import_result:
        raise RuntimeError("Blender FBX importer did not finish.")
    imported = [
        obj for obj in bpy.context.scene.objects if obj.as_pointer() not in before_ids
    ]
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("FBX does not contain a Mesh object.")

    unsafe_meshes = [
        obj.name
        for obj in mesh_objects
        if obj.modifiers or obj.constraints or obj.data.shape_keys is not None
    ]
    if unsafe_meshes:
        raise RuntimeError(
            "FBX contains modified, constrained, or shape-key Mesh objects that "
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
            "FBX contains armatures, instances, or unsupported non-Mesh objects: "
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
            raise RuntimeError("Blender could not join the FBX Mesh parts.")
    for obj in auxiliary_objects:
        bpy.data.objects.remove(obj, do_unlink=True)

    if len(high_object.data.vertices) != vertex_count:
        raise RuntimeError("Joining FBX Mesh parts changed the vertex count.")
    if len(high_object.data.polygons) != polygon_count:
        raise RuntimeError("Joining FBX Mesh parts changed the polygon count.")
    after_bounds = world_bounds(high_object)
    if not bounds_match(before_bounds, after_bounds):
        raise RuntimeError("Joining FBX Mesh parts changed their world-space bounds.")

    high_object.name = HIGH_OBJECT_NAME
    high_object.data.name = HIGH_MESH_NAME
    if high_object.name != HIGH_OBJECT_NAME:
        raise RuntimeError(f"Could not assign the high object name {HIGH_OBJECT_NAME}.")
    input_sha256 = sha256_file(input_path)
    high_object["li3d_role"] = "high"
    high_object["li3d_source_format"] = "fbx"
    high_object["li3d_source_sha256"] = input_sha256
    high_object["li3d_source_mesh_names"] = json.dumps(
        [item["name"] for item in source_meshes], ensure_ascii=False
    )

    scene = bpy.context.scene
    scene["li3d_retopology_source_format"] = "fbx"
    scene["li3d_retopology_high_object"] = HIGH_OBJECT_NAME
    scene["li3d_retopology_source_sha256"] = input_sha256
    bpy.ops.object.select_all(action="DESELECT")
    high_object.select_set(True)
    bpy.context.view_layer.objects.active = high_object

    manifest = {
        "schema": "li3d-retopology-fbx-source-v1",
        "blender_version": bpy.app.version_string,
        "source_format": "fbx",
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
