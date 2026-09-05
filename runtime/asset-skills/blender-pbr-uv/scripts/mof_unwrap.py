"""MOF UV unwrap for one or more Blender meshes with loose-part safety.

Run with Blender:
  blender --background --python mof_unwrap.py -- \
    --input model.blend --output-blend model_MOF_UV.blend \
    --output-fbx model_MOF_UV.fbx
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import time
from collections import defaultdict, deque

import bmesh
import bpy

EPS = 1.0e-10


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-blend", required=True)
    parser.add_argument("--output-fbx")
    parser.add_argument("--backup")
    parser.add_argument("--report")
    parser.add_argument("--object")
    parser.add_argument("--uv-name", default="MOF_UV")
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--padding-px", type=int, default=8)
    parser.add_argument("--relax-iterations", type=int, default=100)
    return parser.parse_args(argv)


def unique_path(path):
    if not os.path.exists(path):
        return path
    stem, extension = os.path.splitext(path)
    index = 2
    while True:
        candidate = f"{stem}_{index}{extension}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def clear_scene_keep_addons():
    """Remove scene content without unloading enabled add-ons.

    ``read_factory_settings`` also clears MOF's registered operator and scene
    properties in Blender 5.2.  A fresh background process only needs its
    default scene content removed before importing an interchange file.
    """
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.update()


def open_source(path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    clear_scene_keep_addons()
    if extension == ".fbx":
        if bpy.app.version >= (4, 3, 0):
            bpy.ops.wm.fbx_import(filepath=path)
        else:
            bpy.ops.import_scene.fbx(filepath=path)
    elif extension == ".obj":
        if bpy.app.version >= (4, 0, 0):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported input format: {extension}")


def activate(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def choose_targets(name):
    meshes = sorted(
        [obj for obj in bpy.context.scene.objects if obj.type == "MESH"],
        key=lambda item: item.name,
    )
    if name:
        matches = [obj for obj in meshes if obj.name == name]
        if len(matches) != 1:
            raise RuntimeError(f"Mesh object '{name}' was not found exactly once")
        targets = matches
    else:
        targets = [obj for obj in meshes if obj.data.polygons]
    if not targets:
        raise RuntimeError("The source contains no face-bearing mesh object")
    return targets, meshes


def reset_uv(mesh, name):
    existing = mesh.uv_layers.get(name)
    if existing is not None:
        mesh.uv_layers.remove(existing)
    layer = mesh.uv_layers.new(name=name)
    mesh.uv_layers.active = layer
    layer.active_render = True
    return layer


def ensure_uv(mesh, name):
    layer = mesh.uv_layers.get(name)
    if layer is None:
        layer = mesh.uv_layers.new(name=name)
    mesh.uv_layers.active = layer
    layer.active_render = True
    return layer


def usable_uv(obj, name):
    if not obj.data.polygons or name not in obj.data.uv_layers:
        return False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.get(name)
    total = 0.0
    if uv_layer is not None:
        for face in bm.faces:
            loops = list(face.loops)
            for index in range(1, len(loops) - 1):
                a = loops[0][uv_layer].uv
                b = loops[index][uv_layer].uv
                c = loops[index + 1][uv_layer].uv
                total += abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) * 0.5
    bm.free()
    return total > 1.0e-12


def digest_values(values):
    digest = hashlib.sha256()
    for value in sorted(values):
        if isinstance(value, tuple):
            digest.update(struct.pack(f"<{len(value)}d", *value))
        else:
            digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def geometry_record(obj):
    mesh = obj.data
    return {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "vertex_digest": digest_values(
            [tuple(round(component, 8) for component in vertex.co) for vertex in mesh.vertices]
        ),
        "edge_length_digest": digest_values(
            [round((mesh.vertices[edge.vertices[0]].co - mesh.vertices[edge.vertices[1]].co).length, 8) for edge in mesh.edges]
        ),
        "face_area_digest": digest_values([round(face.area, 10) for face in mesh.polygons]),
        "dimensions": [round(value, 8) for value in obj.dimensions],
        "matrix_world": [round(value, 8) for row in obj.matrix_world for value in row],
    }


def configure_mof(args):
    if not hasattr(bpy.context.scene, "mof_properties"):
        raise RuntimeError("MOF add-on properties are not registered")
    if not hasattr(bpy.ops.object, "auto_uv_operator"):
        raise RuntimeError("MOF operator bpy.ops.object.auto_uv_operator is not registered")
    props = bpy.context.scene.mof_properties
    values = {
        "target_uv_map": args.uv_name,
        "resolution": args.resolution,
        "pixel_padding": 2,
        "separate_hard_edges": False,
        "separate_marked_edges": False,
        "aspect": 1.0,
        "use_normals": True,
        "udims": 1,
        "overlap_identical": False,
        "overlap_mirrored": False,
        "world_scale": False,
        "suppress_validation": False,
        "quads": True,
        "flat_soft_surface": True,
        "cones": True,
        "grids": True,
        "strips": True,
        "patches": True,
        "planes": True,
        "merge": True,
        "pre_smooth": True,
        "soft_unfold": True,
        "tubes": True,
        "junctions": True,
        "angle_based_flattening": True,
        "smooth": True,
        "repair_smooth": True,
        "repair": True,
        "squares": True,
        "relax": True,
        "relax_iterations": args.relax_iterations,
        "cut": True,
        "stretch": False,
        "match": True,
        "packing": True,
        "packing_iterations": 8,
        "validate": False,
    }
    for key, value in values.items():
        if hasattr(props, key):
            setattr(props, key, value)


def pack_uv(obj, padding_px, resolution, average_scale):
    activate(obj)
    previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if average_scale:
        try:
            bpy.ops.uv.average_islands_scale(shear=False)
        except TypeError:
            bpy.ops.uv.average_islands_scale()
    try:
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
    except TypeError:
        result = bpy.ops.uv.pack_islands(
            rotate=True,
            margin=padding_px / float(resolution),
        )
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync
    obj.data.update()
    return sorted(result)


def select_objects(objects, active=None):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or objects[0]


def pack_uv_objects(objects, padding_px, resolution, average_scale):
    if not objects:
        raise RuntimeError("No face-bearing mesh objects are available for UV packing")
    select_objects(objects)
    previous_sync = bpy.context.scene.tool_settings.use_uv_select_sync
    bpy.context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if average_scale:
        try:
            bpy.ops.uv.average_islands_scale(shear=False)
        except TypeError:
            bpy.ops.uv.average_islands_scale()
    try:
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
    except TypeError:
        result = bpy.ops.uv.pack_islands(
            rotate=True,
            margin=padding_px / float(resolution),
        )
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.tool_settings.use_uv_select_sync = previous_sync
    for obj in objects:
        obj.data.update()
    return sorted(result)


def restore_materials(obj, originals):
    current = list(obj.data.materials)
    canonical = {material: index for index, material in enumerate(originals)}
    indices = []
    for polygon in obj.data.polygons:
        material = current[polygon.material_index] if 0 <= polygon.material_index < len(current) else None
        indices.append(canonical.get(material, 0))
    obj.data.materials.clear()
    for material in originals:
        obj.data.materials.append(material)
    for polygon, index in zip(obj.data.polygons, indices, strict=True):
        polygon.material_index = index


def cross2(first, second):
    return first.x * second.y - first.y * second.x


def topology(mesh):
    edge_by_vertices = {tuple(sorted(edge.vertices)): edge.index for edge in mesh.edges}
    edge_faces = [[] for _ in mesh.edges]
    for polygon in mesh.polygons:
        for index, vertex in enumerate(polygon.vertices):
            other = polygon.vertices[(index + 1) % len(polygon.vertices)]
            edge_faces[edge_by_vertices[tuple(sorted((vertex, other)))]].append(polygon.index)
    return edge_faces


def face_vertex_uv(mesh, uv_layer, face_index, vertex_index):
    for loop_index in mesh.polygons[face_index].loop_indices:
        if mesh.loops[loop_index].vertex_index == vertex_index:
            return uv_layer.data[loop_index].uv
    raise RuntimeError("Vertex not found in face")


def edge_uv_continuous(mesh, uv_layer, edge_index, linked_faces):
    if len(linked_faces) != 2:
        return False
    first, second = linked_faces
    for vertex in mesh.edges[edge_index].vertices:
        if (face_vertex_uv(mesh, uv_layer, first, vertex) - face_vertex_uv(mesh, uv_layer, second, vertex)).length > 1.0e-7:
            return False
    return True


def uv_islands(mesh, uv_layer):
    adjacency = defaultdict(list)
    edge_faces = topology(mesh)
    for edge_index, linked in enumerate(edge_faces):
        if len(linked) == 2 and edge_uv_continuous(mesh, uv_layer, edge_index, linked):
            first, second = linked
            adjacency[first].append(second)
            adjacency[second].append(first)
    seen = set()
    islands = []
    for start in range(len(mesh.polygons)):
        if start in seen:
            continue
        seen.add(start)
        queue = deque([start])
        group = []
        while queue:
            face = queue.popleft()
            group.append(face)
            for other in adjacency.get(face, ()):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        islands.append(group)
    return islands


def polygon_area(points):
    return 0.5 * sum(
        cross2(points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )


def line_intersection(p1, p2, q1, q2):
    r = p2 - p1
    s = q2 - q1
    denominator = cross2(r, s)
    if abs(denominator) <= EPS:
        return p2.copy()
    return p1 + r * (cross2(q1 - p1, s) / denominator)


def triangle_overlap_area(first, second):
    output = [point.copy() for point in first]
    clip = [point.copy() for point in second]
    sign = 1.0 if polygon_area(clip) >= 0.0 else -1.0
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
        minimum_x = max(0, min(grid_size - 1, int(math.floor(min(point.x for point in points) * grid_size))))
        maximum_x = max(0, min(grid_size - 1, int(math.floor(max(point.x for point in points) * grid_size))))
        minimum_y = max(0, min(grid_size - 1, int(math.floor(min(point.y for point in points) * grid_size))))
        maximum_y = max(0, min(grid_size - 1, int(math.floor(max(point.y for point in points) * grid_size))))
        for x in range(minimum_x, maximum_x + 1):
            for y in range(minimum_y, maximum_y + 1):
                for other in grid[(x, y)]:
                    if triangles[other][1] != item[1]:
                        candidates.add((min(index, other), max(index, other)))
                grid[(x, y)].append(index)
    return [
        pair for pair in candidates
        if triangle_overlap_area(triangles[pair[0]][2], triangles[pair[1]][2]) > 1.0e-11
    ]


def inspect_uv(obj, uv_name, include_overlap=True):
    mesh = obj.data
    uv_layer = mesh.uv_layers[uv_name]
    islands = uv_islands(mesh, uv_layer)
    face_island = {face: index for index, faces in enumerate(islands) for face in faces}
    flipped = set()
    degenerate_uv = set()
    degenerate_3d = set()
    out_of_tile = 0
    triangles = []
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    for polygon in mesh.polygons:
        loops = list(polygon.loop_indices)
        for loop_index in loops:
            point = uv_layer.data[loop_index].uv
            min_u = min(min_u, point.x)
            min_v = min(min_v, point.y)
            max_u = max(max_u, point.x)
            max_v = max(max_v, point.y)
            if point.x < -1.0e-7 or point.y < -1.0e-7 or point.x > 1.0 + 1.0e-7 or point.y > 1.0 + 1.0e-7:
                out_of_tile += 1
        for index in range(1, len(loops) - 1):
            tri_loops = (loops[0], loops[index], loops[index + 1])
            points_3d = [mesh.vertices[mesh.loops[loop].vertex_index].co for loop in tri_loops]
            points_uv = [uv_layer.data[loop].uv.copy() for loop in tri_loops]
            signed = cross2(points_uv[1] - points_uv[0], points_uv[2] - points_uv[0])
            area_uv = abs(signed) * 0.5
            area_3d = (points_3d[1] - points_3d[0]).cross(points_3d[2] - points_3d[0]).length * 0.5
            if area_uv <= EPS:
                degenerate_uv.add(polygon.index)
            if area_3d <= EPS:
                degenerate_3d.add(polygon.index)
            if area_uv > EPS and area_3d > EPS and signed < 0.0:
                flipped.add(polygon.index)
            triangles.append((polygon.index, face_island[polygon.index], points_uv))
    overlaps = overlap_pairs(triangles) if include_overlap else []
    overlap_faces = {triangles[index][0] for pair in overlaps for index in pair}
    return {
        "uv_islands": len(islands),
        "uv_bounds": [min_u, min_v, max_u, max_v],
        "out_of_0_1_loops": out_of_tile,
        "flipped_faces": sorted(flipped),
        "degenerate_uv_faces": sorted(degenerate_uv),
        "degenerate_3d_faces": sorted(degenerate_3d),
        "overlap_faces": sorted(overlap_faces),
        "overlap_triangle_pairs": len(overlaps),
    }


def regularize_faces(obj, uv_name, face_indices, fixed_radius=None):
    mesh = obj.data
    uv_layer = mesh.uv_layers[uv_name]
    for serial, face_index in enumerate(sorted(face_indices)):
        polygon = mesh.polygons[face_index]
        loops = list(polygon.loop_indices)
        count = len(loops)
        radius = fixed_radius or max(math.sqrt(max(polygon.area, 1.0e-12) / math.pi), 1.0e-5)
        center_x = serial * radius * 3.0
        for index, loop_index in enumerate(loops):
            angle = 2.0 * math.pi * index / count
            uv_layer.data[loop_index].uv = (
                center_x + radius * math.cos(angle),
                radius * math.sin(angle),
            )
    mesh.update()


def multi_object_overlap_audit(objects, uv_name):
    triangles = []
    for obj in objects:
        mesh = obj.data
        uv_layer = mesh.uv_layers[uv_name]
        islands = uv_islands(mesh, uv_layer)
        face_island = {
            face: index for index, faces in enumerate(islands) for face in faces
        }
        for polygon in mesh.polygons:
            loops = list(polygon.loop_indices)
            for index in range(1, len(loops) - 1):
                tri_loops = (loops[0], loops[index], loops[index + 1])
                points_uv = [uv_layer.data[loop].uv.copy() for loop in tri_loops]
                triangles.append(
                    (
                        (obj.name, polygon.index),
                        (obj.name, face_island[polygon.index]),
                        points_uv,
                    )
                )
    pairs = overlap_pairs(triangles)
    cross_object_pairs = [
        pair
        for pair in pairs
        if triangles[pair[0]][0][0] != triangles[pair[1]][0][0]
    ]
    return {
        "cross_object_overlap_triangle_pairs": len(cross_object_pairs),
        "cross_object_overlap_faces": sorted(
            {
                triangles[index][0]
                for pair in cross_object_pairs
                for index in pair
            }
        ),
    }


def export_fbx(objects, path):
    select_objects(objects)
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


def main():
    args = parse_args()
    started = time.time()
    source = os.path.abspath(args.input)
    output_blend = os.path.abspath(args.output_blend)
    output_fbx = os.path.abspath(args.output_fbx or os.path.splitext(output_blend)[0] + ".fbx")
    report_path = os.path.abspath(args.report or os.path.splitext(output_blend)[0] + "_REPORT.json")
    if os.path.normcase(source) == os.path.normcase(output_blend):
        raise RuntimeError("Refusing to overwrite the source file")
    for path in (output_blend, output_fbx, report_path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

    open_source(source)
    targets, source_meshes = choose_targets(args.object)
    source_mesh_count = len(source_meshes)
    target_data = {target.data.as_pointer() for target in targets}
    for data_pointer in target_data:
        linked = [obj.name for obj in source_meshes if obj.data.as_pointer() == data_pointer]
        if len(linked) > 1:
            raise RuntimeError(
                "Linked mesh datablocks are not safe for MOF processing: "
                f"{linked}"
            )
    source_geometry = {obj.name: geometry_record(obj) for obj in source_meshes}
    target_states = [
        {
            "target": target,
            "name": target.name,
            "data_name": target.data.name,
            "materials": list(target.data.materials),
            "geometry_before": source_geometry[target.name],
        }
        for target in targets
    ]

    backup_default = os.path.splitext(output_blend)[0] + "_SOURCE_BACKUP.blend"
    backup_path = unique_path(os.path.abspath(args.backup or backup_default))
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=backup_path, copy=True, check_existing=False)

    all_parts = []
    for state in target_states:
        target = state["target"]
        reset_uv(target.data, args.uv_name)
        activate(target)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="LOOSE")
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.update()
        parts = sorted(
            [obj for obj in bpy.context.selected_objects if obj.type == "MESH"],
            key=lambda item: item.name,
        )
        if not parts or target not in parts:
            raise RuntimeError(
                f"Loose-part separation did not preserve target object {state['name']}"
            )
        for part in parts:
            ensure_uv(part.data, args.uv_name)
        state["parts"] = parts
        all_parts.extend(parts)

    face_parts = [part for part in all_parts if part.data.polygons]
    if not face_parts:
        raise RuntimeError("Loose-part separation produced no face-bearing mesh objects")

    configure_mof(args)
    select_objects(face_parts)
    print(
        "MOF_START "
        f"objects={len(targets)} parts={len(face_parts)} "
        f"faces={sum(state['geometry_before']['faces'] for state in target_states)}",
        flush=True,
    )
    operator_warning = ""
    try:
        operator_result = sorted(bpy.ops.object.auto_uv_operator())
    except RuntimeError as error:
        operator_result = ["FINISHED_WITH_PART_WARNINGS"]
        operator_warning = str(error)
    print(f"MOF_DONE result={operator_result}", flush=True)

    nonface_parts = [part.name for part in all_parts if not part.data.polygons]
    failed_face_parts = [
        part.name for part in face_parts if not usable_uv(part, args.uv_name)
    ]
    if failed_face_parts:
        raise RuntimeError(f"MOF did not produce usable UVs for face parts: {failed_face_parts[:20]}")

    processed = []
    for state in target_states:
        parts = state["parts"]
        target = state["target"]
        select_objects(parts, active=target)
        if len(parts) > 1:
            result = bpy.ops.object.join()
            if "FINISHED" not in result:
                raise RuntimeError(
                    f"Could not restore loose parts for target {state['name']}: {sorted(result)}"
                )
        joined = bpy.context.active_object
        joined.name = state["name"]
        joined.data.name = state["data_name"]
        restore_materials(joined, state["materials"])
        ensure_uv(joined.data, args.uv_name)
        state["joined"] = joined
        processed.append(joined)

    pack_result = pack_uv_objects(
        processed,
        args.padding_px,
        args.resolution,
        average_scale=True,
    )

    repair_log = []
    for repair_pass in range(1, 5):
        repairs = []
        for obj in processed:
            audit = inspect_uv(obj, args.uv_name, include_overlap=True)
            if audit["degenerate_3d_faces"]:
                raise RuntimeError(
                    f"Source geometry in {obj.name} contains degenerate 3D faces: "
                    f"{audit['degenerate_3d_faces'][:20]}"
                )
            problem_faces = set(
                audit["flipped_faces"]
                + audit["degenerate_uv_faces"]
                + audit["overlap_faces"]
            )
            if not problem_faces:
                continue
            if repair_pass == 1:
                regularize_faces(obj, args.uv_name, problem_faces)
                mode = "area_scaled"
            else:
                regularize_faces(
                    obj,
                    args.uv_name,
                    problem_faces,
                    fixed_radius=0.002,
                )
                mode = "fixed_micro_island"
            repairs.append(
                {
                    "object": obj.name,
                    "faces": sorted(problem_faces),
                    "mode": mode,
                }
            )
        if not repairs:
            break
        pack_uv_objects(
            processed,
            args.padding_px,
            args.resolution,
            average_scale=repair_pass == 1,
        )
        repair_log.append({"pass": repair_pass, "repairs": repairs})

    final_audits = {
        obj.name: inspect_uv(obj, args.uv_name, include_overlap=True)
        for obj in processed
    }
    hard_uv_failures = {}
    for object_name, audit in final_audits.items():
        failures = {
            key: audit[key]
            for key in (
                "out_of_0_1_loops",
                "flipped_faces",
                "degenerate_uv_faces",
                "degenerate_3d_faces",
                "overlap_triangle_pairs",
            )
            if audit[key]
        }
        if failures:
            hard_uv_failures[object_name] = failures
    cross_object_audit = multi_object_overlap_audit(processed, args.uv_name)
    if cross_object_audit["cross_object_overlap_triangle_pairs"]:
        hard_uv_failures["cross_object"] = cross_object_audit
    if hard_uv_failures:
        raise RuntimeError(f"Final UV hard failures remain: {hard_uv_failures}")

    for obj in processed:
        for layer in list(obj.data.uv_layers):
            if layer.name != args.uv_name:
                obj.data.uv_layers.remove(layer)
        obj.data.uv_layers.active = obj.data.uv_layers[args.uv_name]
        obj.data.uv_layers[args.uv_name].active_render = True

    final_meshes = sorted(
        [obj for obj in bpy.context.scene.objects if obj.type == "MESH"],
        key=lambda item: item.name,
    )
    if len(final_meshes) != source_mesh_count:
        raise RuntimeError(
            f"Expected {source_mesh_count} final mesh objects, found {len(final_meshes)}"
        )
    geometry_after = {obj.name: geometry_record(obj) for obj in final_meshes}
    if geometry_after != source_geometry:
        raise RuntimeError("Geometry integrity check failed after multi-object MOF unwrap")

    activate(processed[0])
    bpy.ops.wm.save_as_mainfile(filepath=output_blend, check_existing=False)
    export_fbx(final_meshes, output_fbx)
    report = {
        "source": source,
        "source_backup": backup_path,
        "output_blend": output_blend,
        "output_fbx": output_fbx,
        "objects": [obj.name for obj in processed],
        "objects_processed_by_mof": len(processed),
        "mesh_object_count": len(final_meshes),
        "loose_parts_processed": len(all_parts),
        "face_parts_processed_by_mof": len(face_parts),
        "nonface_parts_preserved": nonface_parts,
        "mof_operator_result": operator_result,
        "mof_operator_warning": operator_warning,
        "resolution": args.resolution,
        "padding_px": args.padding_px,
        "pack_result": pack_result,
        "repair_log": repair_log,
        "geometry": geometry_after,
        "materials": {
            obj.name: [material.name if material else "" for material in obj.data.materials]
            for obj in processed
        },
        "uv_layers": {
            obj.name: [layer.name for layer in obj.data.uv_layers]
            for obj in processed
        },
        "uv_audit": final_audits,
        "cross_object_uv_audit": cross_object_audit,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("BLENDER_MOF_UV_COMPLETE")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
