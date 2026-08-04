import argparse
import bmesh
import bpy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from mathutils import Vector


def parse_args():
    script_args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Create a high-poly source baseline or audit a Blender high/low retopology pair"
    )
    parser.add_argument("--high", required=True, help="High-poly object name")
    parser.add_argument(
        "--low",
        help="Generated low-poly object name; omit to create a high-only source baseline",
    )
    parser.add_argument("--output", required=True, help="Output audit JSON path")
    parser.add_argument(
        "--baseline",
        help="Earlier high-only audit JSON used to verify high-poly preservation",
    )
    parser.add_argument(
        "--require-closed",
        action="store_true",
        help="Fail when the generated low has boundary edges",
    )
    parser.add_argument(
        "--duplicate-tolerance",
        type=float,
        default=1e-6,
        help="Local-space tolerance for duplicate vertex detection (default: 1e-6)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Raise an error after writing JSON when the audit has failures",
    )
    parser.add_argument(
        "--max-faces",
        type=int,
        help="Fail a high/low audit when the low exceeds this polygon-face budget",
    )
    parser.add_argument(
        "--max-triangle-equivalent",
        type=int,
        help="Fail a high/low audit when the low exceeds this triangle-equivalent budget",
    )
    parsed = parser.parse_args(script_args)
    if parsed.duplicate_tolerance <= 0:
        parser.error("--duplicate-tolerance must be greater than zero")
    if parsed.require_closed and not parsed.low:
        parser.error("--require-closed requires --low")
    if (parsed.max_faces is not None or parsed.max_triangle_equivalent is not None) and not parsed.low:
        parser.error("face-budget options require --low")
    if parsed.max_faces is not None and parsed.max_faces <= 0:
        parser.error("--max-faces must be greater than zero")
    if parsed.max_triangle_equivalent is not None and parsed.max_triangle_equivalent <= 0:
        parser.error("--max-triangle-equivalent must be greater than zero")
    return parsed


def _rounded(values, digits=9):
    return tuple(round(float(value), digits) for value in values)


def _json_safe_array(value):
    result = []
    for item in value:
        if isinstance(item, bool):
            result.append(item)
        elif isinstance(item, int):
            result.append(item)
        elif isinstance(item, float):
            result.append(round(item, 9))
        elif isinstance(item, str):
            result.append(item)
        elif hasattr(item, "__iter__"):
            nested = _json_safe_array(item)
            if nested is None:
                return None
            result.append(nested)
        else:
            return None
    return result


def _modifier_signature(modifier):
    signature = {
        "name": modifier.name,
        "type": modifier.type,
        "show_viewport": modifier.show_viewport,
        "show_render": modifier.show_render,
    }
    properties = {}
    for prop in modifier.bl_rna.properties:
        identifier = prop.identifier
        if (
            identifier == "rna_type"
            or prop.type == "COLLECTION"
            or prop.is_readonly
        ):
            continue
        try:
            value = getattr(modifier, identifier)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if value is None or isinstance(value, (bool, int, float, str)):
            properties[identifier] = value
        elif isinstance(value, set):
            properties[identifier] = sorted(value)
        elif hasattr(value, "name_full"):
            properties[identifier] = {
                "type": value.bl_rna.identifier,
                "name": value.name_full,
            }
        elif prop.is_array:
            array_value = _json_safe_array(value)
            if array_value is not None:
                properties[identifier] = array_value
    signature["properties"] = properties
    return signature


def _mesh_signature(obj):
    mesh = obj.data
    signature = {
        "name": mesh.name,
        "vertices": [_rounded(vertex.co) for vertex in mesh.vertices],
        "edges": [tuple(edge.vertices) for edge in mesh.edges],
        "polygons": [
            {
                "vertices": tuple(polygon.vertices),
                "material_index": polygon.material_index,
                "use_smooth": polygon.use_smooth,
            }
            for polygon in mesh.polygons
        ],
        "materials": [
            material.name_full if material else None for material in mesh.materials
        ],
        "uv_layers": [],
        "color_attributes": [],
    }
    for layer in mesh.uv_layers:
        signature["uv_layers"].append(
            {
                "name": layer.name,
                "active": layer.active,
                "active_render": layer.active_render,
                "uv": [_rounded(loop.uv) for loop in layer.data],
            }
        )
    for attribute in getattr(mesh, "color_attributes", []):
        values = []
        for element in attribute.data:
            color = getattr(element, "color", None)
            value = getattr(element, "value", None)
            if color is not None:
                values.append(_rounded(color))
            elif value is not None:
                values.append(round(float(value), 9))
        signature["color_attributes"].append(
            {
                "name": attribute.name,
                "domain": attribute.domain,
                "data_type": attribute.data_type,
                "values": values,
            }
        )
    return signature


