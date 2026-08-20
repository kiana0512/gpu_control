"""One-pass PBR UV unwrap for Blender 4.3+ / 5.x.

Run with Blender:
  blender --background --python unwrap_fbx.py -- --input model.fbx
"""

import argparse
import heapq
import json
import math
import os
import sys
from collections import defaultdict, deque

import bmesh
import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-blend")
    parser.add_argument("--output-fbx")
    parser.add_argument("--output-report")
    parser.add_argument("--hard-angle", type=float, default=75.0)
    parser.add_argument("--planar-angle", type=float, default=1.0)
    parser.add_argument("--hidden-axis", choices=("x+", "x-", "y+", "y-", "z+", "z-"), default="y+")
    parser.add_argument("--padding-px", type=float, default=10.0)
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--repair-stretch", type=float, default=6.0)
    parser.add_argument("--no-angle-sharps", action="store_true")
    parser.add_argument("--no-share-identical-objects", action="store_true")
    parser.add_argument("--no-export-fbx", action="store_true")
    return parser.parse_args(argv)


def derived_paths(args):
    source = os.path.abspath(args.input)
    directory = os.path.dirname(source)
    stem = os.path.splitext(os.path.basename(source))[0]
    blend = os.path.abspath(args.output_blend or os.path.join(directory, stem + "_PBR_UV.blend"))
    fbx = None if args.no_export_fbx else os.path.abspath(args.output_fbx or os.path.join(directory, stem + "_PBR_UV.fbx"))
    report = os.path.abspath(args.output_report or os.path.splitext(blend)[0] + "_report.json")
    return source, blend, fbx, report


def import_source(path):
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


def topology(mesh):
    edge_by_vertices = {tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges}
    polygon_edges = []
    edge_faces = [[] for _ in mesh.edges]
    vertex_edges = defaultdict(list)
    for edge in mesh.edges:
        for vertex in edge.vertices:
            vertex_edges[vertex].append(edge.index)
    for polygon in mesh.polygons:
        edges = []
        for index, vertex in enumerate(polygon.vertices):
            other = polygon.vertices[(index + 1) % len(polygon.vertices)]
            edge_index = edge_by_vertices[tuple(sorted((vertex, other)))]
            edges.append(edge_index)
            edge_faces[edge_index].append(polygon.index)
        polygon_edges.append(edges)
    return polygon_edges, edge_faces, vertex_edges


def triangulate_nonplanar_faces(obj):
    """Triangulate only warped n-gons; preserve planar authoring topology."""
    mesh = obj.data
    edit_mesh = bmesh.new()
    edit_mesh.from_mesh(mesh)
    edit_mesh.faces.ensure_lookup_table()
    edit_mesh.normal_update()
    targets = []
    for face in edit_mesh.faces:
        if len(face.verts) <= 3:
            continue
        center = face.calc_center_median()
        size = max(math.sqrt(max(face.calc_area(), 1.0e-16)), 1.0e-8)
        nonplanarity = max(abs((vertex.co - center).dot(face.normal)) / size for vertex in face.verts)
        if nonplanarity > 0.0001:
            targets.append(face)
    if targets:
        bmesh.ops.triangulate(edit_mesh, faces=targets, quad_method="BEAUTY", ngon_method="BEAUTY")
    count = len(targets)
    edit_mesh.to_mesh(mesh)
    edit_mesh.free()
    mesh.update()
    return count


def loose_face_regions(mesh):
    """Return face groups connected through a shared mesh edge."""
    _, edge_faces, _ = topology(mesh)
    adjacency = defaultdict(list)
    for linked in edge_faces:
        if len(linked) == 2:
            first, second = linked
            adjacency[first].append(second)
            adjacency[second].append(first)
    return components(range(len(mesh.polygons)), adjacency)


