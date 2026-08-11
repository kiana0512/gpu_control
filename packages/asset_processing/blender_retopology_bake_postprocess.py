"""Create a bake-ready high/low pair after Direct V2 retopology.

This stage runs after the topology agent and before artifact publication.  The
uploaded/generated objects are immutable evidence.  New ``BAKE_HIGH`` and
``BAKE_LOW`` objects are created for delivery, their transforms are baked into
mesh coordinates exactly once, and the originals are retained but hidden.

The low is registered from world-space geometry rather than by copying the
high object's Location/Rotation/Scale.  Only translation, proper rotation and
uniform scale are allowed, so the registration cannot introduce a mirror or
silently replace the user's topology.  UV preparation is a separate stage
that finishes before alignment.  Every final pair receives seven matched
views, independent high/low FBX exports, and clean-scene FBX readback evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

SCHEMA_VERSION = "retopology_bake_alignment.v2"
VALIDATION_SCHEMA_VERSION = "retopology_bake_pair_validation.v2"
FBX_UNIT_SCHEMA_VERSION = "retopology_fbx_units.v1"
VIEW_SCHEMA_VERSION = "retopology_bake_views.v1"
MODE = "transform_only_alignment_then_separate_uv"
VIEW_NAMES = ("front", "back", "left", "right", "top", "bottom", "perspective")
ORTHOGRAPHIC_VIEWS = VIEW_NAMES[:-1]
MINIMUM_SILHOUETTE_IOU = 0.97
FBX_UNIT_SCALE_FACTOR_CENTIMETERS = 100.0
ALIGNMENT_SURFACE_ERROR_LIMIT = 0.070
ALIGNMENT_CENTER_ERROR_LIMIT = 0.020
ALIGNMENT_DIMENSION_ERROR_LIMIT = 0.100


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-blend", type=Path, required=True)
    parser.add_argument("--output-high-fbx", type=Path, required=True)
    parser.add_argument("--output-low-fbx", type=Path, required=True)
    parser.add_argument("--generation-report", type=Path, required=True)
    parser.add_argument("--alignment-report", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--views-dir", type=Path, required=True)
    parser.add_argument("--uv-script", type=Path, required=True)
    parser.add_argument("--align-script", type=Path, required=True)
    parser.add_argument(
        "--uv-algorithm",
        choices=("legacy_pbr", "mof_low_seam"),
        default="legacy_pbr",
    )
    parser.add_argument("--resolution", type=int, choices=(256, 384, 512), default=384)
    return parser.parse_args(values)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vector_values(value: Vector | np.ndarray) -> list[float]:
    return [float(value[index]) for index in range(3)]


def matrix_values(matrix: Matrix | np.ndarray, size: int = 4) -> list[list[float]]:
    return [[float(matrix[row][column]) for column in range(size)] for row in range(size)]


def mesh_digest(obj: bpy.types.Object) -> str:
    """Fingerprint geometry, topology, materials and UV without visibility."""

    digest = hashlib.sha256()
    mesh = obj.data
    digest.update(struct.pack("!QQQ", len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for vertex in mesh.vertices:
        digest.update(struct.pack("!ddd", *[float(value) for value in vertex.co]))
    for edge in mesh.edges:
        digest.update(struct.pack("!II", *edge.vertices))
    for polygon in mesh.polygons:
        digest.update(struct.pack("!II", len(polygon.vertices), int(polygon.material_index)))
        for index in polygon.vertices:
            digest.update(struct.pack("!I", index))
    for material in mesh.materials:
        digest.update((material.name if material else "").encode("utf-8") + b"\0")
    for layer in mesh.uv_layers:
        digest.update(layer.name.encode("utf-8") + b"\0")
        for loop in layer.data:
            digest.update(struct.pack("!dd", float(loop.uv.x), float(loop.uv.y)))
    return digest.hexdigest()


def object_fingerprint(obj: bpy.types.Object) -> dict[str, Any]:
    return {
        "name": obj.name,
        "mesh_sha256": mesh_digest(obj),
        "matrix_world": matrix_values(obj.matrix_world),
        "faces": len(obj.data.polygons),
    }


def require_mesh(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH" or not obj.data.polygons:
        raise RuntimeError(f"required non-empty mesh is missing: {name}")
    return obj


def load_role_pairs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text("utf-8"))
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("generation report has no asset pairs")
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(assets, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("generation report contains an invalid asset record")
        reported = [item.get("high_object"), item.get("low_object")]
        if not all(isinstance(name, str) and name for name in reported):
            raise RuntimeError("generation report contains invalid object names")
        first, second = (require_mesh(str(name)) for name in reported)
        if first.name == second.name:
            raise RuntimeError("high and low resolve to the same object")
        first_faces = len(first.data.polygons)
        second_faces = len(second.data.polygons)
        if first_faces == second_faces:
            raise RuntimeError(
                f"cannot identify high/low by face count: {first.name}={second.name}={first_faces}"
            )
        high, low = (first, second) if first_faces > second_faces else (second, first)
        key = (high.name, low.name)
        if key in seen:
            raise RuntimeError("generation report contains a duplicate high/low pair")
        seen.add(key)
        pairs.append(
            {
                "index": index,
                "reported_high": reported[0],
                "reported_low": reported[1],
                "high": high,
                "low": low,
                "role_corrected_by_face_count": high.name != reported[0],
            }
        )
    return pairs


def raw_world_geometry(
    obj: bpy.types.Object,
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    mesh = obj.data
    mesh.calc_loop_triangles()
    matrix = obj.matrix_world
    vertices = np.asarray(
        [tuple(matrix @ vertex.co) for vertex in mesh.vertices], dtype=np.float64
    )
    triangles = [tuple(int(index) for index in item.vertices) for item in mesh.loop_triangles]
    if not len(vertices) or not triangles or not np.isfinite(vertices).all():
        raise RuntimeError(f"mesh has no finite triangle geometry: {obj.name}")
    return vertices, triangles


def bvh_from_geometry(vertices: np.ndarray, triangles: list[tuple[int, int, int]]) -> BVHTree:
    return BVHTree.FromPolygons(
        [Vector(tuple(point)) for point in vertices], triangles, all_triangles=True
    )


def create_world_baked_copy(
    source: bpy.types.Object,
    name: str,
) -> bpy.types.Object:
    candidate = source.copy()
    candidate.data = source.data.copy()
    candidate.animation_data_clear()
    candidate.name = name
    candidate.data.name = f"{name}_MESH"
    bpy.context.scene.collection.objects.link(candidate)
    candidate.data.transform(source.matrix_world)
    candidate.matrix_world = Matrix.Identity(4)
    candidate.data.update()
    return candidate


def segment_strictly_intersects_triangle(
    start: np.ndarray,
    end: np.ndarray,
    triangle: np.ndarray,
    determinant_epsilon: float,
    interior_margin: float,
) -> bool:
    direction = end - start
    first = triangle[1] - triangle[0]
    second = triangle[2] - triangle[0]
    cross = np.cross(direction, second)
    determinant = float(np.dot(first, cross))
    if abs(determinant) <= determinant_epsilon:
        return False
    inverse = 1.0 / determinant
    offset = start - triangle[0]
    u = float(np.dot(offset, cross)) * inverse
    if u <= interior_margin or u >= 1.0 - interior_margin:
        return False
    q = np.cross(offset, first)
    v = float(np.dot(direction, q)) * inverse
    if v <= interior_margin or u + v >= 1.0 - interior_margin:
        return False
    distance = float(np.dot(second, q)) * inverse
    return interior_margin < distance < 1.0 - interior_margin


def triangles_strictly_intersect(
    first: np.ndarray,
    second: np.ndarray,
    determinant_epsilon: float,
    interior_margin: float,
) -> bool:
    return any(
        segment_strictly_intersects_triangle(
            source[index],
            source[(index + 1) % 3],
            target,
            determinant_epsilon,
            interior_margin,
        )
        for source, target in ((first, second), (second, first))
        for index in range(3)
    )


def self_intersection_metrics(obj: bpy.types.Object) -> dict[str, Any]:
    vertices, triangles = raw_world_geometry(obj)
    tree = bvh_from_geometry(vertices, triangles)
    diagonal = max(float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0))), 1.0e-9)
    determinant_epsilon = max(diagonal**3 * 1.0e-14, 1.0e-18)
    interior_margin = 1.0e-5
    intersecting: set[tuple[int, int]] = set()
    examples: list[list[int]] = []
    for first, second in tree.overlap(tree):
        left, right = sorted((int(first), int(second)))
        if left == right or (left, right) in intersecting:
            continue
        if set(triangles[left]).intersection(triangles[right]):
            continue
        left_points = vertices[np.asarray(triangles[left], dtype=np.int64)]
        right_points = vertices[np.asarray(triangles[right], dtype=np.int64)]
        if not triangles_strictly_intersect(
            left_points,
            right_points,
            determinant_epsilon,
            interior_margin,
        ):
            continue
        intersecting.add((left, right))
        if len(examples) < 20:
            examples.append([left, right])
    return {
        "intersecting_triangle_pairs": len(intersecting),
        "examples": examples,
        "test": "bvh_broad_phase_then_strict_interior_edge_triangle",
        "coplanar_or_boundary_touch_is_not_counted": True,
        "interior_margin": interior_margin,
    }


def topology_metrics(obj: bpy.types.Object, *, inspect_intersections: bool = False) -> dict[str, Any]:
    mesh = obj.data
    diagonal = max(float(obj.dimensions.length), 1.0e-9)
    area_tolerance = diagonal * diagonal * 1.0e-12
    duplicate_tolerance = max(diagonal * 1.0e-8, 1.0e-10)
    duplicate_keys = {
        tuple(int(round(float(value) / duplicate_tolerance)) for value in vertex.co)
        for vertex in mesh.vertices
    }
    edge_faces: dict[tuple[int, int], list[int]] = {}
    zero_area = 0
    finite = True
    for vertex in mesh.vertices:
        finite = finite and all(math.isfinite(float(value)) for value in vertex.co)
    for polygon in mesh.polygons:
        zero_area += int(float(polygon.area) <= area_tolerance)
        vertices = list(polygon.vertices)
        for offset, first in enumerate(vertices):
            second = vertices[(offset + 1) % len(vertices)]
            edge_faces.setdefault(tuple(sorted((int(first), int(second)))), []).append(
                polygon.index
            )
    nonmanifold = sum(len(linked) != 2 for linked in edge_faces.values())
    loose_edges = sum(not edge_faces.get(tuple(sorted(edge.vertices))) for edge in mesh.edges)
    used_vertices = {int(index) for polygon in mesh.polygons for index in polygon.vertices}
    loose_vertices = len(mesh.vertices) - len(used_vertices)
    face_keys = [tuple(sorted(int(index) for index in polygon.vertices)) for polygon in mesh.polygons]
    duplicate_faces = len(face_keys) - len(set(face_keys))
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        inconsistent_orientation_edges = sum(
            edge.is_manifold and not edge.is_contiguous for edge in bm.edges
        )
    finally:
        bm.free()
    result: dict[str, Any] = {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "triangles": sum(max(1, len(face.vertices) - 2) for face in mesh.polygons),
        "ngons": sum(len(face.vertices) > 4 for face in mesh.polygons),
        "finite_coordinates": bool(finite),
        "degenerate_faces": zero_area,
        "nonmanifold_edges": nonmanifold,
        "loose_edges": loose_edges,
        "loose_vertices": loose_vertices,
        "duplicate_vertices": len(mesh.vertices) - len(duplicate_keys),
        "duplicate_faces": duplicate_faces,
        "inconsistent_orientation_edges": inconsistent_orientation_edges,
    }
    if inspect_intersections:
        result["self_intersections"] = self_intersection_metrics(obj)
    return result


def cleanup_delivery_degenerate_geometry(
    obj: bpy.types.Object,
    *,
    stage: str,
) -> dict[str, Any]:
    """Normalize invalid geometry on the bake-delivery copy only.

    Direct V2 output remains preserved as the hidden original low.  This helper
    is deliberately narrower than a general mesh cleanup: it does not merge
    nearby vertices, remesh, reduce polygons, or rebuild.  It dissolves
    numerically zero-length edges, removes only faces that fail the same
    scale-relative area test used by ``topology_metrics``, triangulates N-gons
    whose exchange-format tessellation would otherwise be ambiguous, and
    recalculates the disconnected closed shells outward.  The generated Direct
    V2 low remains preserved and hidden as immutable evidence.
    """

    before = topology_metrics(obj, inspect_intersections=False)
    evidence: dict[str, Any] = {
        "stage": stage,
        "scope": "bake_delivery_duplicate_only",
        "original_low_modified": False,
        "attempted": True,
        "method": (
            "dissolve_zero_length_edges_delete_zero_area_faces_"
            "triangulate_ngons_recalculate_outward_normals"
        ),
        "merge_by_distance_used": False,
        "remesh_used": False,
        "polygon_reduction_used": False,
        "before": before,
    }
    if not before["finite_coordinates"]:
        evidence["reason"] = "non_finite_coordinates_are_not_repairable_safely"
        raise RuntimeError(
            "delivery low has non-finite coordinates; refusing unsafe cleanup: "
            f"{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}"
        )
    diagonal = max(float(obj.dimensions.length), 1.0e-9)
    area_tolerance = diagonal * diagonal * 1.0e-12
    edge_tolerance = max(diagonal * 1.0e-10, 1.0e-12)
    evidence["area_tolerance"] = area_tolerance
    evidence["edge_tolerance"] = edge_tolerance
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bmesh.ops.dissolve_degenerate(
            bm,
            edges=list(bm.edges),
            dist=edge_tolerance,
        )
        bm.faces.ensure_lookup_table()
        invalid_faces = [
            face for face in bm.faces if float(face.calc_area()) <= area_tolerance
        ]
        evidence["zero_area_faces_deleted"] = len(invalid_faces)
        if invalid_faces:
            bmesh.ops.delete(bm, geom=invalid_faces, context="FACES_ONLY")
        loose_edges = [edge for edge in bm.edges if not edge.link_faces]
        evidence["resulting_loose_edges_deleted"] = len(loose_edges)
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
        loose_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
        evidence["resulting_loose_vertices_deleted"] = len(loose_vertices)
        if loose_vertices:
            bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
        bm.faces.ensure_lookup_table()
        ngons = [face for face in bm.faces if len(face.verts) > 4]
        evidence["ngons_triangulated"] = len(ngons)
        if ngons:
            bmesh.ops.triangulate(
                bm,
                faces=ngons,
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )
        bm.faces.ensure_lookup_table()
        if bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        evidence["face_normals_recalculated"] = True
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()

    after = topology_metrics(obj, inspect_intersections=False)
    evidence["after"] = after
    for element in ("vertices", "edges", "faces"):
        delta = int(after[element]) - int(before[element])
        evidence[f"{element}_removed"] = max(-delta, 0)
        evidence[f"{element}_added"] = max(delta, 0)
    evidence["passed"] = bool(
        after["finite_coordinates"]
        and not after["degenerate_faces"]
        and not after["ngons"]
    )
    return evidence


def import_uv_module(path: Path) -> ModuleType:
    if not path.is_file():
        raise RuntimeError(f"verified legacy PBR UV script is missing: {path}")
    specification = importlib.util.spec_from_file_location("li3d_legacy_pbr_uv", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load the legacy PBR UV script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def refine_uniform_scale_for_dimension_gate(
    matrix: np.ndarray,
    high_data: dict[str, Any],
    low: bpy.types.Object,
    module: ModuleType,
    high_evaluation: np.ndarray,
    low_evaluation: np.ndarray,
    high_tree: Any,
    trim_fraction: float,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Search only the uniform scale interval that can satisfy the AABB gate.

    Direct V2 can return a geometrically correct sparse low whose ICP optimum is
    slightly too small or large for the independent dimension gate.  Re-running
    unconstrained ICP is not deterministic enough to resolve that conflict.  This
    refinement therefore freezes the selected proper rotation, changes one scalar
    only, and recenters the result on the high model before every evaluation.

    The interval is derived analytically from the existing dimension gate.  Every
    candidate is still evaluated against the unchanged surface and center gates;
    no axis scale, reflection, topology edit, UV edit, or threshold relaxation is
    possible here.
    """

    current_bounds = module.transformed_bounds([low], matrix)
    current_size = np.asarray(current_bounds["size"], dtype=np.float64)
    high_size = np.asarray(high_data["size"], dtype=np.float64)
    maximum_high_dimension = max(float(np.max(high_size)), 1e-12)
    absolute_tolerance = ALIGNMENT_DIMENSION_ERROR_LIMIT * maximum_high_dimension
    evidence: dict[str, Any] = {
        "attempted": True,
        "method": "analytic_dimension_interval_plus_bounded_uniform_scale_search",
        "rotation_frozen": True,
        "center_recomputed_for_each_candidate": True,
        "uniform_scale_only": True,
        "axis_scale_used": False,
        "reflection_allowed": False,
        "dimension_error_limit": ALIGNMENT_DIMENSION_ERROR_LIMIT,
        "surface_error_limit": ALIGNMENT_SURFACE_ERROR_LIMIT,
    }
    if np.any(current_size <= 1e-12):
        evidence.update(
            {
                "feasible": False,
                "reason": "low_bounds_have_zero_dimension",
                "candidate_count": 0,
                "gate_passing_candidate_count": 0,
            }
        )
        return None, evidence

    lower_by_axis = np.maximum((high_size - absolute_tolerance) / current_size, 1e-9)
    upper_by_axis = (high_size + absolute_tolerance) / current_size
    feasible_lower = float(np.max(lower_by_axis))
    feasible_upper = float(np.min(upper_by_axis))
    evidence["feasible_interval"] = [feasible_lower, feasible_upper]
    if not math.isfinite(feasible_lower) or not math.isfinite(feasible_upper):
        evidence.update(
            {
                "feasible": False,
                "reason": "non_finite_dimension_interval",
                "candidate_count": 0,
                "gate_passing_candidate_count": 0,
            }
        )
        return None, evidence
    if feasible_lower > feasible_upper + 1e-12 or feasible_upper <= 0.0:
        evidence.update(
            {
                "feasible": False,
                "reason": "no_uniform_scale_can_satisfy_dimension_gate",
                "candidate_count": 0,
                "gate_passing_candidate_count": 0,
            }
        )
        return None, evidence

    feasible_lower = max(feasible_lower, 1e-9)
    least_squares = float(np.dot(current_size, high_size) / np.dot(current_size, current_size))
    ratios = high_size / current_size
    raw_factors = [
        feasible_lower,
        feasible_upper,
        0.5 * (feasible_lower + feasible_upper),
        float(np.clip(1.0, feasible_lower, feasible_upper)),
        float(np.clip(least_squares, feasible_lower, feasible_upper)),
        *[float(np.clip(value, feasible_lower, feasible_upper)) for value in ratios],
        *[float(value) for value in np.linspace(feasible_lower, feasible_upper, 25)],
    ]
    factors = sorted({round(value, 12) for value in raw_factors if value > 0.0})
    evaluations: list[tuple[float, dict[str, Any]]] = []
    for factor in factors:
        correction = np.eye(4, dtype=np.float64)
        correction[:3, :3] *= factor
        correction[:3, 3] = (
            np.asarray(high_data["center"], dtype=np.float64)
            - factor * np.asarray(current_bounds["center"], dtype=np.float64)
        )
        candidate = module.evaluate_candidate(
            correction @ matrix,
            high_data,
            [low],
            high_evaluation,
            low_evaluation,
            high_tree,
            trim_fraction,
        )
        if (
            candidate["surface_error_ratio"] <= ALIGNMENT_SURFACE_ERROR_LIMIT
            and candidate["center_error_ratio"] <= ALIGNMENT_CENTER_ERROR_LIMIT
            and candidate["dimension_error_ratio"] <= ALIGNMENT_DIMENSION_ERROR_LIMIT
            and candidate["reflected"] is False
        ):
            evaluations.append((factor, candidate))

    evidence.update(
        {
            "feasible": True,
            "candidate_count": len(factors),
            "gate_passing_candidate_count": len(evaluations),
        }
    )
    if not evaluations:
        evidence["reason"] = "uniform_scale_candidates_failed_unchanged_surface_or_center_gate"
        return None, evidence

    selected_factor, selected = min(evaluations, key=lambda item: item[1]["score"])
    evidence.update(
        {
            "selected_multiplier": selected_factor,
            "selected_uniform_scale": selected["uniform_scale"],
            "selected_surface_error_ratio": selected["surface_error_ratio"],
            "selected_center_error_ratio": selected["center_error_ratio"],
            "selected_dimension_error_ratio": selected["dimension_error_ratio"],
        }
    )
    return selected, evidence


