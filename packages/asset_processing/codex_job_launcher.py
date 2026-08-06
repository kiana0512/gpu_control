#!/usr/bin/env python3
"""Seed one isolated Codex job home, then exec the approved Codex binary.

The upstream retopology package deliberately creates a new CODEX_HOME for
every asset. GPU Control keeps the production credential read-only outside
that directory, so this launcher performs only the control-plane integration
step needed before the unmodified upstream adapter invokes Codex.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_generation_report(job_dir: Path) -> bool:
    """Normalize the one known v2.3 report alias without touching geometry.

    The approved upstream verifier requires ``assets`` records, while an
    otherwise successful agent may emit the same facts under ``objects`` with
    explicit high/low prefixes. Preserve that raw evidence and translate only
    when every required value is present. Any incomplete or unrelated report
    remains unchanged so the upstream verifier still fails closed.
    """

    report_path = job_dir / "generation_report.json"
    if not report_path.is_file():
        return False
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if isinstance(report.get("assets"), list) and report["assets"]:
        return False
    if report.get("status") != "generated_for_user_inspection":
        return False
    objects = report.get("objects")
    if not isinstance(objects, list) or not objects:
        return False

    assets: list[dict[str, object]] = []
    for item in objects:
        if not isinstance(item, dict):
            return False
        normalized = {
            "high_object": item.get("high_object", item.get("high_name")),
            "low_object": item.get("low_object", item.get("low_name")),
            "faces": item.get("faces", item.get("low_faces")),
            "triangles": item.get("triangles", item.get("low_triangles")),
            "method_decision": item.get("method_decision"),
            "actual_plugin_use": item.get("actual_plugin_use"),
        }
        if any(value is None for value in normalized.values()):
            return False
        assets.append(normalized)

    original_path = job_dir / "generation_report.original.json"
    if not original_path.exists():
        shutil.copy2(report_path, original_path)
    report["assets"] = assets
    report["gpu_control_compatibility"] = {
        "adapter": "generation-report-objects-to-assets-v1",
        "original_report": original_path.name,
    }
    temporary = job_dir / ".generation_report.json.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(report_path)
    return True


def main() -> None:
    codex_home_value = os.environ.get("CODEX_HOME")
    if not codex_home_value:
        raise SystemExit("CODEX_HOME is required")
    codex_home = Path(codex_home_value).resolve()
    codex_home.mkdir(parents=True, exist_ok=True)

    auth_source = Path(
        os.environ.get("CODEX_AUTH_SOURCE", "/run/secrets/codex-auth.json")
    ).resolve()
    if not auth_source.is_file() or auth_source.stat().st_size <= 0:
        raise SystemExit("approved Codex authentication source is unavailable")
    auth_destination = codex_home / "auth.json"
    temporary = codex_home / ".auth.json.tmp"
    shutil.copyfile(auth_source, temporary)
    temporary.chmod(0o600)
    temporary.replace(auth_destination)
    if sha256(auth_source) != sha256(auth_destination):
        raise SystemExit("Codex authentication copy failed hash verification")

    real_codex = os.environ.get("GPU_CONTROL_REAL_CODEX_BIN", "/usr/local/bin/codex")
    completed = subprocess.run(  # noqa: S603 - immutable Worker setting
        [real_codex, *sys.argv[1:]], check=False
    )
    if completed.returncode == 0:
        normalize_generation_report(Path.cwd().resolve())
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
