"""Align a low-poly mesh to a high-poly mesh and export bake-ready FBX copies.

Run inside Blender 4.2+. The high model is the coordinate authority. The solver
uses PCA orientation hypotheses followed by trimmed similarity ICP. It refuses
ambiguous, mirrored, or poor fits unless the relevant override was explicit.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector
from mathutils.kdtree import KDTree


IDENTITY4 = np.eye(4, dtype=np.float64)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--high", required=True)
    parser.add_argument("--low", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report")
    parser.add_argument("--samples", type=int, default=1800)
    parser.add_argument("--target-points", type=int, default=30000)
    parser.add_argument("--coarse-iterations", type=int, default=7)
    parser.add_argument("--fine-iterations", type=int, default=24)
    parser.add_argument("--trim-fraction", type=float, default=0.82)
    parser.add_argument("--final-candidates", type=int, default=4)
    parser.add_argument("--max-surface-error-ratio", type=float, default=0.055)
    parser.add_argument("--max-center-error-ratio", type=float, default=0.02)
    parser.add_argument("--max-dimension-error-ratio", type=float, default=0.10)
    parser.add_argument("--ambiguity-gap", type=float, default=0.025)
    parser.add_argument("--rigid-only", action="store_true")
    parser.add_argument(
        "--allow-axis-scale",
        action="store_true",
        help=(
            "After orientation is solved, match the low bounds to the high with "
            "controlled XYZ world-axis scaling. Use only when uniform scaling leaves "
            "a small, user-confirmed proportion mismatch."
        ),
    )
    parser.add_argument(
        "--max-axis-scale-delta",
        type=float,
        default=0.10,
        help="Maximum absolute per-axis scale change allowed after similarity alignment.",
    )
    parser.add_argument("--allow-mirror", action="store_true")
    parser.add_argument("--allow-ambiguous", action="store_true")
    parser.add_argument("--prefer-current-orientation", action="store_true")
    parser.add_argument(
        "--prefer-source-local-axes",
        action="store_true",
        help=(
            "For one-high/one-low pairs whose local axes are semantically equivalent, "
            "prefer a near-best orientation that restores the low local axes to the high."
        ),
    )
    parser.add_argument(
        "--source-axis-score-gap",
        type=float,
        default=0.10,
        help="Maximum score gap for local-axis orientation preference.",
    )
    parser.add_argument(
        "--match-bounds-center",
        action="store_true",
        help="Translate the selected low so its world AABB center exactly matches the high.",
    )
    parser.add_argument("--require-low-uv", action="store_true")
    parser.add_argument("--straighten-high", action="store_true")
    parser.add_argument(
        "--manual-high-rotation",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="Known current high tilt in XYZ degrees; apply its inverse to both copies.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_model(path_text: str, role: str) -> list[bpy.types.Object]:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False)
    elif suffix == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif suffix in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError(f"Unsupported format {suffix}; use FBX, OBJ, GLB, or GLTF.")

    objects = [
        obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"
    ]
    if not objects:
        raise RuntimeError(f"No mesh objects found in {path}.")
    for index, obj in enumerate(sorted(objects, key=lambda item: item.name)):
        obj.name = f"ALIGN_{role.upper()}_{index:03d}"
    return objects


def np_matrix(matrix: Matrix) -> np.ndarray:
    return np.array(matrix, dtype=np.float64)


def json_matrix(matrix: np.ndarray) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def object_world_vertices(obj: bpy.types.Object) -> np.ndarray:
    if not obj.data.vertices:
        return np.empty((0, 3), dtype=np.float64)
    local = np.empty((len(obj.data.vertices), 3), dtype=np.float64)
    obj.data.vertices.foreach_get("co", local.ravel())
    return transform_points(local, np_matrix(obj.matrix_world))


def collect_points(
    objects: Iterable[bpy.types.Object],
    max_points: int,
    seed: int,
) -> dict[str, Any]:
    objects = list(objects)
    total = sum(len(obj.data.vertices) for obj in objects)
    if total == 0:
        raise RuntimeError("Mesh collection contains no vertices.")

    rng = np.random.default_rng(seed)
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    entries: list[dict[str, Any]] = []

    for obj in objects:
        world = object_world_vertices(obj)
        if len(world) == 0:
            continue
        minimum = np.minimum(minimum, world.min(axis=0))
        maximum = np.maximum(maximum, world.max(axis=0))
        obj.data.calc_loop_triangles()
        triangle_indices = np.array(
            [triangle.vertices for triangle in obj.data.loop_triangles],
            dtype=np.int64,
        )
        if len(triangle_indices):
            triangles = world[triangle_indices]
            areas = 0.5 * np.linalg.norm(
                np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
                axis=1,
            )
            valid = areas > 1e-18
            triangles = triangles[valid]
            areas = areas[valid]
        else:
            triangles = np.empty((0, 3, 3), dtype=np.float64)
            areas = np.empty(0, dtype=np.float64)
        entries.append(
            {
                "world": world,
                "triangles": triangles,
                "areas": areas,
                "surface_area": float(np.sum(areas)),
            }
        )

    total_area = sum(entry["surface_area"] for entry in entries)
    samples: list[np.ndarray] = []
    for entry in entries:
        if total_area > 1e-18 and entry["surface_area"] > 0.0:
            quota = max(64, int(math.ceil(max_points * entry["surface_area"] / total_area)))
        else:
            quota = max(24, int(math.ceil(max_points * len(entry["world"]) / total)))

        vertex_quota = min(len(entry["world"]), max(24, quota // 5))
        if len(entry["world"]) > vertex_quota:
            chosen = rng.choice(len(entry["world"]), size=vertex_quota, replace=False)
            vertex_samples = entry["world"][chosen]
        else:
            vertex_samples = entry["world"]
        samples.append(vertex_samples)

        surface_quota = max(quota - len(vertex_samples), 0)
        if surface_quota and len(entry["triangles"]):
            probabilities = entry["areas"] / max(float(np.sum(entry["areas"])), 1e-18)
            chosen = rng.choice(
                len(entry["triangles"]),
                size=surface_quota,
                replace=True,
                p=probabilities,
            )
            selected = entry["triangles"][chosen]
            first = np.sqrt(rng.random(surface_quota))
            second = rng.random(surface_quota)
            surface_samples = (
                (1.0 - first)[:, None] * selected[:, 0]
                + (first * (1.0 - second))[:, None] * selected[:, 1]
                + (first * second)[:, None] * selected[:, 2]
            )
            samples.append(surface_samples)

    points = np.concatenate(samples, axis=0)
    if len(points) > max_points:
        chosen = rng.choice(len(points), size=max_points, replace=False)
        points = points[chosen]

    center = (minimum + maximum) * 0.5
    size = maximum - minimum
    return {
        "points": points,
        "minimum": minimum,
        "maximum": maximum,
        "center": center,
        "size": size,
        "diagonal": max(float(np.linalg.norm(size)), 1e-12),
        "vertex_count": total,
    }


def deterministic_subset(points: np.ndarray, count: int, seed: int) -> np.ndarray:
    if len(points) <= count:
        return points.copy()
    rng = np.random.default_rng(seed)
    return points[rng.choice(len(points), size=count, replace=False)]


def build_tree(points: np.ndarray) -> KDTree:
    tree = KDTree(len(points))
    for index, point in enumerate(points):
        tree.insert(Vector((float(point[0]), float(point[1]), float(point[2]))), index)
    tree.balance()
    return tree


def nearest_points(tree: KDTree, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    targets = np.empty_like(points)
    distances = np.empty(len(points), dtype=np.float64)
    for index, point in enumerate(points):
        coordinate, _target_index, distance = tree.find(
            Vector((float(point[0]), float(point[1]), float(point[2])))
        )
        targets[index] = coordinate
        distances[index] = float(distance)
    return targets, distances


def pca_basis(points: np.ndarray) -> tuple[np.ndarray, list[float]]:
    centered = points - points.mean(axis=0)
    covariance = centered.T @ centered / max(len(points), 1)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values = values[order]
    basis = vectors[:, order]
    if np.linalg.det(basis) < 0.0:
        basis[:, -1] *= -1.0
    ratios = [
        float(values[index] / max(values[index + 1], 1e-18))
        for index in range(2)
    ]
    return basis, ratios


def signed_permutations(determinant_sign: int) -> list[np.ndarray]:
    matrices: list[np.ndarray] = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1.0, 1.0), repeat=3):
            matrix = np.zeros((3, 3), dtype=np.float64)
            for row, column in enumerate(permutation):
                matrix[row, column] = signs[row]
            determinant = int(round(np.linalg.det(matrix)))
            if determinant == determinant_sign:
                matrices.append(matrix)
    return matrices


def initial_transforms(
    high: dict[str, Any],
    low: dict[str, Any],
    reflected: bool,
    rigid_only: bool,
) -> list[np.ndarray]:
    high_basis, _ = pca_basis(high["points"])
    low_basis, _ = pca_basis(low["points"])
    sign = -1 if reflected else 1
    scale = 1.0 if rigid_only else high["diagonal"] / low["diagonal"]
    transforms: list[np.ndarray] = []
    for permutation in signed_permutations(sign):
        rotation = high_basis @ permutation @ low_basis.T
        linear = scale * rotation
        translation = high["center"] - linear @ low["center"]
        matrix = IDENTITY4.copy()
        matrix[:3, :3] = linear
        matrix[:3, 3] = translation
        transforms.append(matrix)
    return transforms


def estimate_increment(
    source: np.ndarray,
    target: np.ndarray,
    rigid_only: bool,
) -> np.ndarray:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    source_zero = source - source_center
    target_zero = target - target_center
    covariance = target_zero.T @ source_zero / max(len(source), 1)
    left, singular, right_t = np.linalg.svd(covariance)
    correction = np.eye(3, dtype=np.float64)
    if np.linalg.det(left @ right_t) < 0.0:
        correction[-1, -1] = -1.0
    rotation = left @ correction @ right_t
    if rigid_only:
        scale = 1.0
    else:
        variance = float(np.sum(source_zero * source_zero) / max(len(source), 1))
        scale = float(np.sum(singular * np.diag(correction)) / max(variance, 1e-18))
        scale = float(np.clip(scale, 0.80, 1.25))
    translation = target_center - scale * rotation @ source_center
    result = IDENTITY4.copy()
    result[:3, :3] = scale * rotation
    result[:3, 3] = translation
    return result


def trimmed_metrics(distances: np.ndarray, trim_fraction: float) -> dict[str, float]:
    trim_fraction = float(np.clip(trim_fraction, 0.5, 1.0))
    cutoff = float(np.quantile(distances, trim_fraction))
    kept = distances[distances <= cutoff]
    if len(kept) < 6:
        kept = distances
    return {
        "trimmed_rms": float(np.sqrt(np.mean(kept * kept))),
        "median": float(np.median(distances)),
        "p95": float(np.quantile(distances, 0.95)),
        "maximum": float(np.max(distances)),
    }


def run_icp(
    source: np.ndarray,
    target_tree: KDTree,
    initial: np.ndarray,
    iterations: int,
    trim_fraction: float,
    rigid_only: bool,
) -> tuple[np.ndarray, dict[str, float]]:
    matrix = initial.copy()
    previous = math.inf
    metrics: dict[str, float] = {}
    for _ in range(max(iterations, 1)):
        current = transform_points(source, matrix)
        targets, distances = nearest_points(target_tree, current)
        cutoff = float(np.quantile(distances, trim_fraction))
        mask = distances <= cutoff
        if int(np.count_nonzero(mask)) < 12:
            break
        increment = estimate_increment(current[mask], targets[mask], rigid_only)
        matrix = increment @ matrix
        metrics = trimmed_metrics(distances, trim_fraction)
        score = metrics["trimmed_rms"]
        if abs(previous - score) <= max(score, 1e-9) * 1e-6:
            break
        previous = score
    current = transform_points(source, matrix)
    _targets, distances = nearest_points(target_tree, current)
    return matrix, trimmed_metrics(distances, trim_fraction)


def transformed_bounds(objects: Iterable[bpy.types.Object], matrix: np.ndarray) -> dict[str, Any]:
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    for obj in objects:
        world = object_world_vertices(obj)
        if len(world) == 0:
            continue
        aligned = transform_points(world, matrix)
        minimum = np.minimum(minimum, aligned.min(axis=0))
        maximum = np.maximum(maximum, aligned.max(axis=0))
    center = (minimum + maximum) * 0.5
    size = maximum - minimum
    return {"minimum": minimum, "maximum": maximum, "center": center, "size": size}


def match_axis_aligned_bounds(
    objects: Iterable[bpy.types.Object],
    matrix: np.ndarray,
    high: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Scale an oriented solution in high world axes so its AABB matches the high."""
    current = transformed_bounds(objects, matrix)
    if np.any(current["size"] <= 1e-12):
        raise RuntimeError("Cannot axis-scale a low model with a zero-size dimension.")
    factors = high["size"] / current["size"]
    correction = IDENTITY4.copy()
    correction[:3, :3] = np.diag(factors)
    correction[:3, 3] = high["center"] - factors * current["center"]
    return correction @ matrix, factors


