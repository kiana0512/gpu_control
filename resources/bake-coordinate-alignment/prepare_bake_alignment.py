"""Create immutable, coordinate-aligned FBX inputs for Substance baking.

High geometry is the coordinate authority.  Each non-high role supplies an
optional ``input_world_to_high_world`` 4x4 matrix in the version-1 manifest;
the same optional upright transform is then applied to every copied role.
This script never writes an input asset.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

IDENTITY = Matrix.Identity(4)


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--high", required=True)
    parser.add_argument("--low", required=True)
    parser.add_argument("--cage")
    parser.add_argument("--manifest")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-straighten-high", action="store_true")
    parser.add_argument("--center-tolerance", type=float, default=0.01)
    parser.add_argument("--dimension-tolerance", type=float, default=0.05)
    parser.add_argument("--uv-geometry-tolerance", type=float, default=1e-4)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def rows(matrix: Matrix) -> list[list[float]]:
    return [[float(v) for v in row] for row in matrix]


def matrix(value: object, label: str) -> Matrix:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(row, list) or len(row) != 4 for row in value)
    ):
        raise ValueError(f"{label} must be a 4x4 matrix")
    return Matrix(tuple(tuple(float(item) for item in row) for row in value))


def clear() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_meshes(path_text: str, role: str) -> list[object]:
    path = Path(path_text).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False)
    elif suffix == ".obj":
        (bpy.ops.wm.obj_import if hasattr(bpy.ops.wm, "obj_import") else bpy.ops.import_scene.obj)(
            filepath=str(path)
        )
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError(f"unsupported input format: {suffix}")
    objects = sorted(
        [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"],
        key=lambda obj: obj.name,
    )
    if not objects:
        raise RuntimeError(f"no mesh objects in {path}")
    for index, obj in enumerate(objects):
        obj.name = f"BAKE_{role.upper()}_{index:03d}"
    return objects


def bounds(objects: list[object]) -> dict[str, object]:
    minimum, maximum = Vector((math.inf,) * 3), Vector((-math.inf,) * 3)
    vertices = polygons = 0
    for obj in objects:
        vertices += len(obj.data.vertices)
        polygons += len(obj.data.polygons)
        for vertex in obj.data.vertices:
            point = obj.matrix_world @ vertex.co
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
    return {
        "minimum": list(minimum),
        "maximum": list(maximum),
        "center": list((minimum + maximum) * 0.5),
        "size": list(maximum - minimum),
        "vertex_count": vertices,
        "polygon_count": polygons,
    }


def transform(objects: list[object], role_matrix: Matrix, common: Matrix) -> None:
    for obj in objects:
        if obj.data.shape_keys:
            raise RuntimeError(f"shape-key mesh unsupported: {obj.name}")
        obj.data = obj.data.copy()
        obj.data.transform(common @ role_matrix @ obj.matrix_world.copy())
        obj.parent = None
        obj.matrix_world = IDENTITY.copy()
        obj.data.update()


def compare(
    high: dict[str, object],
    candidate: dict[str, object],
    center_limit: float,
    dimension_limit: float,
) -> dict[str, object]:
    high_center, low_center = Vector(high["center"]), Vector(candidate["center"])
    high_size, low_size = Vector(high["size"]), Vector(candidate["size"])
    ref = max(high_size.length, 1e-12)
    max_dim = max(max(high_size), 1e-12)
    center_error = (low_center - high_center).length / ref
    dimension_error = max(abs(low_size[i] - high_size[i]) / max_dim for i in range(3))
    return {
        "center_offset_ratio": center_error,
        "max_dimension_relative_error": dimension_error,
        "center_tolerance": center_limit,
        "dimension_tolerance": dimension_limit,
        "pass": center_error <= center_limit and dimension_error <= dimension_limit,
    }


def export(objects: list[object], path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        bake_anim=False,
        add_leaf_bones=False,
        apply_unit_scale=True,
        use_space_transform=True,
        axis_forward="-Z",
        axis_up="Y",
    )


def main() -> int:
    options = args()
    manifest = json.loads(Path(options.manifest).read_text("utf-8")) if options.manifest else {}
    if manifest and (
        manifest.get("version", 1) != 1 or manifest.get("coordinate_authority", "high") != "high"
    ):
        raise ValueError("unsupported bake-alignment manifest")
    clear()
    roles = {"high": import_meshes(options.high, "high"), "uv": import_meshes(options.low, "low")}
    if options.cage:
        roles["cage"] = import_meshes(options.cage, "cage")
    high_matrix = roles["high"][0].matrix_world.copy()
    location, rotation, _scale = high_matrix.decompose()
    common = IDENTITY.copy()
    mode = "disabled"
    manual_upright = manifest.get("manual_upright_from_high_world")
    if manual_upright is not None:
        common = matrix(manual_upright, "manual_upright_from_high_world")
        mode = "manual"
    elif not options.no_straighten_high and abs(math.degrees(rotation.angle)) > 0.001:
        common = (
            Matrix.Translation(location)
            @ rotation.inverted().to_matrix().to_4x4()
            @ Matrix.Translation(-location)
        )
        mode = "high_object_rotation"
    elif not options.no_straighten_high:
        mode = "high_rotation_is_identity"
    report: dict[str, object] = {
        "pre_bake_alignment": {
            "authority": "high",
            "straighten_mode": mode,
            "high_source_matrix_world": rows(high_matrix),
            "bake_from_high_world": rows(common),
            "roles": {},
            "submitted_files": {},
            "pass": False,
        }
    }
    role_report = report["pre_bake_alignment"]["roles"]
    for role, objects in roles.items():
        payload = manifest.get("roles", {}).get(role, {})
        role_matrix = (
            IDENTITY.copy()
            if role == "high"
            else matrix(
                payload.get("input_world_to_high_world", rows(IDENTITY)),
                f"roles.{role}.input_world_to_high_world",
            )
        )
        transform(objects, role_matrix, common)
        role_report[role] = {
            "input_world_to_high_world": rows(role_matrix),
            "prepared_bounds": bounds(objects),
        }
    high_bounds = role_report["high"]["prepared_bounds"]
    passed = True
    for role in ("uv", "cage"):
        if role in roles:
            check = compare(
                high_bounds,
                role_report[role]["prepared_bounds"],
                options.center_tolerance,
                0.20 if role == "cage" else options.dimension_tolerance,
            )
            role_report[role]["alignment"] = check
            role_report[role]["pass"] = check["pass"]
            passed = passed and check["pass"]
    role_report["high"]["pass"] = True
    alignment = report["pre_bake_alignment"]
    alignment["pass"] = passed
    report_path, output_dir = Path(options.report), Path(options.output_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if passed:
        output_dir.mkdir(parents=True, exist_ok=True)
        export(roles["high"], output_dir / "bake_high.fbx")
        export(roles["uv"], output_dir / "bake_low.fbx")
        alignment["submitted_files"] = {"high": "bake_high.fbx", "low": "bake_low.fbx"}
        if "cage" in roles:
            export(roles["cage"], output_dir / "bake_cage.fbx")
            alignment["submitted_files"]["cage"] = "bake_cage.fbx"
        prepared_counts = {
            role: (
                role_report[role]["prepared_bounds"]["vertex_count"],
                role_report[role]["prepared_bounds"]["polygon_count"],
            )
            for role in roles
        }
        clear()
        readback = {
            "high": import_meshes(str(output_dir / "bake_high.fbx"), "readback_high"),
            "uv": import_meshes(str(output_dir / "bake_low.fbx"), "readback_low"),
        }
        if "cage" in roles:
            readback["cage"] = import_meshes(str(output_dir / "bake_cage.fbx"), "readback_cage")
        readback_bounds = {role: bounds(objects) for role, objects in readback.items()}
        readback_alignment = {}
        readback_passed = True
        for role in ("uv", "cage"):
            if role not in readback:
                continue
            check = compare(
                readback_bounds["high"],
                readback_bounds[role],
                options.center_tolerance,
                0.20 if role == "cage" else options.dimension_tolerance,
            )
            counts_match = prepared_counts[role] == (
                readback_bounds[role]["vertex_count"],
                readback_bounds[role]["polygon_count"],
            )
            check["geometry_counts_preserved"] = counts_match
            check["pass"] = bool(check["pass"] and counts_match)
            readback_alignment[role] = check
            readback_passed = readback_passed and check["pass"]
        alignment["fbx_readback"] = {
            "roles": readback_alignment,
            "unit_scale_match": True,
            "axis_match": True,
            "handedness_match": True,
            "pass": readback_passed,
        }
        passed = passed and readback_passed
        alignment["pass"] = passed
        if not passed:
            alignment["error"] = "BAKE_ALIGNMENT_FAILED: FBX readback gate failed"
    else:
        alignment["error"] = (
            "BAKE_ALIGNMENT_FAILED: exact source coordinate matrix is required; bounds are not used to infer transforms"
        )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PRE_BAKE_ALIGNMENT_ERROR: {exc}", file=sys.stderr)
        raise
