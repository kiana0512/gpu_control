"""Fail-closed MinistryOfFlat runtime check for Li3D Windows workers.

Run with the same Blender user profile used by the worker:
  blender --background --python preflight_mof.py
"""

import bpy
import json
import os
import sys
import zipfile


def main():
    errors = []
    addon_key = next(
        (key for key in bpy.context.preferences.addons.keys() if "mof" in key.lower()),
        None,
    )
    executable_zip = ""

    if os.name != "nt":
        errors.append("The official MinistryOfFlat runtime is Windows-only; use a Windows Asset Worker")
    if not addon_key:
        errors.append("MOF Blender add-on is not enabled in this Blender user profile")
    if not hasattr(bpy.context.scene, "mof_properties"):
        errors.append("MOF scene properties are not registered")
    if not hasattr(bpy.ops.object, "auto_uv_operator"):
        errors.append("MOF operator bpy.ops.object.auto_uv_operator is not registered")

    if addon_key:
        preferences = bpy.context.preferences.addons[addon_key].preferences
        executable_zip = bpy.path.abspath(getattr(preferences, "executable_path", ""))
        if not executable_zip or not os.path.isfile(executable_zip):
            errors.append("MOF licensed runtime ZIP is not configured or is missing")
        else:
            try:
                with zipfile.ZipFile(executable_zip, "r") as archive:
                    names = [name.lower() for name in archive.namelist() if not name.endswith("/")]
                if not any(name.endswith("unwrapconsole3.exe") for name in names):
                    errors.append("MOF runtime ZIP does not contain UnWrapConsole3.exe")
            except (OSError, zipfile.BadZipFile) as error:
                errors.append(f"MOF runtime ZIP is unreadable: {error}")

    report = {
        "schema": "li3d-mof-runtime-preflight-v1",
        "available": not errors,
        "platform": sys.platform,
        "blender_version": bpy.app.version_string,
        "addon_key": addon_key,
        "runtime_zip": executable_zip,
        "errors": errors,
    }
    print("LI3D_MOF_PREFLIGHT_BEGIN")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("LI3D_MOF_PREFLIGHT_END")
    if errors:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