def fingerprint(obj):
    signature = {
        "name": obj.name,
        "type": obj.type,
        "matrix_world": [_rounded(row) for row in obj.matrix_world],
        "parent": obj.parent.name_full if obj.parent else None,
        "modifiers": [_modifier_signature(modifier) for modifier in obj.modifiers],
    }
    if obj.type == "MESH":
        signature["mesh"] = _mesh_signature(obj)
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def world_bounds(obj):
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    center = (minimum + maximum) * 0.5
    dimensions = maximum - minimum
    return {
        "minimum": [round(float(value), 6) for value in minimum],
        "maximum": [round(float(value), 6) for value in maximum],
        "center": [round(float(value), 6) for value in center],
        "dimensions": [round(float(value), 6) for value in dimensions],
    }


def _duplicate_vertex_count(mesh, tolerance):
    buckets = defaultdict(int)
    inverse = 1.0 / tolerance
    for vertex in mesh.vertices:
        key = tuple(int(round(float(value) * inverse)) for value in vertex.co)
        buckets[key] += 1
    return sum(count - 1 for count in buckets.values() if count > 1)


def _duplicate_face_count(mesh):
    seen = set()
    duplicates = 0
    for polygon in mesh.polygons:
        key = tuple(sorted(polygon.vertices))
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _face_components(bm):
    remaining = set(bm.faces)
    components = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        component = {seed}
        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked in edge.link_faces:
                    if linked in remaining:
                        remaining.remove(linked)
                        component.add(linked)
                        stack.append(linked)
        components.append(component)
    return components


def _signed_component_volume(faces):
    volume = 0.0
    for face in faces:
        vertices = [vertex.co for vertex in face.verts]
        if len(vertices) < 3:
            continue
        origin = vertices[0]
        for index in range(1, len(vertices) - 1):
            volume += origin.dot(vertices[index].cross(vertices[index + 1])) / 6.0
    return volume


def topology(obj, duplicate_tolerance):
    if obj.type != "MESH":
        raise TypeError(f"{obj.name} is not a mesh")
    mesh = obj.data
    result = {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "triangles": 0,
        "quads": 0,
        "ngons": 0,
        "triangle_equivalent": 0,
        "boundary_edges": 0,
        "nonmanifold_edges": 0,
        "loose_edges": 0,
        "loose_vertices": 0,
        "components": 0,
        "duplicate_vertices": _duplicate_vertex_count(mesh, duplicate_tolerance),
        "duplicate_faces": _duplicate_face_count(mesh),
        "zero_area_faces": 0,
        "inconsistent_orientation_edges": 0,
        "closed_face_components": 0,
        "negative_volume_closed_components": 0,
    }
    for polygon in mesh.polygons:
        sides = len(polygon.vertices)
        result["triangle_equivalent"] += max(0, sides - 2)
        if sides == 3:
            result["triangles"] += 1
        elif sides == 4:
            result["quads"] += 1
        elif sides > 4:
            result["ngons"] += 1
        if polygon.area <= 1e-12:
            result["zero_area_faces"] += 1

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    result["boundary_edges"] = sum(
        len(edge.link_faces) == 1 for edge in bm.edges
    )
    result["nonmanifold_edges"] = sum(
        len(edge.link_faces) > 2 for edge in bm.edges
    )
    result["loose_edges"] = sum(
        len(edge.link_faces) == 0 for edge in bm.edges
    )
    result["loose_vertices"] = sum(not vertex.link_edges for vertex in bm.verts)
    result["inconsistent_orientation_edges"] = sum(
        edge.is_manifold and not edge.is_contiguous for edge in bm.edges
    )

    remaining_vertices = set(bm.verts)
    while remaining_vertices:
        result["components"] += 1
        stack = [remaining_vertices.pop()]
        while stack:
            vertex = stack.pop()
            for edge in vertex.link_edges:
                other = edge.other_vert(vertex)
                if other in remaining_vertices:
                    remaining_vertices.remove(other)
                    stack.append(other)

    for component in _face_components(bm):
        edges = {edge for face in component for edge in face.edges}
        if edges and all(len(edge.link_faces) == 2 for edge in edges):
            result["closed_face_components"] += 1
            if _signed_component_volume(component) < -1e-12:
                result["negative_volume_closed_components"] += 1
    bm.free()
    return result


def object_record(obj, duplicate_tolerance):
    determinant = obj.matrix_world.to_3x3().determinant()
    return {
        "name": obj.name,
        "fingerprint": fingerprint(obj),
        "bounds": world_bounds(obj),
        "topology": topology(obj, duplicate_tolerance),
        "materials": [
            material.name_full if material else None for material in obj.data.materials
        ],
        "modifiers": [
            {
                "name": modifier.name,
                "type": modifier.type,
                "show_viewport": modifier.show_viewport,
                "show_render": modifier.show_render,
            }
            for modifier in obj.modifiers
        ],
        "world_transform_determinant": round(float(determinant), 9),
        "mirrored_world_transform": determinant < 0,
    }


