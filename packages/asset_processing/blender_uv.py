"""Blender 5.1 headless PBR UV pipeline entry point.

This module is launched by Blender, not by the API process.  It writes only
the four final contract artifacts into a fresh output directory.
"""

import argparse
import json
import math
import sys
from pathlib import Path


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--options-json", required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def main() -> None:
    import bmesh
    import bpy

    args = arguments()
    source = Path(args.input).resolve()
    output = Path(args.output_dir).resolve()
    options = json.loads(args.options_json)
    output.mkdir(parents=True, exist_ok=False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = source.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source))
    elif suffix == ".fbx":
        bpy.ops.wm.fbx_import(filepath=str(source))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(source))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(source))
    else:
        raise RuntimeError(f"unsupported input format: {suffix}")

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("input contains no mesh objects")

    hard_angle = math.radians(float(options["hard_edge_angle_degrees"]))
    resolution = int(options["resolution"])
    padding_px = int(options["padding_px"])
    object_reports = []
    hard_failures: list[dict[str, object]] = []

    for obj in mesh_objects:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        for other in mesh_objects:
            if other != obj:
                other.select_set(False)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        seam_count = 0
        for edge in bm.edges:
            angle = edge.calc_face_angle(0.0)
            should_seam = len(edge.link_faces) != 2 or angle >= hard_angle
            edge.seam = should_seam
            seam_count += int(should_seam)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.unwrap(method="CONFORMAL", margin=padding_px / resolution)
        bpy.ops.uv.average_islands_scale()
        bpy.ops.uv.pack_islands(margin=padding_px / resolution, rotate=True)
        bpy.ops.object.mode_set(mode="OBJECT")

        uv_layer = obj.data.uv_layers.active
        if uv_layer is None:
            hard_failures.append({"object": obj.name, "code": "MISSING_UV_LAYER"})
            continue
        out_of_bounds = 0
        non_finite = 0
        for loop in uv_layer.data:
            u, v = float(loop.uv.x), float(loop.uv.y)
            if not math.isfinite(u) or not math.isfinite(v):
                non_finite += 1
            elif u < -1e-6 or u > 1.000001 or v < -1e-6 or v > 1.000001:
                out_of_bounds += 1
        if out_of_bounds:
            hard_failures.append(
                {"object": obj.name, "code": "UV_OUT_OF_BOUNDS", "count": out_of_bounds}
            )
        if non_finite:
            hard_failures.append(
                {"object": obj.name, "code": "DEGENERATE_UV", "count": non_finite}
            )
        object_reports.append(
            {
                "object": obj.name,
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
                "seam_edges": seam_count,
                "uv_layer": uv_layer.name,
                "out_of_bounds_loops": out_of_bounds,
            }
        )

    blend_path = output / "model_PBR_UV.blend"
    fbx_path = output / "model_PBR_UV.fbx"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.fbx(filepath=str(fbx_path), use_selection=False)

    report = {
        "schema_version": "1.0",
        "pipeline": "blender-pbr-uv",
        "blender_version": bpy.app.version_string,
        "input_filename": source.name,
        "options": options,
        "objects": object_reports,
    }
    qa = {
        "schema_version": "1.0",
        "passed": not hard_failures,
        "hard_failures": hard_failures,
        "thresholds": {"stretch_p90_max": 1.2, "stretch_p95_max": 1.5},
        "note": "overlap and stretch metrics require the production QA extension before FROZEN",
    }
    (output / "model_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "model_QA.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if hard_failures:
        raise RuntimeError(f"UV QA hard failures: {len(hard_failures)}")


if __name__ == "__main__":
    main()
