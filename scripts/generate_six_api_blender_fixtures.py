#!/usr/bin/env python3
"""Generate and self-validate synthetic Blender/FBX six-API fixtures.

Run this file through Blender's ``--python`` option.  It creates primitives
only; it never opens an external source asset and never performs network I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("/opt/gpu-control-load-fixtures/synthetic-six-api-v1")
INCOMPLETE_MARKER = ".synthetic-six-api-v1.incomplete"
TARGETS = (
    "uv/asset.blend",
    "retopology/audit.blend",
    "retopology/process.blend",
    "bake/asset_low.fbx",
    "bake/asset_high.fbx",
    "blender_validation.json",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--allow-incomplete-root", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def prepare_output_root(
    output: Path, repository_root: Path, *, allow_incomplete_root: bool
) -> tuple[Path, Path]:
    root = output.expanduser().resolve(strict=False)
    repository = repository_root.expanduser().resolve()
    if root in {Path("/"), Path("/opt"), Path("/srv"), Path("/tmp")}:  # noqa: S108
        raise RuntimeError("refusing a broad fixture output directory")
    if _is_within(root, repository):
        raise RuntimeError("synthetic load fixtures must remain outside the repository")
    if root.exists():
        if not allow_incomplete_root or not (root / INCOMPLETE_MARKER).is_file():
            raise RuntimeError(f"output already exists; refusing overwrite: {root}")
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o750)
        (root / INCOMPLETE_MARKER).write_text(
            "schema_version=synthetic-six-api-v1\n", encoding="utf-8"
        )
    for relative in TARGETS:
        if (root / relative).exists():
            raise RuntimeError(f"refusing to overwrite Blender fixture: {relative}")
    return root, repository


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_only(bpy: Any, obj: Any) -> None:
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _create_uv_cube(bpy: Any, bmesh: Any, name: str, *, cuts: int) -> Any:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Blender failed to create a synthetic cube")
    obj.name = name
    obj.data.name = f"{name}_Mesh"
    _select_only(bpy, obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if cuts:
        bpy.ops.mesh.subdivide(number_cuts=cuts, smoothness=0.0)
    mesh = bmesh.from_edit_mesh(obj.data)
    mesh.edges.ensure_lookup_table()
    for edge in mesh.edges:
        edge.seam = len(edge.link_faces) != 2 or edge.calc_face_angle(0.0) >= math.radians(60)
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.uv.unwrap(method="CONFORMAL", margin=0.02)
    bpy.ops.uv.average_islands_scale()
    bpy.ops.uv.pack_islands(margin=0.02, rotate=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.data.update()
    return obj


def _mesh_record(bmesh: Any, obj: Any, *, require_uv: bool) -> dict[str, Any]:
    if obj.type != "MESH" or not obj.data.polygons:
        raise RuntimeError(f"fixture object is not a non-empty mesh: {obj.name}")
    uv_layer = obj.data.uv_layers.active
    if require_uv and (uv_layer is None or len(uv_layer.data) == 0):
        raise RuntimeError(f"fixture object has no UV data: {obj.name}")
    mesh = bmesh.new()
    try:
        mesh.from_mesh(obj.data)
        boundary_edges = sum(len(edge.link_faces) != 2 for edge in mesh.edges)
        ngons = sum(len(face.verts) > 4 for face in mesh.faces)
    finally:
        mesh.free()
    if boundary_edges:
        raise RuntimeError(f"fixture object is not closed: {obj.name}")
    if ngons:
        raise RuntimeError(f"fixture object contains N-gons: {obj.name}")
    dimensions = [round(float(value), 6) for value in obj.dimensions]
    return {
        "name": obj.name,
        "vertices": len(obj.data.vertices),
        "edges": len(obj.data.edges),
        "faces": len(obj.data.polygons),
        "uv_layer": uv_layer.name if uv_layer is not None else None,
        "uv_loops": len(uv_layer.data) if uv_layer is not None else 0,
        "boundary_edges": boundary_edges,
        "ngons": ngons,
        "dimensions": dimensions,
    }


def _export_fbx(bpy: Any, obj: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite Blender fixture: {destination}")
    _select_only(bpy, obj)
    bpy.ops.export_scene.fbx(
        filepath=str(destination),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        axis_forward="-Z",
        axis_up="Y",
    )
    if not destination.is_file() or destination.stat().st_size < 1:
        raise RuntimeError(f"FBX export is missing: {destination}")


def _validate_blend(
    bpy: Any,
    bmesh: Any,
    path: Path,
    expected_names: tuple[str, ...],
) -> dict[str, Any]:
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    records = []
    for name in expected_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise RuntimeError(f"BLEND fixture is missing object {name}: {path}")
        records.append(_mesh_record(bmesh, obj, require_uv=True))
    if len({tuple(record["dimensions"]) for record in records}) != 1:
        raise RuntimeError(f"retopology fixture bounds are not aligned: {path}")
    return {"path": path.name, "objects": records}


def _validate_fbx(bpy: Any, bmesh: Any, path: Path) -> dict[str, Any]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.fbx_import(filepath=str(path))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(meshes) != 1:
        raise RuntimeError(f"FBX fixture must contain exactly one mesh: {path}")
    return {"path": path.name, "object": _mesh_record(bmesh, meshes[0], require_uv=True)}


def _save_uncompressed_blend(bpy: Any, path: Path) -> None:
    """Write the raw BLENDER signature required by the Direct V2 package contract."""

    bpy.ops.wm.save_as_mainfile(
        filepath=str(path),
        check_existing=False,
        compress=False,
    )


def generate(root: Path) -> dict[str, Any]:
    import bmesh
    import bpy

    uv_path = root / "uv" / "asset.blend"
    audit_path = root / "retopology" / "audit.blend"
    process_path = root / "retopology" / "process.blend"
    low_fbx = root / "bake" / "asset_low.fbx"
    high_fbx = root / "bake" / "asset_high.fbx"
    for path in (uv_path, audit_path, process_path, low_fbx, high_fbx):
        path.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    uv_source = _create_uv_cube(bpy, bmesh, "synthetic_uv_source", cuts=3)
    _mesh_record(bmesh, uv_source, require_uv=True)
    _save_uncompressed_blend(bpy, uv_path)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    high = _create_uv_cube(bpy, bmesh, "synthetic_high", cuts=7)
    reference = _create_uv_cube(bpy, bmesh, "synthetic_reference", cuts=3)
    low = _create_uv_cube(bpy, bmesh, "synthetic_low", cuts=3)
    for obj in (high, reference, low):
        _mesh_record(bmesh, obj, require_uv=True)
    _save_uncompressed_blend(bpy, audit_path)
    _export_fbx(bpy, low, low_fbx)
    _export_fbx(bpy, high, high_fbx)

    # Direct V2 accepts one high-poly source project and generates a new low
    # mesh from it.  The audit fixture deliberately contains the three roles,
    # but copying that scene into the processing fixture would make the
    # pre-existing low/reference meshes look like additional high sources.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    process_high = _create_uv_cube(bpy, bmesh, "synthetic_high", cuts=7)
    _mesh_record(bmesh, process_high, require_uv=True)
    _save_uncompressed_blend(bpy, process_path)

    validation = {
        "schema_version": "synthetic_blender_fixtures.v1",
        "passed": True,
        "blender_version": bpy.app.version_string,
        "checks": {
            "uv": _validate_blend(
                bpy, bmesh, uv_path, ("synthetic_uv_source",)
            ),
            "retopology_audit": _validate_blend(
                bpy,
                bmesh,
                audit_path,
                ("synthetic_high", "synthetic_reference", "synthetic_low"),
            ),
            "retopology_process": _validate_blend(
                bpy,
                bmesh,
                process_path,
                ("synthetic_high",),
            ),
            "bake_low": _validate_fbx(bpy, bmesh, low_fbx),
            "bake_high": _validate_fbx(bpy, bmesh, high_fbx),
        },
    }
    artifact_paths = {
        "uv/asset.blend": uv_path,
        "retopology/audit.blend": audit_path,
        "retopology/process.blend": process_path,
        "bake/asset_low.fbx": low_fbx,
        "bake/asset_high.fbx": high_fbx,
    }
    validation["artifacts"] = {
        relative: sha256_path(path) for relative, path in artifact_paths.items()
    }
    receipt = root / "blender_validation.json"
    try:
        with receipt.open("x", encoding="utf-8") as target:
            json.dump(validation, target, ensure_ascii=False, sort_keys=True, indent=2)
            target.write("\n")
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite Blender receipt: {receipt}") from exc
    return validation


def main() -> None:
    args = arguments()
    root, _ = prepare_output_root(
        args.output,
        args.repository_root,
        allow_incomplete_root=args.allow_incomplete_root,
    )
    result = generate(root)
    print(
        "SYNTHETIC_BLENDER_FIXTURES",
        json.dumps(
            {
                "passed": result["passed"],
                "blender_version": result["blender_version"],
                "root": str(root),
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
