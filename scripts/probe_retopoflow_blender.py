"""Blender UI-mode RetopoFlow registration and real operator invocation probe.

This invokes the official add-on's modal operator in an ephemeral scene, records
the Blender result, and exits. It is not an automatic mesh generator.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


ARGS = arguments()


def finish(payload: dict[str, object]) -> None:
    payload["checked_at"] = datetime.now(UTC).isoformat()
    Path(ARGS.output).write_text(json.dumps(payload, indent=2), "utf-8")
    print("GPU_CONTROL_RETOPOFLOW_PROBE=" + json.dumps(payload, separators=(",", ":")))
    bpy.ops.wm.quit_blender()


def probe() -> None:
    payload: dict[str, object] = {
        "schema_version": "gpu-control.retopoflow-probe.v1",
        "registered": False,
        "operator": "cgcookie.retopoflow_newtarget_active",
        "operator_polled": False,
        "operator_invoked": False,
        "operator_result": [],
    }
    try:
        addon = Path(ARGS.addon_root).resolve()
        sys.path.insert(0, str(addon.parent))
        module = importlib.import_module(addon.name)
        module.register()
        payload["version"] = ".".join(map(str, module.bl_info["version"]))
        payload["registered"] = hasattr(bpy.ops.cgcookie, "retopoflow")

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        source = bpy.context.active_object
        source.name = "GPUControl_RetopoFlow_Probe_Source"

        window = bpy.context.window_manager.windows[0]
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        region = next(region for region in area.regions if region.type == "WINDOW")
        with bpy.context.temp_override(window=window, area=area, region=region):
            payload["operator_polled"] = bool(
                bpy.ops.cgcookie.retopoflow_newtarget_active.poll()
            )
            if payload["operator_polled"]:
                result = bpy.ops.cgcookie.retopoflow_newtarget_active("INVOKE_DEFAULT")
                payload["operator_result"] = sorted(result)
                payload["operator_invoked"] = bool(
                    {"RUNNING_MODAL", "FINISHED"}.intersection(result)
                )
        payload["healthy"] = bool(
            payload["registered"]
            and payload["operator_polled"]
            and payload["operator_invoked"]
        )
    except Exception as exc:
        payload["healthy"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc(limit=8)
    finish(payload)


bpy.app.timers.register(probe, first_interval=1.0)