def repair_split_vertex_export(obj):
    """Rejoin proven FBX export splits in the delivered Max-compatible mesh.

    The source file remains untouched.  This conservative repair is restricted
    to exact-position duplicate vertices whose removal preserves every polygon
    and loop, creates no degenerate face or non-manifold edge, and reconnects a
    severely fragmented single-material mesh.  The repaired mesh is the UV and
    delivery mesh so DCC applications see real connected UV shells.
    """
    mesh = obj.data
    before_regions = len(loose_face_regions(mesh))
    report = {
        "applied": False,
        "reason": "not_fragmented",
        "vertices_before": len(mesh.vertices),
        "vertices_after": len(mesh.vertices),
        "polygons_before": len(mesh.polygons),
        "polygons_after": len(mesh.polygons),
        "loose_regions_before": before_regions,
        "loose_regions_after": before_regions,
        "welded_vertices": 0,
        "max_compatible_delivery": False,
    }
    if len(mesh.polygons) < 8 or before_regions < 8:
        return report
    if len(mesh.materials) > 1 or mesh.shape_keys is not None:
        report["reason"] = "multi_material_or_shape_keys"
        return report

    minimum = Vector([min(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)])
    maximum = Vector([max(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)])
    distance = max((maximum - minimum).length, 1.0) * 1.0e-7
    repaired = mesh.copy()
    edit_mesh = bmesh.new()
    edit_mesh.from_mesh(repaired)
    bmesh.ops.remove_doubles(edit_mesh, verts=list(edit_mesh.verts), dist=distance)
    edit_mesh.to_mesh(repaired)
    edit_mesh.free()
    repaired.update()

    welded = len(mesh.vertices) - len(repaired.vertices)
    after_regions = len(loose_face_regions(repaired))
    _, repaired_edge_faces, _ = topology(repaired)
    invalid_faces = sum(
        len(polygon.vertices) < 3 or polygon.area <= 1.0e-14
        for polygon in repaired.polygons
    )
    nonmanifold_edges = sum(len(linked) > 2 for linked in repaired_edge_faces)
    worthwhile = welded >= max(8, int(len(mesh.vertices) * 0.10))
    reconnects = after_regions <= max(8, int(before_regions * 0.25))
    safe = (
        len(repaired.polygons) == len(mesh.polygons)
        and len(repaired.loops) == len(mesh.loops)
        and invalid_faces == 0
        and nonmanifold_edges == 0
    )
    if not (worthwhile and reconnects and safe):
        report.update({
            "reason": "weld_safety_gate_rejected",
            "candidate_welded_vertices": welded,
            "candidate_loose_regions_after": after_regions,
            "candidate_invalid_faces": invalid_faces,
            "candidate_nonmanifold_edges": nonmanifold_edges,
        })
        bpy.data.meshes.remove(repaired)
        return report

    original_flat_faces = sum(not polygon.use_smooth for polygon in repaired.polygons)
    for polygon in repaired.polygons:
        polygon.use_smooth = True
    repaired.name = mesh.name
    obj.data = repaired
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    report.update({
        "applied": True,
        "reason": "split_vertex_export_repaired",
        "vertices_after": len(repaired.vertices),
        "polygons_after": len(repaired.polygons),
        "loose_regions_after": after_regions,
        "welded_vertices": welded,
        "rebuilt_smooth_faces": original_flat_faces,
        "weld_distance": distance,
        "max_compatible_delivery": True,
    })
    return report


def components(nodes, adjacency):
    seen = set()
    groups = []
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
        groups.append(group)
    return groups


