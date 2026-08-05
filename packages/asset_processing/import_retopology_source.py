"""Normalize a supported uploaded model into a Blender scene for Direct V2."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = arguments()
    suffix = args.input.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(args.input))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(args.input))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(args.input))
    else:
        raise RuntimeError(f"unsupported Direct V2 source format: {suffix}")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("source import produced no mesh objects")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output), check_existing=False)
    if not args.output.is_file() or args.output.stat().st_size <= 0:
        raise RuntimeError("source normalization did not create a Blend")


if __name__ == "__main__":
    main()
