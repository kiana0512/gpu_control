#!/usr/bin/env python3
"""Identify a single direct-Blend source Mesh without modifying or saving it."""

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
    return parser.parse_args(arguments)


def main() -> None:
    args = parse_args()
    high_objects = sorted(
        obj.name
        for obj in bpy.data.objects
        if obj.type == "MESH" and len(obj.data.polygons) > 0
    )
    if len(high_objects) != 1:
        raise RuntimeError(
            f"expected exactly one source Mesh, found {len(high_objects)}"
        )
    args.output.write_text(
        json.dumps({"high_objects": high_objects}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