def mark_hard_edges(mesh, edge_faces, hard_angle, add_angle_sharps):
    imported = [edge.use_edge_sharp for edge in mesh.edges]
    added = cleaned = 0
    for edge_index, edge in enumerate(mesh.edges):
        sharp = False
        linked = edge_faces[edge_index]
        if len(linked) == 2:
            first, second = linked
            dot = max(-1.0, min(1.0, mesh.polygons[first].normal.dot(mesh.polygons[second].normal)))
            angle = math.acos(dot)
            imported_sharp = imported[edge_index] and angle > math.radians(1.0)
            angle_sharp = add_angle_sharps and angle >= math.radians(hard_angle)
            sharp = imported_sharp or angle_sharp
            cleaned += int(imported[edge_index] and not sharp)
            added += int(angle_sharp and not imported[edge_index])
        edge.use_edge_sharp = sharp
        edge.use_seam = edge.use_seam or sharp
    mesh.update()
    return {
        "cleaned_coplanar_sharps": cleaned,
        "added_angle_sharps": added,
        "hard_edges": sum(edge.use_edge_sharp for edge in mesh.edges),
    }


def hidden_axis_value(point, spec):
    index = "xyz".index(spec[0])
    sign = 1.0 if spec[1] == "+" else -1.0
    return sign * point[index]


def boundary_vertex_groups(mesh, boundary_edges):
    adjacency = defaultdict(list)
    vertices = set()
    for edge_index in boundary_edges:
        first, second = mesh.edges[edge_index].vertices
        adjacency[first].append(second)
        adjacency[second].append(first)
        vertices.update((first, second))
    return [set(group) for group in components(vertices, adjacency)]


def shortest_hidden_path(mesh, allowed_edges, sources, targets, hidden_axis, preferred_direction=None):
    allowed_edges = [edge for edge in allowed_edges if not mesh.edges[edge].use_seam]
    if not allowed_edges:
        return []
    adjacency = defaultdict(list)
    allowed_vertices = set()
    values = []
    for edge_index in allowed_edges:
        first, second = mesh.edges[edge_index].vertices
        adjacency[first].append((second, edge_index))
        adjacency[second].append((first, edge_index))
        allowed_vertices.update((first, second))
        values.extend((hidden_axis_value(mesh.vertices[first].co, hidden_axis), hidden_axis_value(mesh.vertices[second].co, hidden_axis)))
    sources = set(sources) & allowed_vertices
    targets = set(targets) & allowed_vertices
    if not sources or not targets:
        return []
    minimum, maximum = min(values), max(values)
    span = max(maximum - minimum, 1.0e-8)
    preferred = preferred_direction.normalized() if preferred_direction and preferred_direction.length_squared > 1.0e-16 else None
    distance = {}
    previous = {}
    heap = []
    for source in sources:
        distance[source] = 0.0
        heapq.heappush(heap, (0.0, source))
    end = None
    while heap:
        current_distance, vertex = heapq.heappop(heap)
        if current_distance != distance.get(vertex):
            continue
        if vertex in targets:
            end = vertex
            break
        for other, edge_index in adjacency.get(vertex, ()):
            p0 = mesh.vertices[vertex].co
            p1 = mesh.vertices[other].co
            midpoint = (p0 + p1) * 0.5
            hidden_score = (hidden_axis_value(midpoint, hidden_axis) - minimum) / span
            hidden_penalty = 0.25 + 3.0 * (1.0 - hidden_score) ** 2
            direction_penalty = 1.0
            edge_vector = p1 - p0
            if preferred and edge_vector.length_squared > 1.0e-16:
                alignment = abs(edge_vector.normalized().dot(preferred))
                direction_penalty = 0.2 + 4.0 * (1.0 - alignment) ** 2
            cost = max(edge_vector.length, 1.0e-8) * hidden_penalty * direction_penalty
            candidate = current_distance + cost
            if candidate < distance.get(other, float("inf")):
                distance[other] = candidate
                previous[other] = (vertex, edge_index)
                heapq.heappush(heap, (candidate, other))
    if end is None:
        return []
    path = []
    while end not in sources:
        end, edge_index = previous[end]
        path.append(edge_index)
    path.reverse()
    return path


