#!/usr/bin/env python3
"""Build one high-derived low when semantic regions cannot be separated safely.

This is a generated-low fallback, not an alignment or review stage.  It never
modifies SOURCE_HIGH, never generates UVs, and performs exactly one Blender
geometry build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import bmesh
import bpy


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-blend", required=True)
    parser.add_argument("--output-blend", required=True)
    parser.add_argument("--generation-report", required=True)
    parser.add_argument("--high-object", default="SOURCE_HIGH")
    parser.add_argument("--work-object", default="SOURCE_HIGH_NORMALIZED_WORK")
    parser.add_argument("--low-object", default="NEXTGEN_SOURCE_LOW")
    parser.add_argument("--ratio", type=float, default=0.50)
    parser.add_argument("--prefer-normalized-work", action="store_true")
    return parser.parse_args(argv)


def mesh_fingerprint(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    digest.update(struct.pack("<QQ", len(obj.data.vertices), len(obj.data.polygons)))
    for vertex in obj.data.vertices:
        digest.update(struct.pack("<ddd", *vertex.co))
    for polygon in obj.data.polygons:
        digest.update(struct.pack("<I", len(polygon.vertices)))
        for vertex_index in polygon.vertices:
            digest.update(struct.pack("<I", vertex_index))
    return digest.hexdigest()


def remove_degenerate_faces(obj: bpy.types.Object) -> int:
    mesh = obj.data
    mesh.update(calc_edges=True)
    diagonal = math.sqrt(sum(component * component for component in obj.dimensions))
    area_epsilon = max(diagonal * diagonal * 1e-14, 1e-18)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        broken = [face for face in bm.faces if not math.isfinite(face.calc_area()) or face.calc_area() <= area_epsilon]
        if broken:
            bmesh.ops.delete(bm, geom=broken, context="FACES")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update(calc_edges=True)
    return len(broken)


def assign_display_material(obj: bpy.types.Object) -> None:
    obj.data.materials.clear()
    material = bpy.data.materials.get("NEXTGEN_OPAQUE_ORANGE")
    if material is None:
        material = bpy.data.materials.new("NEXTGEN_OPAQUE_ORANGE")
        material.diffuse_color = (1.0, 0.23, 0.02, 1.0)
    obj.data.materials.append(material)
    obj.color = (1.0, 0.23, 0.02, 1.0)
    for polygon in obj.data.polygons:
        polygon.material_index = 0


def main() -> None:
    args = parse_args()
    if not 0.0 < args.ratio < 1.0:
        raise RuntimeError("controlled reduction ratio must be between zero and one")

    bpy.ops.wm.open_mainfile(filepath=str(Path(args.input_blend).resolve()))
    high = bpy.data.objects.get(args.high_object)
    if high is None or high.type != "MESH":
        raise RuntimeError(f"high mesh not found: {args.high_object}")
    high_fingerprint_before = mesh_fingerprint(high)
    high_faces = len(high.data.polygons)

    work = bpy.data.objects.get(args.work_object)
    source = (
        work
        if args.prefer_normalized_work and work is not None and work.type == "MESH"
        else high
    )
    source_name = source.name
    low = source.copy()
    low.data = source.data.copy()
    low.name = args.low_object
    bpy.context.scene.collection.objects.link(low)
    low.matrix_world = high.matrix_world.copy()

    # Automatic topology does not generate or re-layout UVs.  The source high
    # remains untouched; the newly generated low intentionally starts without UV.
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    modifier = low.modifiers.new(name="CONTROLLED_REDUCTION_FALLBACK", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = args.ratio
    modifier.use_collapse_triangulate = True
    modifier.use_symmetry = False
    bpy.ops.object.modifier_apply(modifier=modifier.name)

    removed_degenerate_faces = remove_degenerate_faces(low)
    low_faces = len(low.data.polygons)
    if low_faces <= 0 or low_faces >= high_faces:
        raise RuntimeError(
            "RETOPOLOGY_TOPOLOGY_INVALID: controlled reduction did not create a lower-face mesh"
        )
    if any(not math.isfinite(component) for vertex in low.data.vertices for component in vertex.co):
        raise RuntimeError("RETOPOLOGY_TOPOLOGY_INVALID: non-finite low vertex")

    assign_display_material(low)
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj not in {high, low}:
            bpy.data.objects.remove(obj, do_unlink=True)
    high.hide_set(False)
    high.hide_render = False
    low.hide_set(False)
    low.hide_render = False

    if mesh_fingerprint(high) != high_fingerprint_before:
        raise RuntimeError("RETOPOLOGY_SOURCE_MUTATED: SOURCE_HIGH changed during fallback")

    output_blend = Path(args.output_blend).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)

    report = {
        "status": "generated_for_user_inspection",
        "assets": [
            {
                "high_object": high.name,
                "low_object": low.name,
                "faces": low_faces,
                "triangles": sum(max(1, len(polygon.vertices) - 2) for polygon in low.data.polygons),
                "uv_layers": len(low.data.uv_layers),
                "method_decision": "controlled_direct_reduction",
                "region_method_map": {
                    "inseparable_fused_asset": {
                        "method": "controlled_direct_reduction",
                        "boundary_evidence": (
                            "classification-only adjacency recovered one fused surface; "
                            "semantic face clipping would tear the source-derived shape"
                        ),
                        "source": source_name,
                        "ratio": args.ratio,
                    }
                },
                "actual_plugin_use": {
                    "used": False,
                    "plugins": [],
                    "note": "deterministic Blender Decimate fallback on a fresh high-derived copy",
                },
                "coordinate_space": "source_high_local",
                "coordinate_authority": "high_object_matrix_world",
                "presentation_offset_applied": False,
            }
        ],
        "source_preserved": True,
        "zero_area_faces": 0,
        "removed_degenerate_faces": removed_degenerate_faces,
        "review_status": "user_inspection_required",
        "uv_policy": "no_uv_generation_or_modification",
    }
    report_path = Path(args.generation_report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "LI3D_CONTROLLED_REDUCTION_FALLBACK_OK:"
        + json.dumps(
            {
                "high_faces": high_faces,
                "low_faces": low_faces,
                "ratio": args.ratio,
                "source": source_name,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
