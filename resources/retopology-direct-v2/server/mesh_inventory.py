#!/usr/bin/env python3
"""Write a non-mutating mesh object inventory from the currently opened Blend."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    meshes = []
    for obj in sorted((item for item in bpy.data.objects if item.type == "MESH"), key=lambda item: item.name):
        mesh = obj.data
        meshes.append(
            {
                "name": obj.name,
                "faces": len(mesh.polygons),
                "triangles": sum(max(0, len(polygon.vertices) - 2) for polygon in mesh.polygons),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"meshes": meshes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