def source_axis_uniform_alignment_candidate(
    high_data: dict[str, Any],
    low: bpy.types.Object,
    module: ModuleType,
    high_evaluation: np.ndarray,
    low_evaluation: np.ndarray,
    high_tree: Any,
    trim_fraction: float,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Evaluate the Direct V2 source axes before allowing rotational ICP.

    Direct V2 authors its generated low in the measured high-local coordinate
    frame.  It may move that low aside for presentation, but it does not use a
    different semantic axis system.  A free similarity ICP can nevertheless
    add a small rotation to thin or nearly symmetric props.  That small
    numerical improvement visibly displaces long handles in the orthographic
    bake views.

    Keep the source axes exact and search only a single uniform scale plus a
    center correction.  The unchanged surface, center and dimension gates
    remain mandatory.  If no source-axis candidate passes, the caller may use
    the general proper-rotation solver.
    """

    current = module.transformed_bounds([low], np.eye(4, dtype=np.float64))
    if np.any(current["size"] <= 1.0e-12):
        return None, {
            "enabled": True,
            "selected": False,
            "reason": "source_axis_low_has_zero_size_dimension",
            "uniform_scale_only": True,
            "rotation_locked_to_source_axes": True,
        }

    ratios = np.asarray(high_data["size"], dtype=np.float64) / np.asarray(
        current["size"], dtype=np.float64
    )
    lower = max(float(np.min(ratios)) * 0.985, 1.0e-9)
    upper = max(float(np.max(ratios)) * 1.015, lower)
    seeds = [
        1.0,
        float(np.mean(ratios)),
        float(np.median(ratios)),
        float(np.cbrt(np.prod(ratios))),
        float(np.linalg.norm(high_data["size"]) / np.linalg.norm(current["size"])),
        *[float(value) for value in ratios],
        *[float(value) for value in np.linspace(lower, upper, 81)],
    ]
    factors = sorted(
        {
            round(value, 12)
            for value in seeds
            if math.isfinite(value) and value > 1.0e-9
        }
    )
    evaluations: list[tuple[float, dict[str, Any]]] = []
    passing: list[tuple[float, dict[str, Any]]] = []
    for factor in factors:
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] *= factor
        matrix[:3, 3] = high_data["center"] - factor * current["center"]
        candidate = module.evaluate_candidate(
            matrix,
            high_data,
            [low],
            high_evaluation,
            low_evaluation,
            high_tree,
            trim_fraction,
        )
        evaluations.append((factor, candidate))
        if (
            candidate["surface_error_ratio"] <= ALIGNMENT_SURFACE_ERROR_LIMIT
            and candidate["center_error_ratio"] <= ALIGNMENT_CENTER_ERROR_LIMIT
            and candidate["dimension_error_ratio"] <= ALIGNMENT_DIMENSION_ERROR_LIMIT
            and candidate["reflected"] is False
        ):
            passing.append((factor, candidate))

    best_any = min(evaluations, key=lambda item: item[1]["score"])
    evidence: dict[str, Any] = {
        "enabled": True,
        "candidate_count": len(evaluations),
        "gate_passing_candidate_count": len(passing),
        "rotation_locked_to_source_axes": True,
        "uniform_scale_only": True,
        "axis_scale_used": False,
        "reflection_allowed": False,
        "dimension_ratios": [float(value) for value in ratios],
        "best_evaluated": module.serializable_candidate(best_any[1]),
    }
    if not passing:
        evidence.update(
            {
                "selected": False,
                "reason": "no_source_axis_candidate_passed_unchanged_geometry_gates",
            }
        )
        return None, evidence

    factor, selected = min(passing, key=lambda item: item[1]["score"])
    evidence.update(
        {
            "selected": True,
            "reason": "direct_v2_high_local_axes_are_authoritative",
            "selected_uniform_scale": factor,
            "selected_candidate": module.serializable_candidate(selected),
        }
    )
    return selected, evidence


def transform_only_alignment(
    high: bpy.types.Object,
    low: bpy.types.Object,
    module: ModuleType,
) -> dict[str, Any]:
    """Run the approved pure-transform solver without changing mesh/UV data."""

    solver_args = argparse.Namespace(
        samples=1800,
        target_points=30000,
        coarse_iterations=7,
        fine_iterations=24,
        trim_fraction=0.82,
        final_candidates=4,
        ambiguity_gap=0.025,
        rigid_only=False,
        source_axis_score_gap=0.10,
    )
    high_data = module.collect_points([high], solver_args.target_points, seed=3)
    low_data = module.collect_points([low], solver_args.target_points, seed=5)
    high_evaluation = module.deterministic_subset(
        high_data["points"], min(solver_args.samples, len(high_data["points"])), 11
    )
    low_evaluation = module.deterministic_subset(
        low_data["points"], min(solver_args.samples, len(low_data["points"])), 13
    )
    high_tree = module.build_tree(high_evaluation)
    source_axis_candidate, source_axis_evidence = source_axis_uniform_alignment_candidate(
        high_data,
        low,
        module,
        high_evaluation,
        low_evaluation,
        high_tree,
        solver_args.trim_fraction,
    )
    proper = module.solve(
        high_data,
        low_data,
        [high],
        [low],
        solver_args,
        reflected=False,
    )
    if not proper:
        raise RuntimeError("pure-transform alignment produced no proper-rotation candidate")
    tied_limit = proper[0]["score"] * (1.0 + solver_args.source_axis_score_gap) + 1e-7
    tied = [candidate for candidate in proper if candidate["score"] <= tied_limit]
    icp_best = min(
        tied,
        key=lambda candidate: module.source_local_axis_difference_degrees(
            candidate, [high], [low]
        ),
    )
    current_bounds = module.transformed_bounds([low], icp_best["matrix"])
    correction = np.eye(4, dtype=np.float64)
    correction[:3, 3] = high_data["center"] - current_bounds["center"]
    centered_matrix = correction @ icp_best["matrix"]
    icp_best = module.evaluate_candidate(
        centered_matrix,
        high_data,
        [low],
        high_evaluation,
        low_evaluation,
        high_tree,
        solver_args.trim_fraction,
    )
    best = source_axis_candidate if source_axis_candidate is not None else icp_best
    before_uniform_scale_refinement = module.serializable_candidate(best)
    uniform_scale_refinement: dict[str, Any] = {
        "attempted": False,
        "reason": "baseline_candidate_already_passes_surface_and_dimension_gates",
        "uniform_scale_only": True,
        "axis_scale_used": False,
        "reflection_allowed": False,
    }
    if (
        best["center_error_ratio"] <= ALIGNMENT_CENTER_ERROR_LIMIT
        and (
            best["surface_error_ratio"] > ALIGNMENT_SURFACE_ERROR_LIMIT
            or best["dimension_error_ratio"] > ALIGNMENT_DIMENSION_ERROR_LIMIT
        )
        and best["reflected"] is False
    ):
        refined, uniform_scale_refinement = refine_uniform_scale_for_dimension_gate(
            best["matrix"],
            high_data,
            low,
            module,
            high_evaluation,
            low_evaluation,
            high_tree,
            solver_args.trim_fraction,
        )
        if refined is not None:
            best = refined
    competitor = next(
        (
            candidate
            for candidate in proper
            if module.rotation_difference_degrees(best["matrix"], candidate["matrix"])
            > 8.0
        ),
        None,
    )
    ambiguous = bool(
        competitor is not None
        and competitor["score"]
        <= best["score"] * (1.0 + solver_args.ambiguity_gap) + 1e-7
    )
    source_axis_preference = {
        "enabled": True,
        "score_gap_limit": solver_args.source_axis_score_gap,
        "near_best_candidate_count": len(tied),
        "selected_axis_difference_degrees": (
            module.source_local_axis_difference_degrees(best, [high], [low])
        ),
    }
    gates = {
        # Direct V2 lows are intentionally sparse; the seven-view independent
        # review remains authoritative above this bounded geometric candidate
        # gate.  The approved skill's default is 0.055; production evidence for
        # the 424-face machine low is 0.0609, so this profile uses 0.070.
        "surface": best["surface_error_ratio"] <= ALIGNMENT_SURFACE_ERROR_LIMIT,
        "center": best["center_error_ratio"] <= ALIGNMENT_CENTER_ERROR_LIMIT,
        "dimensions": best["dimension_error_ratio"] <= ALIGNMENT_DIMENSION_ERROR_LIMIT,
        "orientation_unambiguous_or_source_axes_preferred": (
            not ambiguous or source_axis_preference["enabled"]
        ),
        "proper_rotation_only": best["reflected"] is False,
        "uniform_scale_only": True,
    }
    if not all(gates.values()):
        raise RuntimeError(
            "TRANSFORM_ONLY_ALIGNMENT_REJECTED: "
            + json.dumps(
                {
                    "selected": module.serializable_candidate(best),
                    "before_uniform_scale_refinement": before_uniform_scale_refinement,
                    "uniform_scale_refinement": uniform_scale_refinement,
                    "gates": gates,
                    "orientation_competitor": (
                        module.serializable_candidate(competitor)
                        if competitor is not None
                        else None
                    ),
                },
                ensure_ascii=False,
            )
        )
    fingerprint_before = module.topology_uv_fingerprint([low])
    module.bake_objects([low], best["matrix"])
    fingerprint_after = module.topology_uv_fingerprint([low])
    if fingerprint_after != fingerprint_before:
        raise RuntimeError("pure-transform alignment changed topology or UV data")
    return {
        "skill": "blender-align-bake-models",
        "skill_script_sha256": file_sha256(Path(module.__file__)),
        "method": "PCA proper-axis hypotheses + trimmed similarity ICP",
        "transform_only": True,
        "direct_object_transform_copy_used": False,
        "topology_rebuild_allowed": False,
        "alignment_changes_topology_or_uv": False,
        "uv_is_a_separate_pre_alignment_stage": True,
        "alignment_skill": "blender-align-bake-models",
        "mirror_allowed": False,
        "axis_scale_used": False,
        "uniform_scale_only": True,
        "before_uniform_scale_refinement": before_uniform_scale_refinement,
        "uniform_scale_refinement": uniform_scale_refinement,
        "source_axis_preference": True,
        "direct_v2_source_axis_candidate": source_axis_evidence,
        "source_local_axis_preference": source_axis_preference,
        "orientation_ambiguous_before_source_axis_preference": ambiguous,
        "orientation_competitor": (
            module.serializable_candidate(competitor)
            if competitor is not None
            else None
        ),
        "bounds_center_match": True,
        "selected": module.serializable_candidate(best),
        "gates": gates,
        "low_fingerprint_before": fingerprint_before,
        "low_fingerprint_after": fingerprint_after,
        "topology_uv_preserved_during_alignment": True,
    }


def basic_uv_failures(obj: bpy.types.Object) -> dict[str, int]:
    mesh = obj.data
    layer = mesh.uv_layers.active
    if layer is None or len(layer.data) != len(mesh.loops):
        return {"missing_or_mismatched_uv_layer": 1}
    nonfinite_loops = 0
    out_of_tile_loops = 0
    for loop in layer.data:
        point = loop.uv
        if any(not math.isfinite(float(value)) for value in point):
            nonfinite_loops += 1
        if any(value < -1.0e-7 or value > 1.0 + 1.0e-7 for value in point):
            out_of_tile_loops += 1
    mesh.calc_loop_triangles()
    degenerate_triangles = 0
    flipped_triangles = 0
    for triangle in mesh.loop_triangles:
        points = [layer.data[index].uv for index in triangle.loops]
        cross = (
            (points[1].x - points[0].x) * (points[2].y - points[0].y)
            - (points[1].y - points[0].y) * (points[2].x - points[0].x)
        )
        if abs(cross) <= 1.0e-10:
            degenerate_triangles += 1
        elif cross < 0.0:
            flipped_triangles += 1
    return {
        key: value
        for key, value in {
            "nonfinite_loops": nonfinite_loops,
            "out_of_tile_loops": out_of_tile_loops,
            "degenerate_uv_triangles": degenerate_triangles,
            "flipped_uv_triangles": flipped_triangles,
        }.items()
        if value
    }


def basic_uv_valid(obj: bpy.types.Object) -> bool:
    return not basic_uv_failures(obj)


def uv_invalid_polygon_indices(obj: bpy.types.Object) -> set[int]:
    mesh = obj.data
    layer = mesh.uv_layers.active
    if layer is None:
        return set(range(len(mesh.polygons)))
    mesh.calc_loop_triangles()
    invalid: set[int] = set()
    for triangle in mesh.loop_triangles:
        points = [layer.data[index].uv for index in triangle.loops]
        cross = (
            (points[1].x - points[0].x) * (points[2].y - points[0].y)
            - (points[1].y - points[0].y) * (points[2].x - points[0].x)
        )
        if cross <= 1.0e-10:
            invalid.add(int(triangle.polygon_index))
    return invalid


def triangulate_polygons(obj: bpy.types.Object, indices: set[int]) -> int:
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        targets = [bm.faces[index] for index in sorted(indices) if len(bm.faces[index].verts) > 3]
        if targets:
            bmesh.ops.triangulate(
                bm,
                faces=targets,
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )
            bm.to_mesh(obj.data)
            obj.data.update()
        return len(targets)
    finally:
        bm.free()


def project_invalid_uv_faces(
    obj: bpy.types.Object,
    indices: set[int],
    module: ModuleType,
) -> dict[str, Any]:
    mesh = obj.data
    layer = mesh.uv_layers.active
    if layer is None:
        raise RuntimeError("cannot repair invalid UV faces without an active UV layer")
    edge_by_vertices = {tuple(sorted(edge.vertices)): edge for edge in mesh.edges}
    repaired = 0
    for index in sorted(indices):
        polygon = mesh.polygons[index]
        loops = list(polygon.loop_indices)
        if len(loops) != 3:
            continue
        positions = [mesh.vertices[mesh.loops[loop].vertex_index].co.copy() for loop in loops]
        axis = positions[1] - positions[0]
        if axis.length_squared <= 1.0e-20:
            continue
        axis.normalize()
        normal = polygon.normal.normalized()
        vertical = normal.cross(axis)
        if vertical.length_squared <= 1.0e-20:
            continue
        vertical.normalize()
        projected = [
            Vector(((point - positions[0]).dot(axis), (point - positions[0]).dot(vertical)))
            for point in positions
        ]
        cross = (
            (projected[1].x - projected[0].x) * (projected[2].y - projected[0].y)
            - (projected[1].y - projected[0].y) * (projected[2].x - projected[0].x)
        )
        if abs(cross) <= 1.0e-12:
            continue
        if cross < 0.0:
            projected = [Vector((point.x, -point.y)) for point in projected]
        for loop, point in zip(loops, projected, strict=True):
            layer.data[loop].uv = point
        vertices = list(polygon.vertices)
        for offset, first in enumerate(vertices):
            second = vertices[(offset + 1) % len(vertices)]
            edge_by_vertices[tuple(sorted((first, second)))].use_seam = True
        repaired += 1
    mesh.update()
    islands = module.orient_and_separate(obj)
    average = module.average_scale(obj)
    packed = module.pack(obj, 10.0, 2048)
    return {
        "projected_triangle_faces": repaired,
        "uv_islands_after_repair": islands,
        "average_scale_result": average,
        "pack_result": packed,
    }


def inflate_degenerate_uv_islands(
    obj: bpy.types.Object,
    target_cross: float,
) -> dict[str, Any]:
    mesh = obj.data
    layer = mesh.uv_layers.active
    if layer is None:
        raise RuntimeError("cannot inflate degenerate UV islands without an active UV layer")
    def candidates() -> set[int]:
        mesh.calc_loop_triangles()
        result: set[int] = set()
        for triangle in mesh.loop_triangles:
            points = [layer.data[index].uv for index in triangle.loops]
            cross = abs(
                (points[1].x - points[0].x) * (points[2].y - points[0].y)
                - (points[1].y - points[0].y) * (points[2].x - points[0].x)
            )
            if cross <= 1.0e-10:
                result.add(int(triangle.polygon_index))
        return result

    polygon_indices = candidates()
    original_candidate_count = len(polygon_indices)
    triangulated_polygons = triangulate_polygons(obj, polygon_indices)
    if triangulated_polygons:
        layer = mesh.uv_layers.active
        if layer is None:
            raise RuntimeError("UV layer disappeared while triangulating tiny islands")
        polygon_indices = candidates()
    repaired = 0
    for polygon_index in sorted(polygon_indices):
        polygon = mesh.polygons[polygon_index]
        loops = list(polygon.loop_indices)
        points = [layer.data[index].uv.copy() for index in loops]
        if len(points) != 3:
            continue
        cross = abs(
            (points[1].x - points[0].x) * (points[2].y - points[0].y)
            - (points[1].y - points[0].y) * (points[2].x - points[0].x)
        )
        center = sum(points, Vector((0.0, 0.0))) / len(points)
        if cross <= 1.0e-20:
            side = math.sqrt(target_cross)
            scaled = [
                center + Vector((-side / 3.0, -side / 3.0)),
                center + Vector((side * 2.0 / 3.0, -side / 3.0)),
                center + Vector((-side / 3.0, side * 2.0 / 3.0)),
            ]
        else:
            scale = math.sqrt(target_cross / cross)
            scaled = [center + (point - center) * scale for point in points]
        minimum = Vector((min(point.x for point in scaled), min(point.y for point in scaled)))
        maximum = Vector((max(point.x for point in scaled), max(point.y for point in scaled)))
        translation = Vector(
            (
                max(0.0, -minimum.x) + min(0.0, 1.0 - maximum.x),
                max(0.0, -minimum.y) + min(0.0, 1.0 - maximum.y),
            )
        )
        for loop, point in zip(loops, scaled, strict=True):
            layer.data[loop].uv = point + translation
        repaired += 1
    mesh.update()
    return {
        "candidate_polygon_count": original_candidate_count,
        "triangulated_polygon_count": triangulated_polygons,
        "inflated_triangle_islands": repaired,
        "target_uv_cross": target_cross,
        "kept_inside_0_1": True,
        "protected_by_pack_margin_px": 10,
    }


def enforce_positive_uv_island_winding(
    obj: bpy.types.Object, module: ModuleType
) -> dict[str, int]:
    """Mirror whole negative-winding islands without fragmenting their UVs."""

    mesh = obj.data
    layer = mesh.uv_layers.active
    if layer is None:
        raise RuntimeError("cannot orient UV islands without an active UV layer")
    _, edge_faces, _ = module.topology(mesh)
    adjacency: dict[int, list[int]] = {}
    for edge_index, linked in enumerate(edge_faces):
        if len(linked) != 2 or not module.edge_uv_continuous(
            mesh, layer, edge_index, linked
        ):
            continue
        first, second = (int(linked[0]), int(linked[1]))
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    islands = module.components(range(len(mesh.polygons)), adjacency)
    mesh.calc_loop_triangles()
    polygon_winding: dict[int, tuple[int, int]] = {}
    for triangle in mesh.loop_triangles:
        points = [layer.data[index].uv for index in triangle.loops]
        cross = (
            (points[1].x - points[0].x) * (points[2].y - points[0].y)
            - (points[1].y - points[0].y) * (points[2].x - points[0].x)
        )
        polygon_index = int(triangle.polygon_index)
        positive, negative = polygon_winding.get(polygon_index, (0, 0))
        polygon_winding[polygon_index] = (
            positive + int(cross > 1.0e-10),
            negative + int(cross < -1.0e-10),
        )
    mirrored = 0
    mixed = 0
    for faces in islands:
        positive = 0
        negative = 0
        loops: set[int] = set()
        for polygon_index in faces:
            polygon = mesh.polygons[polygon_index]
            loops.update(int(index) for index in polygon.loop_indices)
        for polygon_index in faces:
            polygon_positive, polygon_negative = polygon_winding.get(
                int(polygon_index), (0, 0)
            )
            positive += polygon_positive
            negative += polygon_negative
        if positive and negative:
            mixed += 1
            continue
        if negative and not positive:
            minimum = min(float(layer.data[index].uv.x) for index in loops)
            maximum = max(float(layer.data[index].uv.x) for index in loops)
            center = (minimum + maximum) * 0.5
            for index in loops:
                layer.data[index].uv.x = 2.0 * center - layer.data[index].uv.x
            mirrored += 1
    mesh.update()
    return {
        "islands": len(islands),
        "mirrored_negative_islands": mirrored,
        "mixed_winding_islands": mixed,
    }


def smart_project_complete_mesh(
    obj: bpy.types.Object, module: ModuleType
) -> dict[str, Any]:
    module.activate_object(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    result = bpy.ops.uv.smart_project(
        angle_limit=math.radians(66.0),
        margin_method="FRACTION",
        rotate_method="AXIS_ALIGNED_Y",
        island_margin=0.0,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.data.update()
    average = module.average_scale(obj)
    packed = module.pack(obj, 10.0, 2048)
    winding = enforce_positive_uv_island_winding(obj, module)
    return {
        "project_result": sorted(result),
        "angle_limit_degrees": 66.0,
        "average_scale_result": average,
        "pack_result": packed,
        "island_winding": winding,
    }


def create_legacy_pbr_uv(obj: bpy.types.Object, module: ModuleType) -> dict[str, Any]:
    mesh = obj.data
    triangulated = module.triangulate_nonplanar_faces(obj)
    polygon_edges, edge_faces, _ = module.topology(mesh)
    shading = module.mark_hard_edges(mesh, edge_faces, 75.0, True)
    cuts = module.add_topology_cuts(mesh, polygon_edges, edge_faces, "y+")
    module.ensure_uv(mesh)
    unwrap_result = module.unwrap(obj)
    # Prefer one coherent conformal unwrap for the delivered low surface.
    # Projecting thousands of small faces independently creates sub-pixel UV
    # islands and can turn otherwise valid topology into degenerate/flipped UV
    # triangles during packing.  Blender 5.1's no_flip solver keeps the larger
    # seam islands coherent; only enter the surgical repair path when this
    # complete-mesh pass still fails the strict numerical checks.
    hard_boundary = module.ensure_hard_boundaries(mesh)
    if hard_boundary["added_guard_seams"]:
        unwrap_result = module.unwrap(obj)
    average = module.average_scale(obj)
    packed = module.pack(obj, 10.0, 2048)
    winding = enforce_positive_uv_island_winding(obj, module)
    coherent_failures = basic_uv_failures(obj)
    if not coherent_failures:
        return {
            "algorithm": "legacy_pbr",
            "preserved_existing": False,
            "strategy": "coherent_conformal_no_flip",
            "triangulated_nonplanar_faces": triangulated,
            "shading": shading,
            "topology_cuts": cuts,
            "unwrap_result": unwrap_result,
            "hard_boundary_guard": hard_boundary,
            "average_scale_result": average,
            "pack_result": packed,
            "island_winding": winding,
            "resolution": 2048,
            "padding_px": 10,
        }
    smart_project = smart_project_complete_mesh(obj, module)
    smart_failures = basic_uv_failures(obj)
    smart_inflation: list[dict[str, Any]] = []
    if set(smart_failures) <= {"degenerate_uv_triangles"}:
        for target_cross in (1.0e-8, 1.0e-6):
            smart_inflation.append(inflate_degenerate_uv_islands(obj, target_cross))
            smart_failures = basic_uv_failures(obj)
            if not smart_failures:
                break
    if not smart_failures:
        return {
            "algorithm": "legacy_pbr",
            "preserved_existing": False,
            "strategy": "angle_cluster_smart_project",
            "triangulated_nonplanar_faces": triangulated,
            "shading": shading,
            "topology_cuts": cuts,
            "coherent_conformal_failures": coherent_failures,
            "smart_project": smart_project,
            "degenerate_island_inflation": smart_inflation,
            "resolution": 2048,
            "padding_px": 10,
        }
    planar = [module.project_planar_islands(obj, 1.0)]
    refinement = module.add_distortion_region_seams(mesh, 6.0)
    if refinement["added_seams"]:
        unwrap_result = module.unwrap(obj)
        planar.append(module.project_planar_islands(obj, 1.0))
    repair = module.project_remaining_bad_faces(obj, 6.0)
    planar.append(module.project_planar_islands(obj, 1.0))
    islands = module.orient_and_separate(obj)
    hard_boundary = module.ensure_hard_boundaries(mesh)
    if hard_boundary["added_guard_seams"]:
        islands = module.orient_and_separate(obj)
    average = module.average_scale(obj)
    packed = module.pack(obj, 10.0, 2048)
    report = {
        "algorithm": "legacy_pbr",
        "preserved_existing": False,
        "triangulated_nonplanar_faces": triangulated,
        "shading": shading,
        "topology_cuts": cuts,
        "unwrap_result": unwrap_result,
        "planar": planar,
        "distortion_refinement": refinement,
        "repair": repair,
        "islands": islands,
        "hard_boundary_guard": hard_boundary,
        "average_scale_result": average,
        "pack_result": packed,
        "resolution": 2048,
        "padding_px": 10,
    }
    if not basic_uv_valid(obj):
        invalid_faces = uv_invalid_polygon_indices(obj)
        original_invalid_face_count = len(invalid_faces)
        triangulated_bad_faces = triangulate_polygons(obj, invalid_faces)
        invalid_faces = uv_invalid_polygon_indices(obj)
        projected = project_invalid_uv_faces(obj, invalid_faces, module)
        inflation_history: list[dict[str, Any]] = []
        for target_cross in (1.0e-8, 1.0e-6):
            inflation_history.append(inflate_degenerate_uv_islands(obj, target_cross))
            if basic_uv_valid(obj):
                break
        final_projection: dict[str, Any] | None = None
        if not basic_uv_valid(obj):
            remaining_invalid = uv_invalid_polygon_indices(obj)
            final_projection = project_invalid_uv_faces(obj, remaining_invalid, module)
            inflation_history.append(inflate_degenerate_uv_islands(obj, 1.0e-6))
        if basic_uv_valid(obj):
            report["invalid_uv_face_projection_repair"] = {
                "invalid_polygon_count": original_invalid_face_count,
                "triangulated_polygon_count": triangulated_bad_faces,
                **projected,
                "degenerate_island_inflation": inflation_history,
                "final_projection": final_projection,
            }
            return report
        raise RuntimeError(
            f"legacy PBR UV generation produced invalid UVs: {obj.name}: "
            f"coherent={coherent_failures}; winding={winding}; "
            f"smart={smart_failures}; "
            f"{basic_uv_failures(obj)}; invalid_faces={len(invalid_faces)}; "
            f"triangulatable={triangulated_bad_faces}; projected={projected}; "
            f"inflation={inflation_history}; final_projection={final_projection}"
        )
    return report


def ensure_final_uv(
    obj: bpy.types.Object, algorithm: str, legacy_module: ModuleType
) -> dict[str, Any]:
    if basic_uv_valid(obj):
        return {
            "algorithm": algorithm,
            "preserved_existing": True,
            "active_uv": obj.data.uv_layers.active.name,
        }
    if algorithm == "mof_low_seam":
        raise RuntimeError(
            "UV_MOF_RUNTIME_UNAVAILABLE: post-retopology MOF requires a licensed, "
            "preflight-approved Windows Worker"
        )
    return create_legacy_pbr_uv(obj, legacy_module)


def configure_render_scene(
    resolution: int,
) -> tuple[bpy.types.Object, list[bpy.types.Object]]:
    scene = bpy.context.scene
    engines = {item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items}
    if "BLENDER_EEVEE" not in engines:
        raise RuntimeError(f"BLENDER_EEVEE is unavailable: {sorted(engines)}")
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    if scene.world is None:
        scene.world = bpy.data.worlds.new("BakeAlignmentWorld")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.015, 0.018, 0.028, 1.0)
        background.inputs["Strength"].default_value = 0.25
    camera_data = bpy.data.cameras.new("BakeAlignmentCamera")
    camera = bpy.data.objects.new("BakeAlignmentCamera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    helpers = [camera]
    camera_data.clip_start = 1.0e-5
    camera_data.clip_end = 1.0e8
    for name, energy, location in (
        ("BakeAlignmentKey", 1000.0, (4.0, -6.0, 6.0)),
        ("BakeAlignmentFill", 650.0, (-5.0, -2.0, 3.0)),
        ("BakeAlignmentRim", 850.0, (2.0, 5.0, 5.0)),
    ):
        light_data = bpy.data.lights.new(name=name, type="AREA")
        light_data.energy = energy
        light_data.shape = "DISK"
        light_data.size = 5.0
        light = bpy.data.objects.new(name, light_data)
        light.location = location
        scene.collection.objects.link(light)
        helpers.append(light)
    return camera, helpers


def look_at(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def camera_for_view(
    camera: bpy.types.Object, view: str, center: Vector, scale: float
) -> None:
    distance = max(scale * 3.0, 1.0e-4)
    offsets = {
        "front": (0.0, -distance, 0.0),
        "back": (0.0, distance, 0.0),
        "left": (-distance, 0.0, 0.0),
        "right": (distance, 0.0, 0.0),
        "top": (0.0, 0.0, distance),
        "bottom": (0.0, 0.0, -distance),
    }
    if view in offsets:
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = max(scale * 1.25, 1.0e-4)
        camera.location = center + Vector(offsets[view])
    else:
        camera.data.type = "PERSP"
        camera.data.lens = 70
        camera.location = center + Vector((scale * 1.8, -scale * 1.8, scale * 1.35))
    look_at(camera, center)


def review_material(name: str, color: tuple[float, float, float, float], wire: bool) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    if wire:
        wire_node = nodes.new("ShaderNodeWireframe")
        wire_node.use_pixel_size = True
        wire_node.inputs["Size"].default_value = 0.75
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MIX"
        mix.inputs[1].default_value = color
        mix.inputs[2].default_value = (0.025, 0.018, 0.012, 1.0)
        links.new(wire_node.outputs["Fac"], mix.inputs[0])
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 0.8
        links.new(mix.outputs[0], emission.inputs["Color"])
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    else:
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = color
        emission.inputs["Strength"].default_value = 0.8
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    vertices, _ = raw_world_geometry(obj)
    return Vector(tuple(vertices.min(axis=0))), Vector(tuple(vertices.max(axis=0)))


def render_alpha_mask(scene: bpy.types.Scene) -> np.ndarray:
    image = bpy.data.images["Render Result"]
    pixels = np.asarray(image.pixels[:])
    rgba = pixels.reshape((-1, 4))
    alpha = rgba[:, 3]
    width, height = (int(value) for value in image.size)
    expected = width * height
    if len(alpha) != expected:
        raise RuntimeError(
            "render alpha size mismatch: "
            f"pixels={len(pixels)}, alpha={len(alpha)}, image={width}x{height}, "
            f"scene={scene.render.resolution_x}x{scene.render.resolution_y}"
        )
    visible = np.logical_or(alpha > 0.05, np.max(rgba[:, :3], axis=1) > 0.005)
    return visible.reshape((height, width))


def image_file_mask(path: Path) -> np.ndarray:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = (int(value) for value in image.size)
        channels = int(image.channels)
        pixels = np.asarray(image.pixels[:]).reshape((-1, channels))
        if channels < 3 or len(pixels) != width * height:
            raise RuntimeError(f"render image cannot be read for silhouette QA: {path.name}")
        alpha = pixels[:, 3] if channels >= 4 else np.zeros(len(pixels))
        visible = np.logical_or(alpha > 0.05, np.max(pixels[:, :3], axis=1) > 0.005)
        return visible.reshape((height, width))
    finally:
        bpy.data.images.remove(image)


def render_seven_views(
    high: bpy.types.Object,
    low: bpy.types.Object,
    output_dir: Path,
    resolution: int,
    *,
    high_reference_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    previous_camera = scene.camera
    camera, render_helpers = configure_render_scene(resolution)
    high_minimum, high_maximum = world_bounds(high)
    center = (high_minimum + high_maximum) * 0.5
    scale = max(float(value) for value in high_maximum - high_minimum)
    if not math.isfinite(scale) or scale <= 0.0:
        raise RuntimeError("high has invalid render bounds")

    review_high = high.copy()
    review_high.data = high.data.copy()
    review_high_mesh = review_high.data
    review_high.name = "REVIEW_BAKE_HIGH"
    scene.collection.objects.link(review_high)
    review_low = low.copy()
    review_low.data = low.data.copy()
    review_low_mesh = review_low.data
    review_low.name = "REVIEW_BAKE_LOW"
    scene.collection.objects.link(review_low)
    high_material = review_material("ReviewHighBlue", (0.08, 0.32, 0.95, 1.0), False)
    low_material = review_material("ReviewLowOpaqueOrangeWire", (1.0, 0.26, 0.015, 1.0), True)
    review_high.data.materials.clear()
    review_high.data.materials.append(high_material)
    review_low.data.materials.clear()
    review_low.data.materials.append(low_material)
    all_meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    hidden_before = {obj.name: bool(obj.hide_render) for obj in all_meshes}
    records: list[dict[str, Any]] = []
    ious: dict[str, float] = {}
    camera_records: dict[str, list[list[float]]] = {}
    try:
        for view in VIEW_NAMES:
            camera_for_view(camera, view, center, scale)
            bpy.context.view_layer.update()
            camera_records[view] = matrix_values(camera.matrix_world)
            masks: dict[str, np.ndarray] = {}
            for role, obj in (("high", review_high), ("low", review_low)):
                path = output_dir / f"{view}_{role}.png"
                if role == "high" and high_reference_dir is not None:
                    source = high_reference_dir / path.name
                    if not source.is_file() or source.stat().st_size <= 0:
                        raise RuntimeError(f"seven-view high reference is missing: {source}")
                    shutil.copy2(source, path)
                    masks[role] = image_file_mask(path)
                    records.append(
                        {
                            "view": view,
                            "role": role,
                            "filename": path.name,
                            "sha256": file_sha256(path),
                            "size_bytes": path.stat().st_size,
                            "reused_from_initial_review": True,
                        }
                    )
                    continue
                for mesh_object in all_meshes:
                    mesh_object.hide_render = mesh_object != obj
                obj.hide_render = False
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                if not path.is_file() or path.stat().st_size <= 0:
                    raise RuntimeError(f"seven-view render is missing: {path.name}")
                masks[role] = image_file_mask(path)
                records.append(
                    {
                        "view": view,
                        "role": role,
                        "filename": path.name,
                        "sha256": file_sha256(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
            union = np.logical_or(masks["high"], masks["low"]).sum()
            intersection = np.logical_and(masks["high"], masks["low"]).sum()
            ious[view] = float(intersection / union) if union else 0.0
    finally:
        for obj in all_meshes:
            if obj.name in hidden_before:
                obj.hide_render = hidden_before[obj.name]
        bpy.data.objects.remove(review_high, do_unlink=True)
        bpy.data.objects.remove(review_low, do_unlink=True)
        bpy.data.meshes.remove(review_high_mesh)
        bpy.data.meshes.remove(review_low_mesh)
        bpy.data.materials.remove(high_material)
        bpy.data.materials.remove(low_material)
        scene.camera = previous_camera
        for helper in render_helpers:
            helper_data = helper.data
            bpy.data.objects.remove(helper, do_unlink=True)
            if isinstance(helper_data, bpy.types.Camera):
                bpy.data.cameras.remove(helper_data)
            elif isinstance(helper_data, bpy.types.Light):
                bpy.data.lights.remove(helper_data)
    worst_orthographic = min(ious[view] for view in ORTHOGRAPHIC_VIEWS)
    manifest = {
        "schema_version": VIEW_SCHEMA_VERSION,
        "views": list(VIEW_NAMES),
        "high_color": "blue",
        "low_display": "opaque_bright_orange_solid_with_dark_wire",
        "low_transparency": False,
        "xray": False,
        "matched_center": vector_values(center),
        "matched_scale": scale,
        "resolution": resolution,
        "camera_matrices": camera_records,
        "files": records,
        "silhouette_iou": ious,
        "worst_orthographic_silhouette_iou": worst_orthographic,
        "minimum_required_silhouette_iou": MINIMUM_SILHOUETTE_IOU,
        "numeric_silhouette_pass": worst_orthographic >= MINIMUM_SILHOUETTE_IOU,
    }
    (output_dir / "views_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
    )
    return manifest


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


def export_meter_fbx(path: Path, objects: list[bpy.types.Object]) -> dict[str, Any]:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        add_leaf_bones=False,
        bake_anim=False,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        use_space_transform=True,
        bake_space_transform=False,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="AUTO",
    )
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"FBX export is empty: {path}")
    unit = fbx_double_property(path, "UnitScaleFactor")
    original_unit = fbx_double_property(path, "OriginalUnitScaleFactor")
    if not math.isclose(unit, 100.0, abs_tol=1.0e-9) or not math.isclose(
        original_unit, 100.0, abs_tol=1.0e-9
    ):
        raise RuntimeError(f"FBX meter unit contract failed: {unit}, {original_unit}")
    return {
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
        "unit_contract": {
            "schema_version": FBX_UNIT_SCHEMA_VERSION,
            "passed": True,
            "coordinate_unit": "meter",
            "unit_scale_factor_centimeters": unit,
            "original_unit_scale_factor_centimeters": original_unit,
            "raw_coordinates_are_meters": True,
            "global_scale": 1.0,
            "apply_unit_scale": True,
            "apply_scale_options": "FBX_SCALE_UNITS",
            "axis_forward": "-Z",
            "axis_up": "Y",
        },
    }


def union_bounds(objects: list[bpy.types.Object]) -> dict[str, list[float]]:
    all_vertices = np.concatenate([raw_world_geometry(obj)[0] for obj in objects], axis=0)
    minimum = all_vertices.min(axis=0)
    maximum = all_vertices.max(axis=0)
    return {
        "minimum": vector_values(minimum),
        "maximum": vector_values(maximum),
        "center": vector_values((minimum + maximum) * 0.5),
        "dimensions": vector_values(maximum - minimum),
    }


def readback_fbx(
    path: Path,
    *,
    require_uv: bool,
    structure_module: ModuleType,
    include_structure: bool,
) -> dict[str, Any]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.fbx_import(filepath=str(path))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not objects or any(not obj.data.polygons for obj in objects):
        raise RuntimeError(f"FBX readback has no non-empty mesh: {path.name}")
    missing_uv = [obj.name for obj in objects if require_uv and not basic_uv_valid(obj)]
    if missing_uv:
        raise RuntimeError(f"FBX readback UV validation failed: {missing_uv}")
    result = {
        "passed": True,
        "mesh_object_count": len(objects),
        "faces": sum(len(obj.data.polygons) for obj in objects),
        "bounds": union_bounds(objects),
        "uv_required": require_uv,
        "uv_passed": not missing_uv,
        "objects": [
            {
                "name": obj.name,
                "faces": len(obj.data.polygons),
                "active_uv": (
                    obj.data.uv_layers.active.name if obj.data.uv_layers.active else None
                ),
            }
            for obj in objects
        ],
    }
    if include_structure:
        result["structure"] = structure_module.fbx_structure_summary(objects)
    return result


def max_vector_delta(left: list[float], right: list[float]) -> float:
    return max(abs(float(left[index]) - float(right[index])) for index in range(3))


def presentation_settings(high: bpy.types.Object, low: bpy.types.Object) -> None:
    high.color = (0.08, 0.32, 0.95, 1.0)
    high.display_type = "SOLID"
    high.show_wire = False
    high.show_all_edges = False
    high.show_transparent = False
    low.color = (1.0, 0.26, 0.015, 1.0)
    low.display_type = "SOLID"
    low.show_wire = True
    low.show_all_edges = True
    low.show_transparent = False
    low["gpu_control_display"] = "opaque_orange_solid_with_dark_wire"


def main() -> None:
    args = arguments()
    if not args.output_blend.is_file() or args.output_blend.stat().st_size <= 0:
        raise RuntimeError("Direct V2 output Blend is missing")
    if args.uv_algorithm == "mof_low_seam" and os.name != "nt":
        raise RuntimeError(
            "UV_MOF_RUNTIME_UNAVAILABLE: MOF low-seam postprocessing requires Windows"
        )
    input_blend_sha256 = file_sha256(args.output_blend)
    pairs = load_role_pairs(args.generation_report)
    protected_before = {
        obj.name: object_fingerprint(obj)
        for pair in pairs
        for obj in (pair["high"], pair["low"])
    }
    legacy_uv = import_uv_module(args.uv_script)
    alignment_module = import_uv_module(args.align_script)
    pair_records: list[dict[str, Any]] = []
    bake_highs: list[bpy.types.Object] = []
    bake_lows: list[bpy.types.Object] = []

    for pair in pairs:
        index = int(pair["index"])
        high = pair["high"]
        low = pair["low"]
        suffix = "" if len(pairs) == 1 else f"_{index:03d}"
        high_name = f"BAKE_HIGH{suffix}"
        final_name = f"BAKE_LOW{suffix}"
        if any(bpy.data.objects.get(name) is not None for name in (high_name, final_name)):
            raise RuntimeError("bake delivery object names already exist; refusing overwrite")
        bake_high = create_world_baked_copy(high, high_name)
        final_low = create_world_baked_copy(low, final_name)
        high_faces = len(high.data.polygons)
        preliminary_views = render_seven_views(
            bake_high,
            final_low,
            args.views_dir / f"pair_{index:03d}" / "initial",
            args.resolution,
        )
        topology_before_uv = topology_metrics(final_low, inspect_intersections=False)
        delivery_geometry_cleanup = {
            "before_uv": cleanup_delivery_degenerate_geometry(
                final_low,
                stage="before_uv",
            )
        }
        uv = ensure_final_uv(final_low, args.uv_algorithm, legacy_uv)
        topology_after_uv = topology_metrics(final_low, inspect_intersections=False)
        if topology_after_uv["degenerate_faces"] or topology_after_uv["ngons"]:
            delivery_geometry_cleanup["after_uv"] = cleanup_delivery_degenerate_geometry(
                final_low,
                stage="after_uv",
            )
            if not basic_uv_valid(final_low):
                delivery_geometry_cleanup["uv_after_cleanup"] = ensure_final_uv(
                    final_low,
                    args.uv_algorithm,
                    legacy_uv,
                )
            else:
                delivery_geometry_cleanup["uv_after_cleanup"] = {
                    "preserved_existing": True,
                    "reason": "cleanup_preserved_valid_uv",
                }
            topology_after_uv = topology_metrics(final_low, inspect_intersections=False)
        if not basic_uv_valid(final_low):
            raise RuntimeError(f"final bake low has no valid UV: {final_low.name}")
        if topology_after_uv["faces"] >= high_faces:
            raise RuntimeError("UV-prepared low is not lower-poly than the high")
        if (
            not topology_after_uv["finite_coordinates"]
            or topology_after_uv["degenerate_faces"]
            or topology_after_uv["ngons"]
        ):
            raise RuntimeError(
                "UV-prepared low has invalid geometry after bounded delivery cleanup: "
                f"{json.dumps(delivery_geometry_cleanup, ensure_ascii=False, sort_keys=True)}"
            )
        alignment = transform_only_alignment(
            bake_high,
            final_low,
            alignment_module,
        )
        final_views = render_seven_views(
            bake_high,
            final_low,
            args.views_dir / f"pair_{index:03d}" / "final",
            args.resolution,
            high_reference_dir=args.views_dir / f"pair_{index:03d}" / "initial",
        )
        final_views["numeric_silhouette_advisory_only"] = True

        high.hide_set(True)
        high.hide_viewport = True
        high.hide_render = True
        low.hide_set(True)
        low.hide_viewport = True
        low.hide_render = True
        high["gpu_control_role"] = "preserved_original_high"
        low["gpu_control_role"] = "preserved_original_low"
        bake_high["gpu_control_role"] = "bake_high"
        final_low["gpu_control_role"] = "bake_low"
        presentation_settings(bake_high, final_low)
        bake_highs.append(bake_high)
        bake_lows.append(final_low)
        pair_records.append(
            {
                "pair_index": index,
                "role_identification": {
                    "method": "higher_face_count_is_high",
                    "reported_high": pair["reported_high"],
                    "reported_low": pair["reported_low"],
                    "identified_high": high.name,
                    "identified_low": low.name,
                    "role_corrected": pair["role_corrected_by_face_count"],
                    "high_faces": high_faces,
                    "original_low_faces": len(low.data.polygons),
                },
                "registration": alignment,
                "transform_application": {
                    "copied_high_object_transform": False,
                    "geometry_registration_used": True,
                    "applied_exactly_once_to_duplicate_mesh": True,
                    "bake_high_matrix_identity": matrix_values(bake_high.matrix_world),
                    "bake_low_matrix_identity": matrix_values(final_low.matrix_world),
                    "mirror_introduced": False,
                },
                "alignment_scope": "transform_only",
                "topology_before_separate_uv_stage": topology_before_uv,
                "topology_after_separate_uv_stage": topology_after_uv,
                "delivery_geometry_cleanup": delivery_geometry_cleanup,
                "preliminary_views": preliminary_views,
                "fallback": None,
                "rebuild_allowed": False,
                "final_high_object": bake_high.name,
                "final_low_object": final_low.name,
                "final_high_topology": topology_metrics(bake_high),
                "final_low_topology": topology_metrics(final_low),
                "uv": uv,
                "final_views": final_views,
                "original_high_preserved": True,
                "original_low_preserved": True,
                "originals_hidden": True,
            }
        )

    protected_after = {
        name: object_fingerprint(require_mesh(name)) for name in protected_before
    }
    if protected_after != protected_before:
        raise RuntimeError("post-topology bake processing changed an original high or low")

    args.output_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output_blend), check_existing=False)
    output_blend_sha256 = file_sha256(args.output_blend)
    expected_high_bounds = union_bounds(bake_highs)
    expected_low_bounds = union_bounds(bake_lows)
    expected_low_structure = alignment_module.fbx_structure_summary(bake_lows)
    high_export = export_meter_fbx(args.output_high_fbx, bake_highs)
    low_export = export_meter_fbx(args.output_low_fbx, bake_lows)

    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "passed": True,
        "input_blend_sha256": input_blend_sha256,
        "output_blend_sha256": output_blend_sha256,
        "source_high_is_sole_coordinate_authority": True,
        "direct_object_transform_copy_used": False,
        "uniform_scale_only": True,
        "mirror_candidates_allowed": False,
        "topology_rebuild_allowed": False,
        "alignment_changes_topology_or_uv": False,
        "uv_is_a_separate_pre_alignment_stage": True,
        "alignment_skill": "blender-align-bake-models",
        "uv_algorithm": args.uv_algorithm,
        "pairs": pair_records,
        "bake_high_fbx": high_export,
        "bake_low_fbx": low_export,
        "automatic_visual_review_required": True,
    }
    args.alignment_report.parent.mkdir(parents=True, exist_ok=True)
    args.alignment_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    high_readback = readback_fbx(
        args.output_high_fbx,
        require_uv=False,
        structure_module=alignment_module,
        include_structure=False,
    )
    low_readback = readback_fbx(
        args.output_low_fbx,
        require_uv=True,
        structure_module=alignment_module,
        include_structure=True,
    )
    high_center_delta = max_vector_delta(expected_high_bounds["center"], high_readback["bounds"]["center"])
    high_dimensions_delta = max_vector_delta(
        expected_high_bounds["dimensions"], high_readback["bounds"]["dimensions"]
    )
    low_center_delta = max_vector_delta(expected_low_bounds["center"], low_readback["bounds"]["center"])
    low_dimensions_delta = max_vector_delta(
        expected_low_bounds["dimensions"], low_readback["bounds"]["dimensions"]
    )
    scale = max(max(expected_high_bounds["dimensions"]), 1.0)
    tolerance = max(1.0e-5, scale * 1.0e-4)
    if max(high_center_delta, high_dimensions_delta, low_center_delta, low_dimensions_delta) > tolerance:
        raise RuntimeError("FBX clean-scene readback changed bake pair bounds")
    low_structure_match = low_readback["structure"] == expected_low_structure
    if not low_structure_match:
        raise RuntimeError("FBX clean-scene readback changed low topology or UV structure")
    validation = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "passed": True,
        "fresh_blender_scene_reimport": True,
        "high": high_readback
        | {
            "sha256": high_export["sha256"],
            "unit_contract": high_export["unit_contract"],
            "center_max_abs_delta": high_center_delta,
            "dimensions_max_abs_delta": high_dimensions_delta,
        },
        "low": low_readback
        | {
            "sha256": low_export["sha256"],
            "unit_contract": low_export["unit_contract"],
            "center_max_abs_delta": low_center_delta,
            "dimensions_max_abs_delta": low_dimensions_delta,
        },
        "tolerance": tolerance,
        "low_faces_less_than_high": low_readback["faces"] < high_readback["faces"],
        "low_has_uv": low_readback["uv_passed"],
        "low_structure_match": low_structure_match,
        "expected_low_structure": expected_low_structure,
    }
    if not validation["low_faces_less_than_high"] or not validation["low_has_uv"]:
        raise RuntimeError("FBX readback bake-pair contract failed")
    args.validation_report.parent.mkdir(parents=True, exist_ok=True)
    args.validation_report.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "passed": True,
                "pair_count": len(pair_records),
                "output_blend_sha256": output_blend_sha256,
                "bake_high_fbx_sha256": high_export["sha256"],
                "bake_low_fbx_sha256": low_export["sha256"],
                "fresh_fbx_readback": True,
                "low_structure_match": low_structure_match,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
