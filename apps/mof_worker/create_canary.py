"""Create a deterministic complex non-hard-surface FBX for MOF acceptance."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy


def output_path() -> Path:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(values) != 1:
        raise SystemExit("usage: blender --background --python create_canary.py -- OUTPUT.fbx")
    return Path(values[0]).resolve()


def main() -> None:
    destination = output_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    parts = []
    locations = ((-2.5, 0.0, 0.0), (0.0, 0.0, 0.5), (2.5, 0.0, 0.0))
    for index, location in enumerate(locations):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0, location=location)
        part = bpy.context.object
        part.name = f"MOF_Canary_OrganicPart_{index + 1}"
        for vertex in part.data.vertices:
            coordinate = vertex.co
            radial_wave = 1.0 + 0.12 * math.sin(
                coordinate.x * (3.0 + index)
                + coordinate.y * 2.0
                - coordinate.z * 1.5
            )
            coordinate *= radial_wave
            coordinate.x *= 0.75 + index * 0.08
            coordinate.y *= 0.9 + index * 0.05
            coordinate.z *= 1.15 + index * 0.12
        part.data.update()
        for polygon in part.data.polygons:
            polygon.use_smooth = True
        material = bpy.data.materials.new(name=f"MOF_Canary_SoftMaterial_{index + 1}")
        part.data.materials.append(material)
        parts.append(part)
    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    target = bpy.context.object
    target.name = "MOF_Canary_ComplexNonHardSurface"
    target.data.name = target.name
    for uv_layer in list(target.data.uv_layers):
        target.data.uv_layers.remove(uv_layer)

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.fbx(
        filepath=str(destination),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=False,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Z",
        axis_up="Y",
    )
    print(f"MOF_CANARY_CREATED {destination}")


if __name__ == "__main__":
    main()
