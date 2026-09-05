"""Fresh-FBX readback audit for a prepared high/low bake pair."""

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--high", required=True)
    parser.add_argument("--low", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-samples", type=int, default=30000)
    return parser.parse_args(argv)


def import_fbx(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=False)
    return [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]


def triangle_count(obj):
    return sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons)


def bounds(obj):
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    return minimum, maximum, (minimum + maximum) * 0.5, maximum - minimum


def percentile(values, fraction):
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def make_bvh(obj):
    vertices = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    polygons = [tuple(poly.vertices) for poly in obj.data.polygons]
    return BVHTree.FromPolygons(vertices, polygons, all_triangles=False)


def nearest_vertex_distances(source, target_bvh, max_samples):
    stride = max(1, len(source.data.vertices) // max_samples)
    distances = []
    for index in range(0, len(source.data.vertices), stride):
        hit = target_bvh.find_nearest(source.matrix_world @ source.data.vertices[index].co)
        if hit and hit[0] is not None:
            distances.append(hit[3])
    return distances


def component_count(obj):
    neighbors = [set() for _ in obj.data.vertices]
    for edge in obj.data.edges:
        a, b = edge.vertices
        neighbors[a].add(b)
        neighbors[b].add(a)
    remaining = set(range(len(neighbors)))
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            connected = neighbors[stack.pop()] & remaining
            remaining.difference_update(connected)
            stack.extend(connected)
    return count


def topology_stats(obj):
    edge_lookup = {tuple(sorted(edge.vertices)): edge.index for edge in obj.data.edges}
    edge_users = [0] * len(obj.data.edges)
    for poly in obj.data.polygons:
        vertices = list(poly.vertices)
        for index, a in enumerate(vertices):
            b = vertices[(index + 1) % len(vertices)]
            edge_users[edge_lookup[tuple(sorted((a, b)))]] += 1
    return {
        "components": component_count(obj),
        "degenerate_faces": sum(1 for poly in obj.data.polygons if poly.area <= 1e-12),
        "boundary_edges": sum(1 for users in edge_users if users == 1),
        "nonmanifold_edges": sum(1 for users in edge_users if users > 2),
    }


def main():
    args = parse_args()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    high_objects = import_fbx(args.high)
    low_objects = import_fbx(args.low)
    if len(high_objects) != 1 or len(low_objects) != 1:
        raise RuntimeError(
            f"Expected one mesh per FBX; got high={len(high_objects)} low={len(low_objects)}"
        )
    high, low = high_objects[0], low_objects[0]

    _, _, high_center, high_dimensions = bounds(high)
    _, _, low_center, low_dimensions = bounds(low)
    diagonal = high_dimensions.length
    low_to_high = nearest_vertex_distances(low, make_bvh(high), args.max_samples)
    high_to_low = nearest_vertex_distances(high, make_bvh(low), args.max_samples)
    topology = topology_stats(low)

    comparison = {
        "center_error_ratio": (high_center - low_center).length / diagonal,
        "dimension_error_ratio_max": max(
            abs(high_dimensions[axis] - low_dimensions[axis])
            / max(high_dimensions[axis], 1e-8)
            for axis in range(3)
        ),
        "low_to_high_p95_ratio": percentile(low_to_high, 0.95) / diagonal,
        "low_to_high_p99_ratio": percentile(low_to_high, 0.99) / diagonal,
        "high_to_low_p95_ratio": percentile(high_to_low, 0.95) / diagonal,
        "high_to_low_p99_ratio": percentile(high_to_low, 0.99) / diagonal,
    }
    report = {
        "pass": False,
        "fbx_readback": True,
        "high": {
            "triangles": triangle_count(high),
            "vertices": len(high.data.vertices),
            "uv_layers": len(high.data.uv_layers),
            "center": list(high_center),
            "dimensions": list(high_dimensions),
        },
        "low": {
            "triangles": triangle_count(low),
            "vertices": len(low.data.vertices),
            "uv_layers": len(low.data.uv_layers),
            "center": list(low_center),
            "dimensions": list(low_dimensions),
            "topology": topology,
        },
        "comparison": comparison,
    }
    report["pass"] = bool(
        report["low"]["uv_layers"] > 0
        and report["low"]["triangles"] < report["high"]["triangles"]
        and comparison["center_error_ratio"] <= 0.01
        and comparison["dimension_error_ratio_max"] <= 0.03
        and comparison["low_to_high_p95_ratio"] <= 0.02
        and comparison["high_to_low_p95_ratio"] <= 0.04
        and topology["degenerate_faces"] == 0
        and topology["nonmanifold_edges"] == 0
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