def add_topology_cuts(mesh, polygon_edges, edge_faces, hidden_axis):
    adjacency = defaultdict(list)
    for edge_index, linked in enumerate(edge_faces):
        if len(linked) == 2 and not mesh.edges[edge_index].use_seam:
            first, second = linked
            adjacency[first].append(second)
            adjacency[second].append(first)
    regions = components(range(len(mesh.polygons)), adjacency)
    added = 0
    annular = closed = 0
    for faces in regions:
        face_set = set(faces)
        region_edges = {edge for face in faces for edge in polygon_edges[face]}
        boundary_edges = []
        for edge_index in region_edges:
            inside = sum(face in face_set for face in edge_faces[edge_index])
            if inside == 1:
                boundary_edges.append(edge_index)
        groups = boundary_vertex_groups(mesh, boundary_edges)
        vertices = sorted({vertex for face in faces for vertex in mesh.polygons[face].vertices})
        if len(groups) >= 2:
            annular += 1
            anchor = groups[0]
            anchor_center = sum((mesh.vertices[v].co for v in anchor), Vector()) / len(anchor)
            for target in groups[1:]:
                target_center = sum((mesh.vertices[v].co for v in target), Vector()) / len(target)
                path = shortest_hidden_path(
                    mesh,
                    region_edges,
                    anchor,
                    target,
                    hidden_axis,
                    target_center - anchor_center,
                )
                for edge_index in path:
                    if not mesh.edges[edge_index].use_seam:
                        mesh.edges[edge_index].use_seam = True
                        added += 1
                anchor |= target
        elif not groups and len(faces) > 4 and len(vertices) > 4:
            closed += 1
            points = [mesh.vertices[v].co for v in vertices]
            spans = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
            axis = max(range(3), key=lambda i: spans[i])
            start = min(vertices, key=lambda v: mesh.vertices[v].co[axis])
            end = max(vertices, key=lambda v: mesh.vertices[v].co[axis])
            direction = Vector((1.0 if axis == 0 else 0.0, 1.0 if axis == 1 else 0.0, 1.0 if axis == 2 else 0.0))
            path = shortest_hidden_path(mesh, region_edges, {start}, {end}, hidden_axis, direction)
            for edge_index in path:
                if not mesh.edges[edge_index].use_seam:
                    mesh.edges[edge_index].use_seam = True
                    added += 1
    mesh.update()
    return {"annular_regions": annular, "closed_regions": closed, "added_topology_seams": added}


