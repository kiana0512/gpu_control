"""Render seven high/low overlay views from bake_alignment.blend."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resolution", type=int, default=768)
    return parser.parse_args(argv)


def role_objects(prefix: str) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.name.startswith(prefix)
    ]


def bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
    return minimum, maximum


def prepare_overlay(
    high_objects: list[bpy.types.Object],
    low_objects: list[bpy.types.Object],
    wire_thickness: float,
) -> list[bpy.types.Object]:
    for obj in high_objects:
        obj.hide_render = False
        obj.color = (0.16, 0.42, 0.80, 1.0)
    wire_objects: list[bpy.types.Object] = []
    for low in low_objects:
        low.hide_render = False
        low.color = (1.0, 0.20, 0.015, 1.0)
        wire = low.copy()
        wire.data = low.data.copy()
        bpy.context.collection.objects.link(wire)
        wire.name = f"VALIDATION_WIRE_{low.name}"
        wire.hide_render = False
        wire.color = (0.18, 0.015, 0.005, 1.0)
        modifier = wire.modifiers.new(name="Validation Wire", type="WIREFRAME")
        modifier.thickness = wire_thickness
        modifier.use_replace = True
        modifier.use_boundary = True
        wire_objects.append(wire)
    return wire_objects


def create_camera(center: Vector, ortho_scale: float) -> bpy.types.Object:
    camera_data = bpy.data.cameras.new("AlignmentValidationCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = ortho_scale
    camera = bpy.data.objects.new("AlignmentValidationCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    return camera


def point_camera(camera: bpy.types.Object, center: Vector, direction: Vector, distance: float) -> None:
    unit = direction.normalized()
    camera.location = center + unit * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()


def configure_workbench(resolution: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.background_type = "THEME"


def main() -> int:
    args = parse_args()
    blend_path = Path(args.blend).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not blend_path.is_file():
        raise FileNotFoundError(blend_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))

    high_objects = role_objects("ALIGN_HIGH_")
    low_objects = role_objects("ALIGN_LOW_")
    if not high_objects or not low_objects:
        raise RuntimeError("Expected ALIGN_HIGH_* and ALIGN_LOW_* mesh objects.")
    minimum, maximum = bounds(high_objects + low_objects)
    center = (minimum + maximum) * 0.5
    size = maximum - minimum
    diagonal = max(size.length, 1e-6)
    prepare_overlay(high_objects, low_objects, wire_thickness=diagonal * 0.0015)
    configure_workbench(args.resolution)
    camera = create_camera(center, max(size) * 1.30)
    distance = diagonal * 2.5

    views = {
        "front": Vector((0.0, -1.0, 0.0)),
        "back": Vector((0.0, 1.0, 0.0)),
        "left": Vector((-1.0, 0.0, 0.0)),
        "right": Vector((1.0, 0.0, 0.0)),
        "top": Vector((0.0, 0.0, 1.0)),
        "bottom": Vector((0.0, 0.0, -1.0)),
        "perspective": Vector((1.15, -1.35, 0.95)),
    }
    rendered: dict[str, str] = {}
    for name, direction in views.items():
        point_camera(camera, center, direction, distance)
        output = output_dir / f"{name}.png"
        bpy.context.scene.render.filepath = str(output)
        bpy.ops.render.render(write_still=True)
        rendered[name] = str(output)

    manifest = output_dir / "views.json"
    with manifest.open("w", encoding="utf-8") as stream:
        json.dump({"blend": str(blend_path), "views": rendered}, stream, indent=2)
        stream.write("\n")
    print(f"Rendered {len(rendered)} alignment views to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