def rotation_from_similarity(matrix: np.ndarray) -> tuple[np.ndarray, float, bool]:
    determinant = float(np.linalg.det(matrix[:3, :3]))
    scale = float(np.cbrt(abs(determinant)))
    rotation = matrix[:3, :3] / max(scale, 1e-18)
    return rotation, scale, determinant < 0.0


def rotation_difference_degrees(first: np.ndarray, second: np.ndarray) -> float:
    first_rotation, _scale, _reflected = rotation_from_similarity(first)
    second_rotation, _scale, _reflected = rotation_from_similarity(second)
    relative = second_rotation @ first_rotation.T
    cosine = float(np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def rotation_from_identity_degrees(matrix: np.ndarray) -> float:
    rotation, _scale, reflected = rotation_from_similarity(matrix)
    if reflected:
        return 180.0
    cosine = float(np.clip((np.trace(rotation) - 1.0) * 0.5, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def source_local_axis_difference_degrees(
    candidate: dict[str, Any],
    high_objects: list[bpy.types.Object],
    low_objects: list[bpy.types.Object],
) -> float:
    if len(high_objects) != 1 or len(low_objects) != 1:
        raise RuntimeError("--prefer-source-local-axes requires one high and one low mesh object.")
    prepared_low_world = Matrix(candidate["matrix"].tolist()) @ low_objects[0].matrix_world
    difference = high_objects[0].matrix_world.to_quaternion().rotation_difference(
        prepared_low_world.to_quaternion()
    )
    return math.degrees(float(difference.angle))


def evaluate_candidate(
    matrix: np.ndarray,
    high: dict[str, Any],
    low_objects: list[bpy.types.Object],
    high_score_points: np.ndarray,
    low_score_points: np.ndarray,
    high_tree: KDTree,
    trim_fraction: float,
) -> dict[str, Any]:
    aligned_low = transform_points(low_score_points, matrix)
    low_tree = build_tree(aligned_low)
    _forward_targets, forward_distances = nearest_points(high_tree, aligned_low)
    _reverse_targets, reverse_distances = nearest_points(low_tree, high_score_points)
    forward = trimmed_metrics(forward_distances, trim_fraction)
    reverse = trimmed_metrics(reverse_distances, trim_fraction)
    bounds = transformed_bounds(low_objects, matrix)
    center_error = float(np.linalg.norm(bounds["center"] - high["center"]) / high["diagonal"])
    dimension_error = float(
        np.max(np.abs(bounds["size"] - high["size"]))
        / max(float(np.max(high["size"])), 1e-12)
    )
    symmetric_rms = 0.5 * (forward["trimmed_rms"] + reverse["trimmed_rms"])
    surface_ratio = max(forward["p95"], reverse["p95"]) / high["diagonal"]
    score = (
        symmetric_rms / high["diagonal"]
        + 0.25 * center_error
        + 0.10 * dimension_error
    )
    rotation, scale, reflected = rotation_from_similarity(matrix)
    euler = Matrix(rotation.tolist()).to_euler("XYZ")
    return {
        "matrix": matrix,
        "score": float(score),
        "surface_error_ratio": float(surface_ratio),
        "center_error_ratio": center_error,
        "dimension_error_ratio": dimension_error,
        "forward": forward,
        "reverse": reverse,
        "uniform_scale": scale,
        "rotation_euler_degrees": [math.degrees(float(value)) for value in euler],
        "translation": [float(value) for value in matrix[:3, 3]],
        "reflected": reflected,
    }


def solve(
    high: dict[str, Any],
    low: dict[str, Any],
    high_objects: list[bpy.types.Object],
    low_objects: list[bpy.types.Object],
    args: argparse.Namespace,
    reflected: bool,
) -> list[dict[str, Any]]:
    del high_objects
    high_fit = deterministic_subset(high["points"], min(args.samples, len(high["points"])), 11)
    low_fit = deterministic_subset(low["points"], min(args.samples, len(low["points"])), 13)
    coarse_high = deterministic_subset(high_fit, min(650, len(high_fit)), 17)
    coarse_low = deterministic_subset(low_fit, min(650, len(low_fit)), 19)
    coarse_tree = build_tree(coarse_high)
    coarse: list[tuple[float, np.ndarray]] = []
    for initial in initial_transforms(high, low, reflected, args.rigid_only):
        matrix, metrics = run_icp(
            coarse_low,
            coarse_tree,
            initial,
            args.coarse_iterations,
            args.trim_fraction,
            args.rigid_only,
        )
        coarse.append((metrics["trimmed_rms"], matrix))
    coarse.sort(key=lambda item: item[0])

    high_tree = build_tree(high_fit)
    results: list[dict[str, Any]] = []
    for _score, initial in coarse[: max(args.final_candidates, 1)]:
        matrix, _metrics = run_icp(
            low_fit,
            high_tree,
            initial,
            args.fine_iterations,
            args.trim_fraction,
            args.rigid_only,
        )
        results.append(
            evaluate_candidate(
                matrix,
                high,
                low_objects,
                high_fit,
                low_fit,
                high_tree,
                args.trim_fraction,
            )
        )
    results.sort(key=lambda item: item["score"])
    return results


def has_low_uv(objects: Iterable[bpy.types.Object]) -> bool:
    meshes = [obj for obj in objects if len(obj.data.polygons) > 0]
    return bool(meshes) and all(obj.data.uv_layers.active is not None for obj in meshes)


def topology_uv_fingerprint(objects: Iterable[bpy.types.Object]) -> dict[str, Any]:
    """Return an exact in-Blender fingerprint that excludes vertex coordinates."""
    meshes = []
    for obj in sorted(objects, key=lambda item: item.name):
        mesh = obj.data
        topology = hashlib.sha256()
        for edge in mesh.edges:
            topology.update(f"e:{edge.vertices[0]},{edge.vertices[1]};".encode("ascii"))
        for polygon in mesh.polygons:
            topology.update(
                ("p:" + ",".join(str(index) for index in polygon.vertices) + ";").encode(
                    "ascii"
                )
            )

        uv_layers = []
        for layer in mesh.uv_layers:
            uv_digest = hashlib.sha256()
            for loop in layer.data:
                uv_digest.update(np.asarray(loop.uv, dtype=np.float32).tobytes())
            uv_layers.append(
                {"name": layer.name, "count": len(layer.data), "hash": uv_digest.hexdigest()}
            )

        meshes.append(
            {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
                "loops": len(mesh.loops),
                "topology_hash": topology.hexdigest(),
                "uv_layers": uv_layers,
                "material_slots": [slot.material.name if slot.material else None for slot in obj.material_slots],
                "vertex_groups": [group.name for group in obj.vertex_groups],
                "shape_keys": (
                    [block.name for block in mesh.shape_keys.key_blocks]
                    if mesh.shape_keys is not None
                    else []
                ),
            }
        )
    return {"object_count": len(meshes), "meshes": meshes}


def fbx_structure_summary(objects: Iterable[bpy.types.Object]) -> dict[str, Any]:
    """Return an ordering-tolerant structure summary for FBX round-trip checks."""
    meshes = []
    for obj in objects:
        mesh = obj.data
        uv_layers = []
        for layer in mesh.uv_layers:
            rounded_uvs = sorted(
                (round(float(loop.uv[0]), 6), round(float(loop.uv[1]), 6))
                for loop in layer.data
            )
            digest = hashlib.sha256(repr(rounded_uvs).encode("ascii")).hexdigest()
            uv_layers.append({"count": len(layer.data), "value_hash": digest})
        meshes.append(
            {
                "vertices": len(mesh.vertices),
                "polygons": len(mesh.polygons),
                "loops": len(mesh.loops),
                "polygon_sizes": sorted(len(polygon.vertices) for polygon in mesh.polygons),
                "uv_layers": sorted(uv_layers, key=lambda item: (item["count"], item["value_hash"])),
                "material_slot_count": len(obj.material_slots),
            }
        )
    meshes.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return {"object_count": len(meshes), "meshes": meshes}


def common_high_rotation(objects: list[bpy.types.Object]) -> tuple[Any, str]:
    rotations = [obj.matrix_world.to_quaternion() for obj in objects]
    reference = rotations[0]
    maximum = max(reference.rotation_difference(item).angle for item in rotations)
    if maximum > math.radians(0.05):
        return None, "multiple_unrelated_object_rotations"
    if abs(reference.angle) <= math.radians(0.001):
        return None, "no_traceable_object_rotation"
    return reference, "common_object_rotation"


def upright_transform(
    high_center: np.ndarray,
    high_objects: list[bpy.types.Object],
    args: argparse.Namespace,
) -> tuple[np.ndarray, str]:
    if args.manual_high_rotation is not None:
        current = Euler(
            tuple(math.radians(value) for value in args.manual_high_rotation), "XYZ"
        ).to_matrix().to_4x4()
        inverse = current.inverted()
        mode = "manual_high_rotation"
    elif args.straighten_high:
        rotation, reason = common_high_rotation(high_objects)
        if rotation is None:
            raise RuntimeError(
                f"Cannot safely straighten high automatically: {reason}. "
                "Provide --manual-high-rotation after visual confirmation."
            )
        inverse = rotation.inverted().to_matrix().to_4x4()
        mode = reason
    else:
        return IDENTITY4.copy(), "keep_current_high_pose"

    pivot = Matrix.Translation(Vector(tuple(float(value) for value in high_center)))
    result = pivot @ inverse @ pivot.inverted()
    return np_matrix(result), mode


def reverse_mesh_faces(mesh: bpy.types.Mesh) -> None:
    editable = bmesh.new()
    editable.from_mesh(mesh)
    bmesh.ops.reverse_faces(editable, faces=list(editable.faces))
    editable.to_mesh(mesh)
    editable.free()
    mesh.update()


def bake_objects(objects: list[bpy.types.Object], matrix: np.ndarray) -> None:
    global_reflection = float(np.linalg.det(matrix[:3, :3])) < 0.0
    transform = Matrix(matrix.tolist())
    for obj in objects:
        object_world = obj.matrix_world.copy()
        obj.parent = None
        obj.data = obj.data.copy()
        obj.data.transform(transform @ object_world)
        obj.matrix_world = Matrix.Identity(4)
        if global_reflection:
            reverse_mesh_faces(obj.data)
        obj.data.update()


def role_bounds(objects: Iterable[bpy.types.Object]) -> dict[str, list[float]]:
    payload = collect_points(objects, max_points=1, seed=1)
    return {
        "minimum": [float(value) for value in payload["minimum"]],
        "maximum": [float(value) for value in payload["maximum"]],
        "center": [float(value) for value in payload["center"]],
        "size": [float(value) for value in payload["size"]],
    }


def export_fbx(objects: list[bpy.types.Object], path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        object_types={"MESH"},
        use_mesh_modifiers=False,
        bake_anim=False,
        add_leaf_bones=False,
        apply_unit_scale=True,
        use_space_transform=True,
        axis_forward="-Z",
        axis_up="Y",
        path_mode="AUTO",
    )


def configure_alignment_display(
    high_objects: list[bpy.types.Object], low_objects: list[bpy.types.Object]
) -> None:
    """Use viewport-only colors; never replace the user's material slots."""
    for obj in high_objects:
        obj.color = (0.03, 0.16, 0.80, 1.0)
        obj.display_type = "SOLID"
        obj.show_in_front = False
        obj.show_wire = False
    for obj in low_objects:
        obj.color = (0.01, 1.0, 0.04, 1.0)
        obj.display_type = "SOLID"
        obj.show_in_front = True
        obj.show_wire = True
        obj.show_all_edges = True
    bpy.ops.object.select_all(action="DESELECT")
    bounds = role_bounds(low_objects)
    view_center = Vector(bounds["center"])
    view_distance = max(max(bounds["size"]), 1e-6) * 3.8
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.shading.type = "SOLID"
            space.shading.color_type = "OBJECT"
            space.shading.show_xray = False
            space.overlay.show_overlays = True
            space.region_3d.view_location = view_center
            space.region_3d.view_distance = view_distance


def compare_readback(
    high_path: Path,
    low_path: Path,
    expected_high: dict[str, list[float]],
    expected_low: dict[str, list[float]],
    expected_low_structure: dict[str, Any],
) -> dict[str, Any]:
    clear_scene()
    high_objects = import_model(str(high_path), "readback_high")
    high = role_bounds(high_objects)
    clear_scene()
    low_objects = import_model(str(low_path), "readback_low")
    low = role_bounds(low_objects)
    low_structure = fbx_structure_summary(low_objects)

    reference = max(float(np.linalg.norm(expected_high["size"])), 1e-12)
    high_error = max(
        float(np.linalg.norm(np.array(high[key]) - np.array(expected_high[key])) / reference)
        for key in ("center", "size")
    )
    low_error = max(
        float(np.linalg.norm(np.array(low[key]) - np.array(expected_low[key])) / reference)
        for key in ("center", "size")
    )
    structure_match = low_structure == expected_low_structure
    passed = high_error <= 1e-5 and low_error <= 1e-5 and structure_match
    return {
        "high_center_size_error_ratio": high_error,
        "low_center_size_error_ratio": low_error,
        "tolerance": 1e-5,
        "low_structure_match": structure_match,
        "expected_low_structure": expected_low_structure,
        "actual_low_structure": low_structure,
        "pass": passed,
    }


def serializable_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (json_matrix(value) if key == "matrix" else value)
        for key, value in candidate.items()
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    args = parse_args()
    if not 0.5 <= args.trim_fraction <= 1.0:
        raise ValueError("--trim-fraction must be between 0.5 and 1.0.")
    if args.samples < 200 or args.target_points < args.samples:
        raise ValueError("Use at least 200 samples and target-points >= samples.")
    if args.allow_axis_scale and args.rigid_only:
        raise ValueError("--allow-axis-scale and --rigid-only cannot be used together.")
    if args.prefer_current_orientation and args.prefer_source_local_axes:
        raise ValueError(
            "Use only one of --prefer-current-orientation or --prefer-source-local-axes."
        )
    if not 0.0 <= args.max_axis_scale_delta <= 0.50:
        raise ValueError("--max-axis-scale-delta must be between 0 and 0.50.")
    if not 0.0 <= args.source_axis_score_gap <= 0.50:
        raise ValueError("--source-axis-score-gap must be between 0 and 0.50.")

    output_dir = Path(args.output_dir).expanduser().resolve()
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else output_dir / "bake_alignment_report.json"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    clear_scene()
    high_objects = import_model(args.high, "high")
    low_objects = import_model(args.low, "low")
    low_fingerprint_before = topology_uv_fingerprint(low_objects)
    low_fbx_structure_before = fbx_structure_summary(low_objects)
    low_has_uv = has_low_uv(low_objects)
    high = collect_points(high_objects, args.target_points, seed=3)
    low = collect_points(low_objects, args.target_points, seed=5)
    high_basis, high_pca_ratios = pca_basis(high["points"])
    low_basis, low_pca_ratios = pca_basis(low["points"])
    del high_basis, low_basis

    report: dict[str, Any] = {
        "skill": "blender-align-bake-models",
        "inputs": {
            "high": str(Path(args.high).resolve()),
            "low": str(Path(args.low).resolve()),
        },
        "authority": "high",
        "method": (
            "PCA proper-axis hypotheses + trimmed similarity ICP"
            + (" + controlled XYZ bounds refinement" if args.allow_axis_scale else "")
        ),
        "pca_eigenvalue_ratios": {"high": high_pca_ratios, "low": low_pca_ratios},
        "low_has_active_uv": low_has_uv,
        "transform_only": True,
        "low_fingerprint_before": low_fingerprint_before,
        "thresholds": {
            "surface_error_ratio": args.max_surface_error_ratio,
            "center_error_ratio": args.max_center_error_ratio,
            "dimension_error_ratio": args.max_dimension_error_ratio,
            "ambiguity_gap": args.ambiguity_gap,
        },
        "pass": False,
    }

    proper = solve(high, low, high_objects, low_objects, args, reflected=False)
    best = proper[0]
    if args.prefer_current_orientation:
        tied_limit = best["score"] * (1.0 + args.ambiguity_gap) + 1e-7
        tied = [candidate for candidate in proper if candidate["score"] <= tied_limit]
        best = min(tied, key=lambda candidate: rotation_from_identity_degrees(candidate["matrix"]))
        report["current_orientation_preference"] = {
            "enabled": True,
            "tied_candidate_count": len(tied),
            "selected_rotation_from_identity_degrees": rotation_from_identity_degrees(best["matrix"]),
        }
    if args.prefer_source_local_axes:
        tied_limit = proper[0]["score"] * (1.0 + args.source_axis_score_gap) + 1e-7
        tied = [candidate for candidate in proper if candidate["score"] <= tied_limit]
        best = min(
            tied,
            key=lambda candidate: source_local_axis_difference_degrees(
                candidate,
                high_objects,
                low_objects,
            ),
        )
        report["source_local_axis_preference"] = {
            "enabled": True,
            "score_gap_limit": args.source_axis_score_gap,
            "near_best_candidate_count": len(tied),
            "selected_axis_difference_degrees": source_local_axis_difference_degrees(
                best,
                high_objects,
                low_objects,
            ),
        }
    report["proper_candidates"] = [serializable_candidate(item) for item in proper]

    distinct_second = None
    for candidate in proper:
        if candidate is best:
            continue
        if rotation_difference_degrees(best["matrix"], candidate["matrix"]) > 8.0:
            distinct_second = candidate
            break
    ambiguous = False
    if distinct_second is not None:
        allowed_score = best["score"] * (1.0 + args.ambiguity_gap) + 1e-7
        ambiguous = distinct_second["score"] <= allowed_score
        report["orientation_competitor"] = serializable_candidate(distinct_second)
        report["orientation_score_gap_ratio"] = float(
            (distinct_second["score"] - best["score"]) / max(best["score"], 1e-12)
        )

    surface_pass = best["surface_error_ratio"] <= args.max_surface_error_ratio
    center_pass = best["center_error_ratio"] <= args.max_center_error_ratio
    dimension_pass = best["dimension_error_ratio"] <= args.max_dimension_error_ratio
    uv_pass = low_has_uv or not args.require_low_uv

    mirror_suspected = False
    if not args.allow_mirror and not (surface_pass and center_pass and dimension_pass):
        reflected = solve(high, low, high_objects, low_objects, args, reflected=True)
        reflected_best = reflected[0]
        report["reflected_probe"] = serializable_candidate(reflected_best)
        mirror_suspected = reflected_best["score"] < best["score"] * 0.72

    if args.allow_mirror:
        reflected = solve(high, low, high_objects, low_objects, args, reflected=True)
        report["reflected_candidates"] = [serializable_candidate(item) for item in reflected]
        if reflected[0]["score"] < best["score"]:
            best = reflected[0]
            surface_pass = best["surface_error_ratio"] <= args.max_surface_error_ratio
            center_pass = best["center_error_ratio"] <= args.max_center_error_ratio
            dimension_pass = best["dimension_error_ratio"] <= args.max_dimension_error_ratio

    axis_scale_pass = True
    if args.allow_axis_scale:
        before_axis_scale = best
        refined_matrix, axis_factors = match_axis_aligned_bounds(
            low_objects,
            best["matrix"],
            high,
        )
        maximum_delta = float(np.max(np.abs(axis_factors - 1.0)))
        axis_scale_pass = maximum_delta <= args.max_axis_scale_delta
        best = evaluate_candidate(
            refined_matrix,
            high,
            low_objects,
            deterministic_subset(high["points"], min(args.samples, len(high["points"])), 11),
            deterministic_subset(low["points"], min(args.samples, len(low["points"])), 13),
            build_tree(deterministic_subset(high["points"], min(args.samples, len(high["points"])), 11)),
            args.trim_fraction,
        )
        report["axis_scale_refinement"] = {
            "enabled": True,
            "world_axis_factors": [float(value) for value in axis_factors],
            "maximum_delta": maximum_delta,
            "limit": args.max_axis_scale_delta,
            "within_limit": axis_scale_pass,
            "before": serializable_candidate(before_axis_scale),
        }

    if args.match_bounds_center:
        before_center_match = best
        current_bounds = transformed_bounds(low_objects, best["matrix"])
        correction = IDENTITY4.copy()
        correction[:3, 3] = high["center"] - current_bounds["center"]
        centered_matrix = correction @ best["matrix"]
        high_evaluation = deterministic_subset(
            high["points"], min(args.samples, len(high["points"])), 11
        )
        low_evaluation = deterministic_subset(
            low["points"], min(args.samples, len(low["points"])), 13
        )
        best = evaluate_candidate(
            centered_matrix,
            high,
            low_objects,
            high_evaluation,
            low_evaluation,
            build_tree(high_evaluation),
            args.trim_fraction,
        )
        report["bounds_center_refinement"] = {
            "enabled": True,
            "translation": [float(value) for value in correction[:3, 3]],
            "before": serializable_candidate(before_center_match),
        }

    surface_pass = best["surface_error_ratio"] <= args.max_surface_error_ratio
    center_pass = best["center_error_ratio"] <= args.max_center_error_ratio
    dimension_pass = best["dimension_error_ratio"] <= args.max_dimension_error_ratio

    report["selected"] = serializable_candidate(best)
    report["gates"] = {
        "surface": surface_pass,
        "center": center_pass,
        "dimensions": dimension_pass,
        "axis_scale_within_limit": axis_scale_pass,
        "low_uv": uv_pass,
        "orientation_unambiguous": (
            not ambiguous
            or args.allow_ambiguous
            or args.prefer_current_orientation
            or args.prefer_source_local_axes
        ),
        "mirror_authorized_or_not_suspected": not mirror_suspected,
    }

    all_passed = all(report["gates"].values())
    if not all_passed:
        if mirror_suspected:
            report["error"] = "MIRRORED_ALIGNMENT_SUSPECTED"
            exit_code = 4
        elif ambiguous and not args.allow_ambiguous:
            report["error"] = "ALIGNMENT_ORIENTATION_AMBIGUOUS"
            exit_code = 3
        elif not uv_pass:
            report["error"] = "LOW_MODEL_HAS_NO_ACTIVE_UV"
            exit_code = 2
        else:
            report["error"] = "ALIGNMENT_QUALITY_GATE_FAILED"
            exit_code = 2
        write_json(report_path, report)
        print(f"Alignment rejected; report written to {report_path}")
        return exit_code

    upright, upright_mode = upright_transform(high["center"], high_objects, args)
    high_bake_matrix = upright
    low_bake_matrix = upright @ best["matrix"]
    report["upright_mode"] = upright_mode
    report["bake_from_high_world"] = json_matrix(upright)

    if not args.dry_run:
        bake_objects(high_objects, high_bake_matrix)
        bake_objects(low_objects, low_bake_matrix)
        low_fingerprint_after = topology_uv_fingerprint(low_objects)
        low_preserved = low_fingerprint_after == low_fingerprint_before
        report["low_preservation"] = {
            "pass": low_preserved,
            "after": low_fingerprint_after,
        }
        if not low_preserved:
            report["error"] = "LOW_TOPOLOGY_OR_UV_CHANGED"
            write_json(report_path, report)
            print(f"Transform-only invariant rejected; report written to {report_path}")
            return 6
        expected_high = role_bounds(high_objects)
        expected_low = role_bounds(low_objects)

        high_output = output_dir / "bake_high.fbx"
        low_output = output_dir / "bake_low.fbx"
        blend_output = output_dir / "bake_alignment.blend"
        export_fbx(high_objects, high_output)
        export_fbx(low_objects, low_output)
        configure_alignment_display(high_objects, low_objects)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output))
        readback = compare_readback(
            high_output,
            low_output,
            expected_high,
            expected_low,
            low_fbx_structure_before,
        )
        report["readback"] = readback
        if not readback["pass"]:
            report["error"] = "EXPORT_READBACK_MISMATCH"
            write_json(report_path, report)
            print(f"Export readback rejected; report written to {report_path}")
            return 5
        report["outputs"] = {
            "high": str(high_output),
            "low": str(low_output),
            "blend": str(blend_output),
            "report": str(report_path),
        }

    report["pass"] = True
    write_json(report_path, report)
    print(f"Alignment passed; report written to {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print(f"ALIGN_BAKE_MODELS_ERROR: {error}", file=sys.stderr)
        raise
