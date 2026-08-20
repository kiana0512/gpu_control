"""Blender-side geometry evidence extraction for automatic UV routing."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.gpu_control_core.uv_auto_classification import (  # noqa: E402
    UVGeometryEvidence,
    classify_uv_geometry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(arguments)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def import_asset(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        return
    clear_scene()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False)
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise RuntimeError(f"unsupported UV classification input: {suffix}")


def face_component_count(mesh: bpy.types.Mesh) -> int:
    face_neighbors: list[list[int]] = [[] for _ in mesh.polygons]
    for edge in mesh.edges:
        linked_faces = list(edge.link_faces) if hasattr(edge, "link_faces") else []
        if len(linked_faces) == 2:
            left, right = linked_faces
            face_neighbors[left.index].append(right.index)
            face_neighbors[right.index].append(left.index)
    # MeshEdge does not expose link_faces in object mode. Build the same
    # adjacency from polygon edge keys without changing source geometry.
    if not any(face_neighbors):
        owners: dict[tuple[int, int], list[int]] = {}
        for polygon in mesh.polygons:
            for edge_key in polygon.edge_keys:
                owners.setdefault(tuple(sorted(edge_key)), []).append(polygon.index)
        for linked in owners.values():
            if len(linked) == 2:
                left, right = linked
                face_neighbors[left].append(right)
                face_neighbors[right].append(left)
    visited: set[int] = set()
    components = 0
    for polygon in mesh.polygons:
        if polygon.index in visited:
            continue
        components += 1
        stack = [polygon.index]
        visited.add(polygon.index)
        while stack:
            current = stack.pop()
            for neighbor in face_neighbors[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
    return components


def extract_evidence() -> UVGeometryEvidence:
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    total_faces = 0
    total_vertices = 0
    total_edges = 0
    total_components = 0
    total_smooth_faces = 0
    total_authored_sharp = 0
    manifold_angles: list[float] = []
    boundary_edges = 0
    nonmanifold_edges = 0

    for obj in mesh_objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        total_faces += len(mesh.polygons)
        total_vertices += len(mesh.vertices)
        total_edges += len(mesh.edges)
        total_components += face_component_count(mesh)
        total_smooth_faces += sum(
            1 for polygon in mesh.polygons if polygon.use_smooth
        )
        total_authored_sharp += sum(
            1 for edge in mesh.edges if getattr(edge, "use_edge_sharp", False)
        )

        owners: dict[tuple[int, int], list[int]] = {}
        for polygon in mesh.polygons:
            for edge_key in polygon.edge_keys:
                owners.setdefault(tuple(sorted(edge_key)), []).append(polygon.index)
        for linked in owners.values():
            if len(linked) == 1:
                boundary_edges += 1
            elif len(linked) == 2:
                first = mesh.polygons[linked[0]].normal
                second = mesh.polygons[linked[1]].normal
                dot = max(-1.0, min(1.0, first.dot(second)))
                manifold_angles.append(math.degrees(math.acos(dot)))
            else:
                nonmanifold_edges += 1

    manifold_count = len(manifold_angles)

    def angle_ratio(predicate) -> float:
        if not manifold_count:
            return 0.0
        return sum(1 for angle in manifold_angles if predicate(angle)) / manifold_count

    return UVGeometryEvidence(
        mesh_object_count=len(mesh_objects),
        face_count=total_faces,
        face_component_count=total_components,
        vertex_count=total_vertices,
        edge_count=total_edges,
        manifold_edge_count=manifold_count,
        boundary_edge_count=boundary_edges,
        nonmanifold_edge_count=nonmanifold_edges,
        modifier_count=sum(len(obj.modifiers) for obj in mesh_objects),
        shape_key_count=sum(
            len(obj.data.shape_keys.key_blocks)
            if obj.data.shape_keys is not None
            else 0
            for obj in mesh_objects
        ),
        smooth_face_ratio=total_smooth_faces / total_faces if total_faces else 0.0,
        authored_sharp_edge_ratio=(
            total_authored_sharp / total_edges if total_edges else 0.0
        ),
        near_planar_edge_ratio=angle_ratio(lambda angle: angle <= 2.0),
        curved_edge_ratio=angle_ratio(lambda angle: 2.0 < angle < 60.0),
        steep_edge_ratio=angle_ratio(lambda angle: angle >= 60.0),
        very_steep_edge_ratio=angle_ratio(lambda angle: angle >= 75.0),
    )


def main() -> None:
    arguments = parse_args()
    source = Path(arguments.input).resolve()
    output = Path(arguments.output).resolve()
    if not source.is_file():
        raise RuntimeError(f"UV classification input does not exist: {source}")
    import_asset(source)
    classification = classify_uv_geometry(extract_evidence())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(classification.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(classification.as_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
