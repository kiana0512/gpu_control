"""Merge a V6 retopology delivery into one object without welding components.

This is a delivery-layout operation, not a topology generator.  It preserves
the component meshes as disconnected islands, their material slots, and their
world-space placement while producing one BLEND object and one selected-only
FBX object for downstream applications.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import bpy


MERGE_MODE = "single_object_disconnected_islands"


def parse_args() -> argparse.Namespace:
    argv = []
    if "--" in __import__("sys").argv:
        argv = __import__("sys").argv[__import__("sys").argv.index("--") + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--objects-json", required=True)
    parser.add_argument("--merged-name", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args(argv)


def load_object_names(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not value:
        raise RuntimeError("V6 merge requires at least one formal low object name")
    names = [str(item).strip() for item in value]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise RuntimeError("V6 formal low object names must be unique and non-empty")
    return names


def evaluated_mesh_object(obj: bpy.types.Object) -> None:
    """Freeze visible modifiers while preserving the object's world transform."""

    if not obj.modifiers:
        return
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    old_mesh = obj.data
    obj.data = bpy.data.meshes.new_from_object(
        evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
    )
    obj.modifiers.clear()
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def main() -> None:
    args = parse_args()
    blend_path = Path(args.blend).resolve()
    fbx_path = Path(args.fbx).resolve()
    report_path = Path(args.report).resolve()
    object_names = load_object_names(args.objects_json)

    if Path(bpy.data.filepath).resolve() != blend_path:
        raise RuntimeError("Blender did not open the requested V6 delivery file")

    missing = [name for name in object_names if bpy.data.objects.get(name) is None]
    if missing:
        raise RuntimeError(f"V6 merge objects are missing: {missing}")
    objects = [bpy.data.objects[name] for name in object_names]
    non_mesh = [obj.name for obj in objects if obj.type != "MESH"]
    if non_mesh:
        raise RuntimeError(f"V6 merge accepts mesh objects only: {non_mesh}")

    authored = {
        "objects": len(objects),
        "vertices": sum(len(obj.data.vertices) for obj in objects),
        "edges": sum(len(obj.data.edges) for obj in objects),
        "polygons": sum(len(obj.data.polygons) for obj in objects),
    }

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        evaluated_mesh_object(obj)
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
    before = {
        "objects": len(objects),
        "vertices": sum(len(obj.data.vertices) for obj in objects),
        "edges": sum(len(obj.data.edges) for obj in objects),
        "polygons": sum(len(obj.data.polygons) for obj in objects),
    }
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    merged = bpy.context.view_layer.objects.active
    if merged is None or merged.type != "MESH":
        raise RuntimeError("V6 component join did not produce a mesh object")
    merged.name = args.merged_name
    merged.data.name = f"{args.merged_name}_MESH"
    merged["li3d_merge_mode"] = MERGE_MODE
    merged["li3d_source_component_count"] = len(object_names)
    merged["li3d_source_component_names_json"] = json.dumps(
        object_names, ensure_ascii=False, separators=(",", ":")
    )

    after = {
        "objects": 1,
        "vertices": len(merged.data.vertices),
        "edges": len(merged.data.edges),
        "polygons": len(merged.data.polygons),
    }
    if any(after[key] != before[key] for key in ("vertices", "edges", "polygons")):
        raise RuntimeError("V6 delivery merge changed topology element counts")

    # A formal delivery contains only the merged low mesh.  This avoids hidden
    # high/review objects leaking into BLEND or FBX while keeping disconnected
    # mechanical parts intact inside the one authoritative object.
    for obj in list(bpy.data.objects):
        if obj != merged:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.objects.active = merged
    merged.select_set(True)

    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    fbx_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(fbx_path),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        use_space_transform=True,
        path_mode="AUTO",
    )
    if not fbx_path.is_file() or fbx_path.stat().st_size <= 0:
        raise RuntimeError("V6 merged FBX export is empty")

    report_path.write_text(
        json.dumps(
            {
                "schema_version": "retopology_v6_merge.v1",
                "merge_mode": MERGE_MODE,
                "merged_object_name": merged.name,
                "source_component_names": object_names,
                "topology_authored": authored,
                "topology_before": before,
                "topology_after": after,
                "blend": blend_path.name,
                "fbx": fbx_path.name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    print("RETOPOLOGY_V6_MERGE " + report_path.read_text("utf-8"))


if __name__ == "__main__":
    main()
