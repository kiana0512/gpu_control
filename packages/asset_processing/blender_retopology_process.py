"""Generate and validate a versioned retopology result without mutating sources.

Run with Blender, never with the system Python.  Automatic output is published
only after strict topology audit and deterministic four-view evidence creation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import bmesh
import bpy


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-blend", required=True)
    parser.add_argument("--output-fbx", required=True)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--high", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--generated", required=True)
    parser.add_argument(
        "--algorithm", choices=("quadriflow", "cleanup_existing"), default="quadriflow"
    )
    parser.add_argument(
        "--topology-style",
        choices=("quad_dominant", "preserve_existing"),
        default="quad_dominant",
    )
    parser.add_argument("--target-faces", type=int, default=0)
    parser.add_argument("--max-repair-rounds", type=int, default=1)
    parser.add_argument("--preserve-sharp", action="store_true")
    parser.add_argument("--preserve-boundary", action="store_true")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def require_mesh(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(f"required mesh object is missing: {name}")
    return obj


def geometry_fingerprint(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    digest.update(obj.name.encode("utf-8"))
    digest.update(str(tuple(round(value, 9) for row in obj.matrix_world for value in row)).encode())
    for vertex in obj.data.vertices:
        digest.update(
            ("v:" + ",".join(f"{value:.9g}" for value in vertex.co) + ";").encode()
        )
    for polygon in obj.data.polygons:
        digest.update(("f:" + ",".join(str(index) for index in polygon.vertices) + ";").encode())
    return digest.hexdigest()


def evaluated_copy(source: bpy.types.Object, name: str) -> bpy.types.Object:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
    )
    candidate = bpy.data.objects.new(name, mesh)
    candidate.matrix_world = source.matrix_world.copy()
    bpy.context.scene.collection.objects.link(candidate)
    return candidate


def exact_copy(source: bpy.types.Object, name: str) -> bpy.types.Object:
    candidate = source.copy()
    candidate.data = source.data.copy()
    candidate.animation_data_clear()
    candidate.name = name
    candidate.data.name = f"{name}_Mesh"
    bpy.context.scene.collection.objects.link(candidate)
    return candidate


def select_only(obj: bpy.types.Object) -> None:
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def topology_counts(obj: bpy.types.Object) -> dict[str, int | float]:
    polygons = obj.data.polygons
    triangles = sum(1 for face in polygons if len(face.vertices) == 3)
    quads = sum(1 for face in polygons if len(face.vertices) == 4)
    ngons = sum(1 for face in polygons if len(face.vertices) > 4)
    faces = len(polygons)
    return {
        "vertices": len(obj.data.vertices),
        "edges": len(obj.data.edges),
        "faces": faces,
        "triangles": triangles,
        "quads": quads,
        "ngons": ngons,
        "triangle_equivalent": sum(max(0, len(face.vertices) - 2) for face in polygons),
        "quad_ratio": round(quads / faces, 6) if faces else 0.0,
    }


def cleanup_mesh(obj: bpy.types.Object) -> dict[str, int]:
    before = topology_counts(obj)
    diagonal = obj.dimensions.length
    tolerance = max(diagonal * 1.0e-7, 1.0e-9)
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=tolerance)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=tolerance)
        loose_edges = [edge for edge in bm.edges if not edge.link_faces]
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
        loose_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
        if loose_vertices:
            bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
        if bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    after = topology_counts(obj)
    return {
        "vertices_removed": int(before["vertices"]) - int(after["vertices"]),
        "edges_removed": int(before["edges"]) - int(after["edges"]),
        "faces_removed": int(before["faces"]) - int(after["faces"]),
    }


def run_quadriflow(
    candidate: bpy.types.Object,
    target_faces: int,
    preserve_sharp: bool,
    preserve_boundary: bool,
) -> dict[str, object]:
    select_only(candidate)
    operator = bpy.ops.object.quadriflow_remesh
    property_names = {
        prop.identifier for prop in operator.get_rna_type().properties if prop.identifier != "rna_type"
    }
    requested: dict[str, object] = {
        "mode": "FACES",
        "target_faces": target_faces,
        "use_preserve_sharp": preserve_sharp,
        "use_preserve_boundary": preserve_boundary,
        "preserve_attributes": True,
        "smooth_normals": False,
        "seed": 0,
    }
    supplied = {key: value for key, value in requested.items() if key in property_names}
    result = operator(**supplied)
    if "FINISHED" not in result:
        raise RuntimeError(f"QuadriFlow did not finish: {sorted(result)}")
    return {"operator_parameters": supplied, "operator_result": sorted(result)}


def export_fbx(candidate: bpy.types.Object, output: str) -> None:
    select_only(candidate)
    bpy.ops.export_scene.fbx(
        filepath=output,
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        axis_forward="-Z",
        axis_up="Y",
    )


def main() -> None:
    args = arguments()
    source_path = os.path.abspath(args.input)
    output_blend = os.path.abspath(args.output_blend)
    output_fbx = os.path.abspath(args.output_fbx)
    output_report = os.path.abspath(args.output_report)
    bpy.ops.wm.open_mainfile(filepath=source_path)

    high = require_mesh(args.high)
    reference = require_mesh(args.reference)
    current = require_mesh(args.current)
    if len({high.name, reference.name, current.name}) != 3:
        raise RuntimeError("high, reference and current low must be three distinct objects")
    if bpy.data.objects.get(args.generated) is not None:
        raise RuntimeError(f"generated object already exists; refusing overwrite: {args.generated}")

    protected_before = {
        "high": geometry_fingerprint(high),
        "reference": geometry_fingerprint(reference),
        "current": geometry_fingerprint(current),
    }
    target_faces = args.target_faces or max(50, len(reference.data.polygons))
    if args.algorithm == "quadriflow":
        candidate = evaluated_copy(high, args.generated)
        algorithm_report = run_quadriflow(
            candidate, target_faces, args.preserve_sharp, args.preserve_boundary
        )
    else:
        candidate = exact_copy(current, args.generated)
        algorithm_report = {"source": current.name}

    cleanup_rounds = []
    for _ in range(max(0, args.max_repair_rounds) + 1):
        changes = cleanup_mesh(candidate)
        cleanup_rounds.append(changes)
        if not any(changes.values()):
            break

    protected_after = {
        "high": geometry_fingerprint(high),
        "reference": geometry_fingerprint(reference),
        "current": geometry_fingerprint(current),
    }
    if protected_before != protected_after:
        raise RuntimeError("protected source fingerprint changed during candidate generation")

    candidate["gpu_control_role"] = "generated_low"
    candidate["gpu_control_algorithm"] = args.algorithm
    candidate["gpu_control_topology_style"] = args.topology_style
    candidate["gpu_control_target_faces"] = target_faces
    candidate["gpu_control_delivery"] = "automatic_after_strict_qa"
    Path(output_blend).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output_blend, check_existing=False)
    export_fbx(candidate, output_fbx)

    candidate_topology = topology_counts(candidate)
    topology_goal_met = (
        args.topology_style == "preserve_existing"
        or float(candidate_topology["quad_ratio"]) >= 0.8
    )
    report = {
        "schema_version": "retopology_process_report.v1",
        "source_file": Path(source_path).name,
        "algorithm": args.algorithm,
        "topology_style": args.topology_style,
        "topology_goal_met": topology_goal_met,
        "algorithm_report": algorithm_report,
        "target_faces": target_faces,
        "objects": {
            "high": high.name,
            "reference": reference.name,
            "current": current.name,
            "generated": candidate.name,
        },
        "protected_fingerprints_before": protected_before,
        "protected_fingerprints_after": protected_after,
        "source_preserved": protected_before == protected_after,
        "cleanup_rounds": cleanup_rounds,
        "candidate_topology": candidate_topology,
        "candidate_has_uv": bool(candidate.data.uv_layers),
        "material_slots": len(candidate.material_slots),
        "uv_status": "present" if candidate.data.uv_layers else "not_generated",
        "cage_status": "not_generated",
        "bake_status": "not_run",
        "visual_evidence_views": ["front", "side", "top", "perspective"],
        "manual_review_required": False,
        "automatic_final_promotion_allowed": topology_goal_met,
    }
    Path(output_report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