def compare_bounds(high_record, low_record):
    high_dimensions = high_record["bounds"]["dimensions"]
    low_dimensions = low_record["bounds"]["dimensions"]
    high_center = Vector(high_record["bounds"]["center"])
    low_center = Vector(low_record["bounds"]["center"])
    largest = max(max(high_dimensions), 1e-8)
    return {
        "dimension_ratio_low_over_high": [
            round(low / high, 6) if abs(high) > 1e-8 else None
            for high, low in zip(high_dimensions, low_dimensions)
        ],
        "dimension_relative_error": [
            round(abs(low - high) / abs(high), 6) if abs(high) > 1e-8 else None
            for high, low in zip(high_dimensions, low_dimensions)
        ],
        "normalized_center_offset": round((low_center - high_center).length / largest, 6),
        "world_center_offset": round((low_center - high_center).length, 6),
    }


def _topology_failures(low_topology, require_closed):
    failures = []
    if not low_topology["faces"]:
        failures.append("low has no faces")
    if low_topology["ngons"]:
        failures.append("low contains N-gons")
    if low_topology["nonmanifold_edges"]:
        failures.append("low contains edges shared by more than two faces")
    if low_topology["loose_edges"] or low_topology["loose_vertices"]:
        failures.append("low contains loose geometry")
    if low_topology["duplicate_vertices"] or low_topology["duplicate_faces"]:
        failures.append("low contains duplicate geometry")
    if low_topology["zero_area_faces"]:
        failures.append("low contains zero-area faces")
    if low_topology["inconsistent_orientation_edges"]:
        failures.append("low contains inconsistent face winding")
    if (
        low_topology["closed_face_components"] == 1
        and low_topology["negative_volume_closed_components"] == 1
    ):
        failures.append("low closed shell appears inward-facing")
    if require_closed and low_topology["boundary_edges"]:
        failures.append("low is not closed")
    return failures


def main():
    args = parse_args()
    names = {"high": args.high}
    if args.low:
        names["low"] = args.low

    objects = {}
    for role, name in names.items():
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"Missing {role} object: {name}")
        objects[role] = object_record(obj, args.duplicate_tolerance)

    topology_failures = (
        _topology_failures(objects["low"]["topology"], args.require_closed)
        if "low" in objects
        else []
    )
    failures = list(topology_failures)
    face_budget_failures = []
    if "low" in objects:
        low_topology = objects["low"]["topology"]
        if args.max_faces is not None and low_topology["faces"] > args.max_faces:
            face_budget_failures.append(
                f"low faces {low_topology['faces']} exceed hard maximum {args.max_faces}"
            )
        if (
            args.max_triangle_equivalent is not None
            and low_topology["triangle_equivalent"] > args.max_triangle_equivalent
        ):
            face_budget_failures.append(
                "low triangle-equivalent "
                f"{low_topology['triangle_equivalent']} exceeds hard maximum "
                f"{args.max_triangle_equivalent}"
            )
    failures.extend(face_budget_failures)
    preservation = {}
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline_objects = baseline.get("objects", {})
        baseline_fingerprint = baseline_objects.get("high", {}).get("fingerprint")
        if not baseline_fingerprint:
            preservation["high"] = False
            failures.append("baseline is missing high fingerprint")
        else:
            unchanged = objects["high"]["fingerprint"] == baseline_fingerprint
            preservation["high"] = unchanged
            if not unchanged:
                failures.append("high fingerprint changed from baseline")

    payload = {
        "schema_version": 3,
        "audit_mode": "high_low" if "low" in objects else "high_baseline",
        "blend": bpy.data.filepath,
        "objects": objects,
        "comparison": (
            compare_bounds(objects["high"], objects["low"])
            if "low" in objects
            else None
        ),
        "preservation": preservation,
        "topology_passed": not topology_failures if "low" in objects else None,
        "face_budget": {
            "max_faces": args.max_faces,
            "max_triangle_equivalent": args.max_triangle_equivalent,
            "passed": not face_budget_failures if "low" in objects else None,
        },
        "audit_passed": not failures,
        "failures": failures,
        "notes": [
            "Open boundary edges are reported but allowed unless --require-closed is used.",
            "Negative-volume closed components require visual review when multiple shells exist.",
            "Silhouette, self-intersections, UV distortion, and bake quality require separate review.",
        ],
        "visual_review_required": (
            [
                "front",
                "back",
                "left",
                "right",
                "top",
                "bottom",
                "perspective",
                "high/generated topology comparison",
            ]
            if "low" in objects
            else ["source fingerprint baseline only"]
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("RETOPOLOGY_HIGH_LOW_AUDIT", json.dumps(payload, ensure_ascii=False))
    if args.strict and failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
