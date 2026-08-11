"""Re-export a UV result while preserving its source FBX unit contract.

The approved UV Skill intentionally owns UV generation and remains unchanged.
This GPU Control delivery adapter opens the already-produced Blend, exports only
its mesh objects, and verifies that the FBX keeps both the same source-facing
raw-coordinate convention and the same Blender world-space bounds after a fresh
import.  It changes no mesh, topology, UV, material, or source Blend data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

DEFAULT_FBX_UNIT_SCALE_FACTOR_CENTIMETERS = 100.0
FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE = 1.0e-6


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-asset", type=Path, required=True)
    parser.add_argument("--input-blend", type=Path, required=True)
    parser.add_argument("--output-fbx", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    return parser.parse_args(values)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fbx_double_property(path: Path, name: str) -> float:
    encoded = name.encode("ascii")
    pattern = (
        b"S"
        + struct.pack("<I", len(encoded))
        + encoded
        + b"S"
        + struct.pack("<I", len(b"double"))
        + b"double"
        + b"S"
        + struct.pack("<I", len(b"Number"))
        + b"Number"
        + b"S"
        + struct.pack("<I", 0)
        + b"D"
    )
    payload = path.read_bytes()
    positions: list[int] = []
    offset = 0
    while True:
        found = payload.find(pattern, offset)
        if found < 0:
            break
        positions.append(found)
        offset = found + 1
    if len(positions) != 1:
        raise RuntimeError(f"FBX property {name} is missing or ambiguous")
    return float(struct.unpack_from("<d", payload, positions[0] + len(pattern))[0])


def mesh_objects() -> list[bpy.types.Object]:
    return sorted(
        (
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and len(obj.data.polygons) > 0
        ),
        key=lambda item: item.name,
    )


def union_bounds(objects: list[bpy.types.Object]) -> dict[str, list[float]]:
    points = [obj.matrix_world @ vertex.co for obj in objects for vertex in obj.data.vertices]
    if not points:
        raise RuntimeError("UV delivery contains no mesh vertices")
    minimum = Vector(min(point[index] for point in points) for index in range(3))
    maximum = Vector(max(point[index] for point in points) for index in range(3))
    return {
        "minimum": [float(value) for value in minimum],
        "maximum": [float(value) for value in maximum],
        "center": [float(value) for value in (minimum + maximum) * 0.5],
        "dimensions": [float(value) for value in maximum - minimum],
    }


def structure_summary(objects: list[bpy.types.Object]) -> dict[str, Any]:
    return {
        "mesh_object_count": len(objects),
        "vertices": sum(len(obj.data.vertices) for obj in objects),
        "edges": sum(len(obj.data.edges) for obj in objects),
        "polygons": sum(len(obj.data.polygons) for obj in objects),
        "loops": sum(len(obj.data.loops) for obj in objects),
        "uv_layers": sum(len(obj.data.uv_layers) for obj in objects),
        "material_slots": sum(len(obj.material_slots) for obj in objects),
    }


def max_vector_delta(first: list[float], second: list[float]) -> float:
    return max(abs(left - right) for left, right in zip(first, second, strict=True))


def scaled_bounds(bounds: dict[str, list[float]], multiplier: float) -> dict[str, list[float]]:
    return {key: [float(value * multiplier) for value in values] for key, values in bounds.items()}


def source_unit_contract(source_asset: Path) -> dict[str, Any]:
    if source_asset.suffix.lower() != ".fbx":
        return {
            "source_format": source_asset.suffix.lower().lstrip(".") or "unknown",
            "unit_scale_factor_centimeters": None,
            "original_unit_scale_factor_centimeters": None,
            "target_unit_scale_factor_centimeters": (DEFAULT_FBX_UNIT_SCALE_FACTOR_CENTIMETERS),
            "selection": "browser-safe meter default for a source without FBX units",
        }
    unit = fbx_double_property(source_asset, "UnitScaleFactor")
    original_unit = fbx_double_property(source_asset, "OriginalUnitScaleFactor")
    if (
        not math.isfinite(unit)
        or unit <= 0.0
        or not math.isfinite(original_unit)
        or original_unit <= 0.0
    ):
        raise RuntimeError(
            "UV source FBX unit contract is invalid: "
            f"UnitScaleFactor={unit}, OriginalUnitScaleFactor={original_unit}"
        )
    supported = (1.0, 100.0)
    if not any(
        math.isclose(unit, candidate, abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE)
        for candidate in supported
    ):
        raise RuntimeError(
            "UV source FBX uses an unsupported delivery unit: "
            f"UnitScaleFactor={unit}; supported={supported}"
        )
    return {
        "source_format": "fbx",
        "unit_scale_factor_centimeters": unit,
        "original_unit_scale_factor_centimeters": original_unit,
        "target_unit_scale_factor_centimeters": unit,
        "selection": "preserve source FBX UnitScaleFactor",
    }


def export_source_unit_fbx(
    path: Path,
    objects: list[bpy.types.Object],
    target_unit_scale_factor_centimeters: float,
) -> dict[str, Any]:
    # Match the Skill's material-safe linked-mesh handling without saving any
    # of these temporary copies back to the authoritative UV Blend.
    original_meshes: dict[bpy.types.Object, bpy.types.Mesh] = {}
    temporary_meshes: list[bpy.types.Mesh] = []
    seen: set[int] = set()
    try:
        for obj in objects:
            pointer = obj.data.as_pointer()
            if pointer in seen:
                original_meshes[obj] = obj.data
                export_mesh = obj.data.copy()
                temporary_meshes.append(export_mesh)
                obj.data = export_mesh
            else:
                seen.add(pointer)
            for slot in obj.material_slots:
                slot.link = "DATA"

        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.hide_render = False
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]

        scene = bpy.context.scene
        if math.isclose(
            target_unit_scale_factor_centimeters,
            1.0,
            abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE,
        ):
            # This is Blender's stable centimetre-FBX path: raw browser
            # coordinates remain centimetres while Blender readback restores
            # the same world-space metres.
            scene.unit_settings.system = "NONE"
            scene.unit_settings.scale_length = 1.0
            apply_scale_options = "FBX_SCALE_NONE"
        else:
            scene.unit_settings.system = "METRIC"
            scene.unit_settings.scale_length = 1.0
            scene.unit_settings.length_unit = "METERS"
            apply_scale_options = "FBX_SCALE_UNITS"
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            use_visible=False,
            object_types={"MESH"},
            use_mesh_modifiers=False,
            mesh_smooth_type="OFF",
            use_tspace=False,
            use_triangles=False,
            add_leaf_bones=False,
            bake_anim=False,
            global_scale=1.0,
            apply_unit_scale=True,
            apply_scale_options=apply_scale_options,
            use_space_transform=True,
            bake_space_transform=False,
            axis_forward="-Z",
            axis_up="Y",
            path_mode="AUTO",
        )
        return {
            "global_scale": 1.0,
            "apply_unit_scale": True,
            "apply_scale_options": apply_scale_options,
            "scene_unit_system": scene.unit_settings.system,
            "scene_scale_length": scene.unit_settings.scale_length,
        }
    finally:
        for obj, mesh in original_meshes.items():
            obj.data = mesh
        for mesh in temporary_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def main() -> None:
    args = arguments()
    source_asset = args.source_asset.resolve()
    input_blend = args.input_blend.resolve()
    output_fbx = args.output_fbx.resolve()
    output_report = args.output_report.resolve()
    if not input_blend.is_file():
        raise RuntimeError("UV delivery Blend is missing")
    if not source_asset.is_file():
        raise RuntimeError("UV source asset is missing")

    source_contract = source_unit_contract(source_asset)
    target_unit = float(source_contract["target_unit_scale_factor_centimeters"])

    bpy.ops.wm.open_mainfile(filepath=str(input_blend))
    objects = mesh_objects()
    if not objects:
        raise RuntimeError("UV delivery Blend has no non-empty mesh")
    expected_bounds = union_bounds(objects)
    expected_structure = structure_summary(objects)

    export_settings = export_source_unit_fbx(output_fbx, objects, target_unit)
    if not output_fbx.is_file() or output_fbx.stat().st_size <= 0:
        raise RuntimeError("UV source-unit-contract FBX export is empty")
    unit = fbx_double_property(output_fbx, "UnitScaleFactor")
    original_unit = fbx_double_property(output_fbx, "OriginalUnitScaleFactor")
    if not math.isclose(
        unit,
        target_unit,
        abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE,
    ) or not math.isclose(
        original_unit,
        target_unit,
        abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE,
    ):
        raise RuntimeError(
            "UV FBX source unit contract failed: "
            f"expected={target_unit}, actual={unit}, original={original_unit}"
        )

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if bpy.app.version >= (4, 3, 0):
        bpy.ops.wm.fbx_import(filepath=str(output_fbx))
    else:
        bpy.ops.import_scene.fbx(filepath=str(output_fbx))
    imported = mesh_objects()
    if not imported:
        raise RuntimeError("UV FBX fresh readback has no non-empty mesh")
    readback_bounds = union_bounds(imported)
    readback_structure = structure_summary(imported)
    dimensions = expected_bounds["dimensions"]
    scale = max(max(abs(value) for value in dimensions), 1.0)
    tolerance = max(1.0e-5, scale * 1.0e-4)
    center_delta = max_vector_delta(expected_bounds["center"], readback_bounds["center"])
    dimensions_delta = max_vector_delta(dimensions, readback_bounds["dimensions"])
    structure_fields = ("mesh_object_count", "vertices", "polygons", "loops", "uv_layers")
    structure_passed = all(
        expected_structure[field] == readback_structure[field] for field in structure_fields
    )
    passed = center_delta <= tolerance and dimensions_delta <= tolerance and structure_passed

    browser_multiplier = 100.0 / target_unit
    report = {
        "schema_version": "uv_fbx_unit_contract.v2",
        "passed": passed,
        "source_asset_sha256": file_sha256(source_asset),
        "input_blend_sha256": file_sha256(input_blend),
        "output_fbx_sha256": file_sha256(output_fbx),
        "output_fbx_size_bytes": output_fbx.stat().st_size,
        "expected_bounds": expected_bounds,
        "readback_bounds": readback_bounds,
        "center_max_abs_delta": center_delta,
        "dimensions_max_abs_delta": dimensions_delta,
        "tolerance": tolerance,
        "expected_structure": expected_structure,
        "readback_structure": readback_structure,
        "source_unit_contract": source_contract,
        "browser_coordinate_contract": {
            "passed": True,
            "canonical_unit": "centimeter",
            "raw_coordinate_multiplier_from_blender_world_meters": browser_multiplier,
            "expected_raw_bounds": scaled_bounds(expected_bounds, browser_multiplier),
            "readback_raw_bounds": scaled_bounds(readback_bounds, browser_multiplier),
            "canonical_bounds": scaled_bounds(expected_bounds, 100.0),
            "source_unit_scale_factor_preserved": (
                source_contract["unit_scale_factor_centimeters"] is None
                or math.isclose(
                    unit,
                    float(source_contract["unit_scale_factor_centimeters"]),
                    abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE,
                )
            ),
        },
        "unit_contract": {
            "coordinate_unit": "meter" if unit >= 100.0 else "centimeter",
            "unit_scale_factor_centimeters": unit,
            "original_unit_scale_factor_centimeters": original_unit,
            "raw_coordinates_are_meters": math.isclose(
                unit,
                100.0,
                abs_tol=FBX_UNIT_SCALE_ABSOLUTE_TOLERANCE,
            ),
            **export_settings,
            "axis_forward": "-Z",
            "axis_up": "Y",
        },
    }
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise RuntimeError(
            "UV FBX fresh readback changed delivery data: "
            f"center_delta={center_delta:.9g}, dimensions_delta={dimensions_delta:.9g}, "
            f"structure_passed={structure_passed}, tolerance={tolerance:.9g}"
        )


if __name__ == "__main__":
    main()
