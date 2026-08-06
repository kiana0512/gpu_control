#!/usr/bin/env python3
"""Read generated low-object identity and mesh counters without saving Blend."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--high-object", required=True)
    return parser.parse_args(arguments)


def main() -> None:
    args = parse_args()
    low_objects = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.name == args.high_object:
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        low_objects.append(
            {
                "low_object": obj.name,
                "faces": len(mesh.polygons),
                "triangles": len(mesh.loop_triangles),
            }
        )
    if len(low_objects) != 1:
        raise RuntimeError(
            f"expected exactly one generated low Mesh, found {len(low_objects)}"
        )
    args.output.write_text(
        json.dumps({"low_objects": low_objects}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
