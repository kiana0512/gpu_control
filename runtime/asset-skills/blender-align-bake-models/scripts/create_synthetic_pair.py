"""Create an asymmetric FBX high/low pair with a known transform for regression tests."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Matrix


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--symmetric", action="store_true")
    parser.add_argument("--mirror", action="store_true")
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def add_box(
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def build_asymmetric_object() -> bpy.types.Object:
    pieces = [
        add_box((0.0, 0.0, 0.0), (2.1, 0.85, 0.42)),
        add_box((1.45, 0.58, 0.92), (0.38, 0.28, 0.76)),
        add_box((-1.25, -0.45, 0.63), (0.62, 0.24, 0.21)),
    ]
    bpy.ops.mesh.primitive_cone_add(
        vertices=7,
        radius1=0.48,
        radius2=0.16,
        depth=1.15,
        location=(-1.55, 0.34, 0.83),
    )
    cone = bpy.context.active_object
    cone.rotation_euler = Euler(
        (math.radians(17.0), math.radians(9.0), math.radians(-12.0)), "XYZ"
    )
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    pieces.append(cone)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in pieces:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = pieces[0]
    bpy.ops.object.join()
    result = bpy.context.active_object
    result.name = "SYNTHETIC_ASYMMETRIC"
    bpy.context.view_layer.objects.active = result
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return result


def build_symmetric_object() -> bpy.types.Object:
    result = add_box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    result.name = "SYNTHETIC_SYMMETRIC"
    bpy.context.view_layer.objects.active = result
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return result


def export_selected(obj: bpy.types.Object, path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        bake_anim=False,
        add_leaf_bones=False,
        apply_unit_scale=True,
        use_space_transform=True,
        axis_forward="-Z",
        axis_up="Y",
    )


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    clear_scene()
    high = build_symmetric_object() if args.symmetric else build_asymmetric_object()
    export_selected(high, output_dir / "synthetic_high.fbx")

    low = high.copy()
    low.data = high.data.copy()
    bpy.context.collection.objects.link(low)
    low.name = "SYNTHETIC_LOW_MISALIGNED"
    reflection = (
        Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
        if args.mirror
        else Matrix.Identity(4)
    )
    low.matrix_world = (
        Matrix.Translation((5.3, -3.7, 2.4))
        @ Euler(
            (math.radians(37.0), math.radians(-21.0), math.radians(68.0)), "XYZ"
        ).to_matrix().to_4x4()
        @ Matrix.Scale(1.73, 4)
        @ reflection
    )
    export_selected(low, output_dir / "synthetic_low.fbx")
    expected = low.matrix_world.inverted()
    with (output_dir / "expected_low_world_to_high_world.json").open(
        "w", encoding="utf-8"
    ) as stream:
        json.dump(
            {"matrix": [[float(value) for value in row] for row in expected]},
            stream,
            indent=2,
        )
        stream.write("\n")
    print(f"Synthetic pair written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
