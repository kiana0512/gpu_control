"""Render matched high/reference/generated review views for one retopology candidate."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--high", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--generated", required=True)
    parser.add_argument("--resolution", type=int, choices=(256, 512, 1024), default=512)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def require_mesh(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(f"required mesh object is missing: {name}")
    return obj


def world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector, Vector]:
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[index] for point in corners) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in corners) for index in range(3)))
    return minimum, maximum, (minimum + maximum) * 0.5


def look_at(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def configure_scene(scene: bpy.types.Scene, resolution: int) -> tuple[bpy.types.Object, bool]:
    engines = {
        item.identifier
        for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    workbench = False
    if "BLENDER_WORKBENCH" in engines:
        scene.render.engine = "BLENDER_WORKBENCH"
        workbench = True
    elif "BLENDER_WORKBENCH_NEXT" in engines:
        scene.render.engine = "BLENDER_WORKBENCH_NEXT"
        workbench = True
    elif "BLENDER_EEVEE_NEXT" in engines:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    elif "BLENDER_EEVEE" in engines:
        # The pinned Blender 5.1.2 headless runtime exposes this identifier.
        scene.render.engine = "BLENDER_EEVEE"
    else:
        raise RuntimeError(f"no supported review render engine is available: {sorted(engines)}")
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    if workbench:
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "OBJECT"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
        scene.display.shading.cavity_type = "BOTH"
        scene.display.shading.show_specular_highlight = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("GPUControlReviewWorld")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.015, 0.018, 0.028, 1.0)
        background.inputs["Strength"].default_value = 0.8
    camera_data = bpy.data.cameras.new("GPUControlReviewCamera")
    camera = bpy.data.objects.new("GPUControlReviewCamera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.clip_start = 0.001
    camera_data.clip_end = 1_000_000
    if not workbench:
        for name, energy, location in (
            ("GPUControlKey", 1200.0, (4.0, -6.0, 6.0)),
            ("GPUControlFill", 800.0, (-5.0, -2.0, 3.0)),
            ("GPUControlRim", 1000.0, (2.0, 5.0, 5.0)),
        ):
            light_data = bpy.data.lights.new(name=name, type="AREA")
            light_data.energy = energy
            light_data.shape = "DISK"
            light_data.size = 5.0
            light = bpy.data.objects.new(name, light_data)
            light.location = location
            scene.collection.objects.link(light)
            look_at(light, Vector((0.0, 0.0, 0.0)))
    return camera, workbench


def apply_review_material(
    obj: bpy.types.Object,
    role: str,
    color: tuple[float, float, float, float],
) -> None:
    """Give Eevee renders a deterministic solid/wire material.

    This mutates only the in-memory review scene; the worker never saves this
    render-only scene over the immutable candidate BLEND.
    """
    material = bpy.data.materials.new(f"GPUControlReview_{role}")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    if role == "high":
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.inputs["Roughness"].default_value = 0.72
        principled.inputs["Base Color"].default_value = color
        emission_color = principled.inputs.get("Emission Color") or principled.inputs.get(
            "Emission"
        )
        if emission_color is not None:
            emission_color.default_value = color
        emission_strength = principled.inputs.get("Emission Strength")
        if emission_strength is not None:
            emission_strength.default_value = 0.22
        links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    else:
        wire = nodes.new("ShaderNodeWireframe")
        wire.use_pixel_size = True
        wire.inputs["Size"].default_value = 0.35
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MIX"
        mix.inputs[1].default_value = color
        mix.inputs[2].default_value = (0.92, 0.95, 1.0, 1.0)
        links.new(wire.outputs["Fac"], mix.inputs[0])
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 0.8
        links.new(mix.outputs[0], emission.inputs["Color"])
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    obj.data.materials.clear()
    obj.data.materials.append(material)


def camera_for_view(
    camera: bpy.types.Object,
    view: str,
    center: Vector,
    scale: float,
) -> None:
    # Model units vary wildly (the Stanford Bunny is about 0.15 m while game
    # assets may be thousands of units).  A fixed one-unit minimum makes small
    # meshes occupy only a few pixels in perspective views, so all camera
    # distances must be derived from the matched object bounds.
    distance = max(scale * 3.0, 1.0e-4)
    if view == "front":
        camera.data.type = "ORTHO"
        camera.location = center + Vector((0.0, -distance, 0.0))
    elif view == "side":
        camera.data.type = "ORTHO"
        camera.location = center + Vector((distance, 0.0, 0.0))
    elif view == "top":
        camera.data.type = "ORTHO"
        camera.location = center + Vector((0.0, 0.0, distance))
    else:
        camera.data.type = "PERSP"
        camera.data.lens = 70
        perspective_distance = max(scale * 1.8, 1.0e-4)
        camera.location = center + Vector(
            (
                perspective_distance,
                -perspective_distance,
                perspective_distance * 0.75,
            )
        )
    if camera.data.type == "ORTHO":
        camera.data.ortho_scale = max(scale * 1.25, 0.01)
    look_at(camera, center)


def main() -> None:
    args = arguments()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.input))
    roles = {
        "high": require_mesh(args.high),
        "reference": require_mesh(args.reference),
        "generated": require_mesh(args.generated),
    }
    if len({obj.name for obj in roles.values()}) != 3:
        raise RuntimeError("review roles must resolve to three distinct mesh objects")
    scene = bpy.context.scene
    camera, workbench = configure_scene(scene, args.resolution)
    all_meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    bounds = {role: world_bounds(obj) for role, obj in roles.items()}
    matched_scale = max(
        max((maximum - minimum)[index] for index in range(3))
        for minimum, maximum, _ in bounds.values()
    )
    if not math.isfinite(matched_scale) or matched_scale <= 0:
        raise RuntimeError("review objects have invalid or empty bounds")

    colors = {
        "high": (0.24, 0.55, 0.95, 1.0),
        "reference": (0.95, 0.66, 0.22, 1.0),
        "generated": (0.28, 0.85, 0.55, 1.0),
    }
    generated_files: list[dict[str, object]] = []
    for role, obj in roles.items():
        if not workbench:
            apply_review_material(obj, role, colors[role])
        for candidate in all_meshes:
            candidate.hide_render = candidate != obj
        obj.hide_render = False
        obj.hide_set(False)
        obj.color = colors[role]
        obj.show_wire = role != "high"
        obj.show_all_edges = role != "high"
        _, _, center = bounds[role]
        for view in ("front", "side", "top", "perspective"):
            camera_for_view(camera, view, center, matched_scale)
            output = output_dir / f"{role}_{view}.png"
            scene.render.filepath = str(output)
            bpy.ops.render.render(write_still=True)
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError(f"review render is missing: {output.name}")
            generated_files.append(
                {"role": role, "view": view, "filename": output.name}
            )
    manifest = {
        "schema_version": "retopology_views.v1",
        "matched_scale": matched_scale,
        "roles": {role: obj.name for role, obj in roles.items()},
        "views": generated_files,
        "layout": {"rows": ["high", "reference", "generated"], "columns": ["front", "side", "top", "perspective"]},
        "labels_are_filenames_not_scene_geometry": True,
    }
    (output_dir / "retopology_views.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
