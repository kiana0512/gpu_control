"""Fast UV QA for Blender files and imported mesh formats.

Run with Blender:
  blender --background --python qa_uv.py -- --input model_PBR_UV.blend
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict, deque

import bpy
from mathutils import Vector

EPS = 1.0e-10


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--stretch-p90", type=float, default=1.2)
    parser.add_argument("--stretch-p95", type=float, default=1.5)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--enforce-imported-hard-edges", action="store_true")
    parser.add_argument("--require-max-compatible-shells", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def open_source(path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if extension == ".fbx":
        # Blender 4.3 moved FBX import to wm.fbx_import.  bpy.ops proxies
        # report arbitrary attributes as present, so hasattr is unreliable.
        if bpy.app.version >= (4, 3, 0):
            bpy.ops.wm.fbx_import(filepath=path)
        else:
            bpy.ops.import_scene.fbx(filepath=path)
    elif extension == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported input format: {extension}")


def percentile(values, fraction):
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    amount = position - lower
    return values[lower] * (1.0 - amount) + values[upper] * amount


def topology(mesh):
    edge_by_vertices = {tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges}
    edge_faces = [[] for _ in mesh.edges]
    polygon_edges = []
    for polygon in mesh.polygons:
        edges = []
        for index, vertex in enumerate(polygon.vertices):
            other = polygon.vertices[(index + 1) % len(polygon.vertices)]
            edge_index = edge_by_vertices[tuple(sorted((vertex, other)))]
            edges.append(edge_index)
            edge_faces[edge_index].append(polygon.index)
        polygon_edges.append(edges)
    return polygon_edges, edge_faces


def face_vertex_uv(mesh, uv_layer, face_index, vertex_index):
    polygon = mesh.polygons[face_index]
    for loop_index in polygon.loop_indices:
        if mesh.loops[loop_index].vertex_index == vertex_index:
            return uv_layer.data[loop_index].uv
    raise RuntimeError("Vertex not found in face")


def edge_uv_continuous(mesh, uv_layer, edge_index, linked_faces):
    if len(linked_faces) != 2:
        return False
    first, second = linked_faces
    for vertex in mesh.edges[edge_index].vertices:
        uv_first = face_vertex_uv(mesh, uv_layer, first, vertex)
        uv_second = face_vertex_uv(mesh, uv_layer, second, vertex)
        if (uv_first - uv_second).length > 1.0e-7:
            return False
    return True


def virtual_split_edge_adjacency(mesh, uv_layer):
    """Find UV-continuous face pairs split only by duplicate FBX vertices."""
    if not mesh.vertices:
        return []
    minimum = Vector([min(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)])
    maximum = Vector([max(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)])
    tolerance = max((maximum - minimum).length, 1.0) * 1.0e-7

    def position_key(vertex_index):
        coordinate = mesh.vertices[vertex_index].co
        return tuple(int(round(coordinate[axis] / tolerance)) for axis in range(3))

    geometric_edges = defaultdict(list)
    for polygon in mesh.polygons:
        loops = list(polygon.loop_indices)
        for index, first_loop in enumerate(loops):
            second_loop = loops[(index + 1) % len(loops)]
            first_key = position_key(mesh.loops[first_loop].vertex_index)
            second_key = position_key(mesh.loops[second_loop].vertex_index)
            if first_key == second_key:
                continue
            edge_key = tuple(sorted((first_key, second_key)))
            geometric_edges[edge_key].append(
                (
                    polygon.index,
                    {
                        first_key: uv_layer.data[first_loop].uv.copy(),
                        second_key: uv_layer.data[second_loop].uv.copy(),
                    },
                )
            )

    by_face_pair = defaultdict(list)
    for edge_key, uses in geometric_edges.items():
        if len(uses) != 2 or uses[0][0] == uses[1][0]:
            continue
        first, second = uses
        pair = tuple(sorted((first[0], second[0])))
        by_face_pair[pair].append((edge_key, first[1], second[1]))

    adjacent = []
    for pair, candidates in by_face_pair.items():
        # Duplicate faces share every edge. They must remain visible to the
        # overlap checker instead of being treated as adjacent faces.
        if len(candidates) != 1:
            continue
        edge_key, first_uv, second_uv = candidates[0]
        if all((first_uv[key] - second_uv[key]).length <= 1.0e-7 for key in edge_key):
            adjacent.append(pair)
    return adjacent


def connected_components(nodes, adjacency):
    seen = set()
    result = []
    for start in nodes:
        if start in seen:
            continue
        seen.add(start)
        queue = deque([start])
        group = []
        while queue:
            node = queue.popleft()
            group.append(node)
            for other in adjacency.get(node, ()):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        result.append(group)
    return result


def triangle_stretch(p0, p1, p2, uv0, uv1, uv2):
    edge1 = p1 - p0
    edge2 = p2 - p0
    length1 = edge1.length
    if length1 <= EPS:
        return None
    axis = edge1 / length1
    x2 = edge2.dot(axis)
    y2_squared = edge2.length_squared - x2 * x2
    if y2_squared <= EPS:
        return None
    y2 = math.sqrt(y2_squared)
    duv1 = uv1 - uv0
    duv2 = uv2 - uv0
    j00 = duv1.x / length1
    j10 = duv1.y / length1
    j01 = (duv2.x - j00 * x2) / y2
    j11 = (duv2.y - j10 * x2) / y2
    a = j00 * j00 + j10 * j10
    b = j00 * j01 + j10 * j11
    d = j01 * j01 + j11 * j11
    discriminant = math.sqrt(max((a - d) ** 2 + 4.0 * b * b, 0.0))
    minimum = 0.5 * (a + d - discriminant)
    maximum = 0.5 * (a + d + discriminant)
    if minimum <= 1.0e-20:
        return None
    return math.sqrt(maximum / minimum)


def cross2(a, b):
    return a.x * b.y - a.y * b.x


def polygon_area(points):
    return 0.5 * sum(cross2(points[(index + 1) % len(points)], points[index]) * -1.0 for index in range(len(points)))


def line_intersection(p1, p2, q1, q2):
    r = p2 - p1
    s = q2 - q1
    denominator = cross2(r, s)
    if abs(denominator) <= EPS:
        return p2.copy()
    amount = cross2(q1 - p1, s) / denominator
    return p1 + r * amount


def triangle_overlap_area(first, second):
    subject = [point.copy() for point in first]
    clip = [point.copy() for point in second]
    sign = 1.0 if polygon_area(clip) >= 0.0 else -1.0
    output = subject
    for index, clip_start in enumerate(clip):
        clip_end = clip[(index + 1) % 3]
        input_points = output
        output = []
        if not input_points:
            break
        previous = input_points[-1]
        previous_inside = sign * cross2(clip_end - clip_start, previous - clip_start) >= -EPS
        for current in input_points:
            current_inside = sign * cross2(clip_end - clip_start, current - clip_start) >= -EPS
            if current_inside:
                if not previous_inside:
                    output.append(line_intersection(previous, current, clip_start, clip_end))
                output.append(current)
            elif previous_inside:
                output.append(line_intersection(previous, current, clip_start, clip_end))
            previous = current
            previous_inside = current_inside
    return abs(polygon_area(output)) if len(output) >= 3 else 0.0


def overlap_pairs(triangles, grid_size=64):
    grid = defaultdict(list)
    candidates = set()
    for index, item in enumerate(triangles):
        points = item[2]
        minimum_x = max(0, min(grid_size - 1, int(math.floor(min(p.x for p in points) * grid_size))))
        maximum_x = max(0, min(grid_size - 1, int(math.floor(max(p.x for p in points) * grid_size))))
        minimum_y = max(0, min(grid_size - 1, int(math.floor(min(p.y for p in points) * grid_size))))
        maximum_y = max(0, min(grid_size - 1, int(math.floor(max(p.y for p in points) * grid_size))))
        for x in range(minimum_x, maximum_x + 1):
            for y in range(minimum_y, maximum_y + 1):
                for other in grid[(x, y)]:
                    if triangles[other][1] != item[1]:
                        candidates.add((min(index, other), max(index, other)))
                grid[(x, y)].append(index)
    overlaps = []
    for first, second in candidates:
        if triangle_overlap_area(triangles[first][2], triangles[second][2]) > 1.0e-11:
            overlaps.append((first, second))
    return overlaps


def island_axis_error(mesh, uv_layer, islands):
    values = []
    for faces in islands:
        loops = [loop for face in faces for loop in mesh.polygons[face].loop_indices]
        points = [uv_layer.data[loop].uv for loop in loops]
        if len(points) < 3:
            continue
        center = sum(points, Vector((0.0, 0.0))) / len(points)
        xx = yy = xy = 0.0
        for point in points:
            delta = point - center
            xx += delta.x * delta.x
            yy += delta.y * delta.y
            xy += delta.x * delta.y
        angle = abs(math.degrees(0.5 * math.atan2(2.0 * xy, xx - yy))) % 90.0
        values.append(min(angle, 90.0 - angle))
    return values


def inspect_mesh(obj):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return {"object": obj.name, "has_uv": False, "hard_failure": True}
    polygon_edges, edge_faces = topology(mesh)
    adjacency = defaultdict(list)
    hard_missing = 0
    uv_boundary_edges = 0
    soft_boundaries = 0
    for edge_index, linked in enumerate(edge_faces):
        continuous = edge_uv_continuous(mesh, uv_layer, edge_index, linked)
        if len(linked) == 2 and continuous:
            first, second = linked
            adjacency[first].append(second)
            adjacency[second].append(first)
        elif len(linked) == 2:
            uv_boundary_edges += 1
            if not mesh.edges[edge_index].use_edge_sharp:
                soft_boundaries += 1
        if mesh.edges[edge_index].use_edge_sharp and len(linked) == 2 and continuous:
            hard_missing += 1
    topological_islands = connected_components(range(len(mesh.polygons)), adjacency)
    virtual_edges = 0
    for first, second in virtual_split_edge_adjacency(mesh, uv_layer):
        if second not in adjacency[first]:
            adjacency[first].append(second)
            adjacency[second].append(first)
            virtual_edges += 1
    islands = connected_components(range(len(mesh.polygons)), adjacency)
    face_island = {
        face: island_index
        for island_index, faces in enumerate(islands)
        for face in faces
    }

    out_of_tile = 0
    flipped_faces = set()
    degenerate_faces = set()
    stretch = []
    density = []
    triangles = []
    for polygon in mesh.polygons:
        loops = list(polygon.loop_indices)
        for loop in loops:
            point = uv_layer.data[loop].uv
            if point.x < -1.0e-7 or point.y < -1.0e-7 or point.x > 1.0 + 1.0e-7 or point.y > 1.0 + 1.0e-7:
                out_of_tile += 1
        for index in range(1, len(loops) - 1):
            triangle_loops = (loops[0], loops[index], loops[index + 1])
            points_3d = [mesh.vertices[mesh.loops[loop].vertex_index].co for loop in triangle_loops]
            points_uv = [uv_layer.data[loop].uv.copy() for loop in triangle_loops]
            uv_cross = cross2(points_uv[1] - points_uv[0], points_uv[2] - points_uv[0])
            area_uv = abs(uv_cross) * 0.5
            area_3d = (points_3d[1] - points_3d[0]).cross(points_3d[2] - points_3d[0]).length * 0.5
            if area_uv <= EPS or area_3d <= EPS:
                degenerate_faces.add(polygon.index)
            elif uv_cross < 0.0:
                flipped_faces.add(polygon.index)
            ratio = triangle_stretch(*points_3d, *points_uv)
            if ratio is not None:
                stretch.append(ratio)
            if area_uv > EPS and area_3d > EPS:
                density.append(math.sqrt(area_uv / area_3d))
            triangles.append((polygon.index, face_island[polygon.index], points_uv))

    overlap = overlap_pairs(triangles)
    density_median = percentile(density, 0.5)
    normalized_density = [value / density_median for value in density] if density_median else []
    axis_errors = island_axis_error(mesh, uv_layer, islands)
    return {
        "object": obj.name,
        "has_uv": True,
        "vertices": len(mesh.vertices),
        "polygons": len(mesh.polygons),
        "uv_layer": uv_layer.name,
        "uv_islands": len(islands),
        "topological_uv_islands": len(topological_islands),
        "virtual_welded_uv_edges": virtual_edges,
        "out_of_0_1_loops": out_of_tile,
        "hard_edges": sum(edge.use_edge_sharp for edge in mesh.edges),
        "hard_edges_not_uv_boundary": hard_missing,
        "uv_boundary_edges": uv_boundary_edges,
        "soft_uv_boundary_edges": soft_boundaries,
        "flipped_faces": len(flipped_faces),
        "degenerate_uv_faces": len(degenerate_faces),
        "overlap_triangle_pairs": len(overlap),
        "stretch_p50": round(percentile(stretch, 0.50), 5) if stretch else None,
        "stretch_p90": round(percentile(stretch, 0.90), 5) if stretch else None,
        "stretch_p95": round(percentile(stretch, 0.95), 5) if stretch else None,
        "stretch_p99": round(percentile(stretch, 0.99), 5) if stretch else None,
        "stretch_max": round(max(stretch), 5) if stretch else None,
        "texel_density_relative_p10": round(percentile(normalized_density, 0.10), 5) if normalized_density else None,
        "texel_density_relative_p90": round(percentile(normalized_density, 0.90), 5) if normalized_density else None,
        "axis_alignment_error_degrees_p90": round(percentile(axis_errors, 0.90), 5) if axis_errors else None,
    }


def main():
    args = parse_args()
    source = os.path.abspath(args.input)
    hard_edge_check_authoritative = (
        os.path.splitext(source)[1].lower() == ".blend"
        or args.enforce_imported_hard_edges
    )
    output = os.path.abspath(args.output or os.path.splitext(source)[0] + "_QA.json")
    open_source(source)
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    seen_meshes = set()
    reports = []
    for obj in mesh_objects:
        pointer = obj.data.as_pointer()
        if pointer in seen_meshes:
            continue
        seen_meshes.add(pointer)
        reports.append(inspect_mesh(obj))

    hard_failures = []
    warnings = []
    for report in reports:
        if not report.get("has_uv"):
            hard_failures.append(f"{report['object']}: missing UV")
            continue
        for key in ("out_of_0_1_loops", "flipped_faces", "degenerate_uv_faces"):
            if report[key]:
                hard_failures.append(f"{report['object']}: {key}={report[key]}")
        if hard_edge_check_authoritative and report["hard_edges_not_uv_boundary"]:
            hard_failures.append(
                f"{report['object']}: hard_edges_not_uv_boundary={report['hard_edges_not_uv_boundary']}"
            )
        if report["overlap_triangle_pairs"] and not args.allow_overlap:
            hard_failures.append(f"{report['object']}: overlap_triangle_pairs={report['overlap_triangle_pairs']}")
        if args.require_max_compatible_shells and (
            report["virtual_welded_uv_edges"]
            or report["topological_uv_islands"] != report["uv_islands"]
        ):
            hard_failures.append(
                f"{report['object']}: max_incompatible_split_uv_shells="
                f"{report['topological_uv_islands']} visual_uv_shells={report['uv_islands']} "
                f"virtual_welded_uv_edges={report['virtual_welded_uv_edges']}"
            )
        if report["stretch_p90"] is not None and report["stretch_p90"] > args.stretch_p90:
            warnings.append(f"{report['object']}: stretch_p90={report['stretch_p90']}")
        if report["stretch_p95"] is not None and report["stretch_p95"] > args.stretch_p95:
            warnings.append(f"{report['object']}: stretch_p95={report['stretch_p95']}")

    result = {
        "source": source,
        "mesh_objects": len(mesh_objects),
        "unique_mesh_datablocks": len(seen_meshes),
        "hard_edge_check_authoritative": hard_edge_check_authoritative,
        "reports": reports,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "passed": not hard_failures,
    }
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print("BLENDER_PBR_UV_QA_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("BLENDER_PBR_UV_QA_END")
    if args.strict and hard_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