def activate_object(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def unwrap(obj):
    activate_object(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    unwrap_args = dict(
        method="CONFORMAL",
        fill_holes=True,
        correct_aspect=True,
        margin_method="FRACTION",
        margin=0.0,
    )
    # These solver controls were added after Blender 4.2.
    if bpy.app.version >= (4, 3, 0):
        unwrap_args.update(no_flip=True, iterations=30)
    result = bpy.ops.uv.unwrap(**unwrap_args)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.data.update()
    return sorted(result)


def average_scale(obj):
    activate_object(obj)
    previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    result = bpy.ops.uv.average_islands_scale(shear=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync
    obj.data.update()
    return sorted(result)


def seam_islands(mesh, edge_faces):
    adjacency = defaultdict(list)
    for edge_index, linked in enumerate(edge_faces):
        if len(linked) == 2 and not mesh.edges[edge_index].use_seam:
            first, second = linked
            adjacency[first].append(second)
            adjacency[second].append(first)
    return components(range(len(mesh.polygons)), adjacency)


def project_planar_islands(obj, planar_angle):
    mesh = obj.data
    polygon_edges, edge_faces, _ = topology(mesh)
    uv = mesh.uv_layers.active
    cosine_limit = math.cos(math.radians(planar_angle))
    groups = faces_total = 0
    for faces in seam_islands(mesh, edge_faces):
        reference = mesh.polygons[faces[0]].normal.normalized()
        if any(reference.dot(mesh.polygons[face].normal.normalized()) < cosine_limit for face in faces):
            continue
        average_normal = sum((mesh.polygons[face].normal for face in faces), Vector())
        if average_normal.length_squared <= 1.0e-16:
            continue
        average_normal.normalize()
        vertices = sorted({vertex for face in faces for vertex in mesh.polygons[face].vertices})
        origin = mesh.vertices[vertices[0]].co.copy()
        axis = None
        best_length = 0.0
        for face in faces:
            for edge_index in polygon_edges[face]:
                edge = mesh.edges[edge_index]
                candidate = mesh.vertices[edge.vertices[1]].co - mesh.vertices[edge.vertices[0]].co
                candidate -= average_normal * candidate.dot(average_normal)
                if candidate.length_squared > best_length:
                    best_length = candidate.length_squared
                    axis = candidate
        if axis is None or axis.length_squared <= 1.0e-16:
            continue
        u_axis = axis.normalized()
        v_axis = average_normal.cross(u_axis).normalized()
        for face in faces:
            for loop_index in mesh.polygons[face].loop_indices:
                point = mesh.vertices[mesh.loops[loop_index].vertex_index].co
                delta = point - origin
                uv.data[loop_index].uv = (delta.dot(u_axis), delta.dot(v_axis))
        groups += 1
        faces_total += len(faces)
    mesh.update()
    if groups:
        average_scale(obj)
    return {"groups": groups, "faces": faces_total}


def triangle_stretch(p0, p1, p2, uv0, uv1, uv2):
    edge1 = p1 - p0
    edge2 = p2 - p0
    length1 = edge1.length
    if length1 <= 1.0e-12:
        return None
    axis = edge1 / length1
    x2 = edge2.dot(axis)
    y2_squared = edge2.length_squared - x2 * x2
    if y2_squared <= 1.0e-12:
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


def face_stretch(mesh):
    uv = mesh.uv_layers.active
    stretch = {}
    signed_areas = {}
    for polygon in mesh.polygons:
        loops = list(polygon.loop_indices)
        points = [uv.data[loop].uv for loop in loops]
        signed_areas[polygon.index] = 0.5 * sum(
            point.x * points[(index + 1) % len(points)].y
            - points[(index + 1) % len(points)].x * point.y
            for index, point in enumerate(points)
        )
        maximum = 1.0
        valid = False
        for index in range(1, len(loops) - 1):
            triangle_loops = (loops[0], loops[index], loops[index + 1])
            positions = [mesh.vertices[mesh.loops[loop].vertex_index].co for loop in triangle_loops]
            uvs = [uv.data[loop].uv for loop in triangle_loops]
            ratio = triangle_stretch(*positions, *uvs)
            if ratio is not None and math.isfinite(ratio):
                maximum = max(maximum, ratio)
                valid = True
        stretch[polygon.index] = maximum if valid else float("inf")
    return stretch, signed_areas


def add_distortion_region_seams(mesh, threshold):
    stretch, signed_areas = face_stretch(mesh)
    bad = {
        face
        for face, ratio in stretch.items()
        if ratio > threshold or signed_areas[face] <= 1.0e-12
    }
    _, edge_faces, _ = topology(mesh)
    added = 0
    for edge_index, linked in enumerate(edge_faces):
        if len(linked) != 2:
            continue
        if (linked[0] in bad) != (linked[1] in bad) and not mesh.edges[edge_index].use_seam:
            mesh.edges[edge_index].use_seam = True
            added += 1
    mesh.update()
    return {"threshold": threshold, "bad_faces": len(bad), "added_seams": added}


def project_remaining_bad_faces(obj, threshold):
    mesh = obj.data
    activate_object(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    edit_mesh = bmesh.from_edit_mesh(mesh)
    edit_mesh.faces.ensure_lookup_table()
    edit_mesh.normal_update()
    uv_layer = edit_mesh.loops.layers.uv.active
    bad_faces = set()
    repaired_flips = repaired_stretch = 0
    for face in edit_mesh.faces:
        loops = list(face.loops)
        points = [loop[uv_layer].uv for loop in loops]
        signed_area = 0.5 * sum(
            point.x * points[(index + 1) % len(points)].y
            - points[(index + 1) % len(points)].x * point.y
            for index, point in enumerate(points)
        )
        maximum = 1.0
        valid = False
        for index in range(1, len(loops) - 1):
            ratio = triangle_stretch(
                loops[0].vert.co,
                loops[index].vert.co,
                loops[index + 1].vert.co,
                loops[0][uv_layer].uv,
                loops[index][uv_layer].uv,
                loops[index + 1][uv_layer].uv,
            )
            if ratio is not None and math.isfinite(ratio):
                maximum = max(maximum, ratio)
                valid = True
        if signed_area <= 1.0e-12 or not valid or maximum > threshold:
            bad_faces.add(face)
            repaired_flips += int(signed_area < -1.0e-12)
            repaired_stretch += int(maximum > threshold)

    adjacency = defaultdict(list)
    cosine = math.cos(math.radians(1.0))
    for face in bad_faces:
        for edge in face.edges:
            for neighbor in edge.link_faces:
                if neighbor is not face and neighbor in bad_faces and face.normal.dot(neighbor.normal) >= cosine and edge.smooth:
                    adjacency[face.index].append(neighbor.index)
    by_index = {face.index: face for face in bad_faces}
    groups = components(sorted(by_index), adjacency)
    for indices in groups:
        group = [by_index[index] for index in indices]
        group_set = set(group)
        for face in group:
            for edge in face.edges:
                internal = len(edge.link_faces) == 2 and all(linked in group_set for linked in edge.link_faces)
                if internal and edge.smooth:
                    edge.seam = False
                elif not internal:
                    edge.seam = True
        loops = [loop for face in group for loop in face.loops]
        origin = loops[0].vert.co.copy()
        axis = next((loop.vert.co - origin for loop in loops[1:] if (loop.vert.co - origin).length_squared > 1.0e-20), Vector((1.0, 0.0, 0.0)))
        axis.normalize()
        normal = sum((face.normal for face in group), Vector())
        normal = normal.normalized() if normal.length_squared > 1.0e-20 else group[0].normal.copy()
        v_axis = normal.cross(axis)
        v_axis = v_axis.normalized() if v_axis.length_squared > 1.0e-20 else Vector((0.0, 1.0, 0.0))
        for loop in loops:
            delta = loop.vert.co - origin
            loop[uv_layer].uv = (delta.dot(axis), delta.dot(v_axis))
    bmesh.update_edit_mesh(mesh)
    bpy.ops.uv.average_islands_scale(shear=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh.update()
    return {
        "repaired_faces": len(bad_faces),
        "repaired_flips": repaired_flips,
        "repaired_stretch": repaired_stretch,
        "projected_groups": len(groups),
    }


def orient_and_separate(obj):
    mesh = obj.data
    _, edge_faces, _ = topology(mesh)
    uv = mesh.uv_layers.active
    islands = seam_islands(mesh, edge_faces)
    island_loops = []
    bounds = []
    maximum_extent = 1.0
    for faces in islands:
        loops = [loop for face in faces for loop in mesh.polygons[face].loop_indices]
        island_loops.append(loops)
        points = [uv.data[loop].uv.copy() for loop in loops]
        center = sum(points, Vector((0.0, 0.0))) / len(points)
        xx = yy = xy = 0.0
        for point in points:
            delta = point - center
            xx += delta.x * delta.x
            yy += delta.y * delta.y
            xy += delta.x * delta.y
        angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
        cosine, sine = math.cos(-angle), math.sin(-angle)
        for loop in loops:
            delta = uv.data[loop].uv - center
            uv.data[loop].uv = (delta.x * cosine - delta.y * sine, delta.x * sine + delta.y * cosine)
        points = [uv.data[loop].uv for loop in loops]
        box = (min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points))
        bounds.append(box)
        maximum_extent = max(maximum_extent, box[2] - box[0], box[3] - box[1])
    columns = max(1, math.ceil(math.sqrt(len(islands))))
    cell = maximum_extent * 1.5
    for index, loops in enumerate(island_loops):
        box = bounds[index]
        offset = Vector(((index % columns) * cell - box[0], (index // columns) * cell - box[1]))
        for loop in loops:
            uv.data[loop].uv += offset
    mesh.update()
    return len(islands)


def face_vertex_uv(mesh, uv_layer, face_index, vertex_index):
    for loop_index in mesh.polygons[face_index].loop_indices:
        if mesh.loops[loop_index].vertex_index == vertex_index:
            return uv_layer.data[loop_index].uv
    raise RuntimeError("Vertex not found in face")


def edge_uv_continuous(mesh, uv_layer, edge_index, linked):
    if len(linked) != 2:
        return False
    for vertex in mesh.edges[edge_index].vertices:
        first = face_vertex_uv(mesh, uv_layer, linked[0], vertex)
        second = face_vertex_uv(mesh, uv_layer, linked[1], vertex)
        if (first - second).length > 1.0e-7:
            return False
    return True


def ensure_hard_boundaries(mesh):
    """Close partial hard-edge cuts that planar projection can weld locally."""
    polygon_edges, edge_faces, _ = topology(mesh)
    uv_layer = mesh.uv_layers.active
    failed = [
        edge_index
        for edge_index, linked in enumerate(edge_faces)
        if mesh.edges[edge_index].use_edge_sharp
        and edge_uv_continuous(mesh, uv_layer, edge_index, linked)
    ]
    added = 0
    isolated_faces = set()
    for edge_index in failed:
        linked = edge_faces[edge_index]
        if len(linked) != 2:
            continue
        face = min(linked, key=lambda index: len(mesh.polygons[index].vertices))
        isolated_faces.add(face)
        for face_edge in polygon_edges[face]:
            if not mesh.edges[face_edge].use_seam:
                mesh.edges[face_edge].use_seam = True
                added += 1
    mesh.update()
    return {
        "hard_edges_welded_before_guard": len(failed),
        "isolated_guard_faces": len(isolated_faces),
        "added_guard_seams": added,
    }


def pack(obj, padding_px, resolution):
    activate_object(obj)
    previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    result = bpy.ops.uv.pack_islands(
        udim_source="CLOSEST_UDIM",
        rotate=True,
        rotate_method="CARDINAL",
        scale=True,
        merge_overlap=False,
        margin_method="ADD",
        margin=padding_px / (2.0 * resolution),
        shape_method="CONCAVE",
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync
    obj.data.update()
    return sorted(result)


def mesh_signature(obj):
    mesh = obj.data
    return (
        tuple((round(v.co.x, 6), round(v.co.y, 6), round(v.co.z, 6)) for v in mesh.vertices),
        tuple((tuple(p.vertices), p.material_index) for p in mesh.polygons),
        tuple(material.name if material else "" for material in mesh.materials),
    )


def ensure_uv(mesh):
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
    layer = mesh.uv_layers.new(name="UVChannel_1")
    mesh.uv_layers.active = layer
    layer.active_render = True


def export_fbx(path, mesh_objects):
    # FBX does not reliably serialize linked mesh datablocks when the objects
    # carry different object-level material-slot bindings.  Export temporary
    # mesh copies, then restore the linked meshes kept in the .blend file.
    original_meshes = {}
    temporary_meshes = []
    seen = set()
    for obj in mesh_objects:
        pointer = obj.data.as_pointer()
        if pointer in seen:
            original_meshes[obj] = obj.data
            export_mesh = obj.data.copy()
            temporary_meshes.append(export_mesh)
            obj.data = export_mesh
        else:
            seen.add(pointer)
        for slot in obj.material_slots:
            slot.link = "DATA"
    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=True,
        use_visible=False,
        object_types={"MESH"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_NONE",
        use_mesh_modifiers=False,
        mesh_smooth_type="OFF",
        use_tspace=False,
        use_triangles=False,
        add_leaf_bones=False,
        bake_anim=False,
        axis_forward="-Z",
        axis_up="Y",
    )
    for obj, mesh in original_meshes.items():
        obj.data = mesh
    for mesh in temporary_meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def main():
    args = parse_args()
    source, output_blend, output_fbx, output_report = derived_paths(args)
    import_source(source)
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("No mesh objects found")

    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    signature_groups = defaultdict(list)
    for obj in mesh_objects:
        signature_groups[mesh_signature(obj)].append(obj)
    representatives = [group[0] for group in signature_groups.values()]

    object_reports = []
    for obj in representatives:
        split_vertex_repair = repair_split_vertex_export(obj)
        mesh = obj.data
        triangulated = triangulate_nonplanar_faces(obj)
        polygon_edges, edge_faces, _ = topology(mesh)
        shading = mark_hard_edges(mesh, edge_faces, args.hard_angle, not args.no_angle_sharps)
        cuts = add_topology_cuts(mesh, polygon_edges, edge_faces, args.hidden_axis)
        ensure_uv(mesh)
        unwrap_result = unwrap(obj)
        planar = [project_planar_islands(obj, args.planar_angle)]
        refinement = add_distortion_region_seams(mesh, args.repair_stretch)
        if refinement["added_seams"]:
            unwrap_result = unwrap(obj)
            planar.append(project_planar_islands(obj, args.planar_angle))
        repair = project_remaining_bad_faces(obj, args.repair_stretch)
        planar.append(project_planar_islands(obj, args.planar_angle))
        island_count = orient_and_separate(obj)
        hard_boundary_guard = ensure_hard_boundaries(mesh)
        if hard_boundary_guard["added_guard_seams"]:
            island_count = orient_and_separate(obj)
        average_scale_result = average_scale(obj)
        pack_result = pack(obj, args.padding_px, args.resolution)
        object_reports.append({
            "object": obj.name,
            "vertices": len(mesh.vertices),
            "polygons": len(mesh.polygons),
            "split_vertex_repair": split_vertex_repair,
            "triangulated_nonplanar_faces": triangulated,
            "uv_islands": island_count,
            "shading": shading,
            "topology_cuts": cuts,
            "planar_projection": planar,
            "refinement": refinement,
            "repair": repair,
            "hard_boundary_guard": hard_boundary_guard,
            "unwrap": unwrap_result,
            "average_scale": average_scale_result,
            "pack": pack_result,
        })

    shared_objects = 0
    if not args.no_share_identical_objects:
        for group in signature_groups.values():
            representative = group[0]
            for duplicate in group[1:]:
                duplicate.data = representative.data
                for slot in duplicate.material_slots:
                    slot.link = "DATA"
                shared_objects += 1
            for slot in representative.material_slots:
                slot.link = "DATA"

    os.makedirs(os.path.dirname(output_blend), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output_blend)
    if output_fbx:
        os.makedirs(os.path.dirname(output_fbx), exist_ok=True)
        export_fbx(output_fbx, mesh_objects)

    report = {
        "source": source,
        "output_blend": output_blend,
        "output_fbx": output_fbx,
        "mesh_objects": len(mesh_objects),
        "processed_unique_geometries": len(representatives),
        "shared_identical_objects": shared_objects,
        "settings": {
            "hard_angle": args.hard_angle,
            "hidden_axis": args.hidden_axis,
            "padding_px": args.padding_px,
            "resolution": args.resolution,
        },
        "objects": object_reports,
    }
    os.makedirs(os.path.dirname(output_report), exist_ok=True)
    with open(output_report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("BLENDER_PBR_UV_REPORT_BEGIN")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("BLENDER_PBR_UV_REPORT_END")


if __name__ == "__main__":
    main()
