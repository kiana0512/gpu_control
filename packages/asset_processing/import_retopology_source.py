"""Normalize a supported uploaded model into a Blender scene for Direct V2."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy

BLEND_SIGNATURE = b"BLENDER"


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = arguments()
    # ``--factory-startup`` still creates Blender's default Cube.  Importing
    # into that scene made Direct V2 see the Cube as another source high and
    # could make it build or pair the wrong object.  Start from a genuinely
    # empty scene so every mesh belongs to the uploaded asset.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = args.input.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(args.input))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(args.input))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(args.input))
    elif suffix == ".blend":
        # Re-save a valid compressed Blend as an uncompressed Direct V2 input.
        # Auto-execution remains disabled by the Worker Blender invocation.
        bpy.ops.wm.open_mainfile(
            filepath=str(args.input),
            load_ui=False,
            use_scripts=False,
        )
    else:
        raise RuntimeError(f"unsupported Direct V2 source format: {suffix}")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("source import produced no mesh objects")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Blender 5 enables Zstd-compressed .blend output by default.  Direct V2
    # deliberately validates the raw Blender header before handing a file to
    # Codex, so an otherwise valid normalized GLB/GLTF/OBJ was rejected at 8%
    # with ``input does not have a valid Blend signature``.  Keep this adapter
    # output deterministic and compatible with that frozen input contract.
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.output),
        check_existing=False,
        compress=False,
    )
    if not args.output.is_file() or args.output.stat().st_size <= 0:
        raise RuntimeError("source normalization did not create a Blend")
    with args.output.open("rb") as handle:
        signature = handle.read(len(BLEND_SIGNATURE))
    if signature != BLEND_SIGNATURE:
        raise RuntimeError("source normalization did not create an uncompressed Blend")


if __name__ == "__main__":
    main()
