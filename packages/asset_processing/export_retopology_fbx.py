"""Export only Direct V2 generated low objects from its saved Blend to FBX."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--objects-json", required=True)
    return parser.parse_args(values)


def main() -> None:
    args = arguments()
    requested = json.loads(args.objects_json)
    if not isinstance(requested, list) or not requested or not all(
        isinstance(name, str) and name for name in requested
    ):
        raise RuntimeError("generated low object list is invalid")

    bpy.ops.object.select_all(action="DESELECT")
    selected = []
    for name in requested:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH" or len(obj.data.polygons) <= 0:
            raise RuntimeError(f"generated low object is missing or empty: {name}")
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
        selected.append(obj)
    bpy.context.view_layer.objects.active = selected[0]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(args.output),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="AUTO",
    )
    if not args.output.is_file() or args.output.stat().st_size <= 0:
        raise RuntimeError("FBX export did not create a non-empty file")


if __name__ == "__main__":
    main()
