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
from collections.abc import Callable
from pathlib import Path

ALLOWED_METHODS = {
    "controlled_direct_reduction",
    "semantic_reconstruction",
    "per_component_hybrid",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _auth_identity(auth: dict[str, object]) -> tuple[object, object]:
    """Return the stable account identity without exposing token material."""

    tokens = auth.get("tokens")
    account_id = tokens.get("account_id") if isinstance(tokens, dict) else None
    return auth.get("auth_mode"), account_id


def persist_refreshed_auth(
    auth_source: Path,
    task_auth: Path,
    source_sha256: str,
    destination: Path | None,
) -> str:
    """Persist a CLI-rotated credential back to the node-private runtime.

    Direct V2 deliberately gives every task an isolated ``CODEX_HOME``.  The
    source credential is the node-private, writable runtime credential, so a
    successful OAuth refresh must be copied back before the task directory is
    removed.  A compare-before-replace guard prevents an operator's newer
    credential from being overwritten while a task is running.
    """

    if destination is None:
        return "disabled"
    source = auth_source.resolve()
    target = destination.resolve()
    if source != target:
        return "destination_mismatch"
    if auth_source.is_symlink() or destination.is_symlink():
        return "symlink_rejected"
    try:
        if sha256(source) != source_sha256:
            return "source_changed"
        source_auth = json.loads(source.read_text(encoding="utf-8"))
        refreshed_auth = json.loads(task_auth.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid_auth_json"
    if not isinstance(source_auth, dict) or not source_auth:
        return "invalid_source_auth"
    if not isinstance(refreshed_auth, dict) or not refreshed_auth:
        return "invalid_task_auth"
    source_identity = _auth_identity(source_auth)
    refreshed_identity = _auth_identity(refreshed_auth)
    if (
        any(value is not None for value in source_identity)
        and refreshed_identity != source_identity
    ):
        return "identity_mismatch"
    refreshed_sha256 = sha256(task_auth)
    if refreshed_sha256 == source_sha256:
        return "unchanged"

    temporary = target.with_name(f".{target.name}.refresh.{os.getpid()}")
    try:
        shutil.copyfile(task_auth, temporary)
        temporary.chmod(0o600)
        if sha256(source) != source_sha256:
            return "source_changed"
        os.replace(temporary, target)
        target.chmod(0o600)
        if sha256(target) != refreshed_sha256:
            return "verification_failed"
    except OSError:
        return "write_failed"
    finally:
        temporary.unlink(missing_ok=True)
    return "updated"


def inspect_direct_blend_source(job_dir: Path) -> str | None:
    """Return the only source Mesh name from a direct Blend without saving it."""

    source_path = job_dir / "input" / "source.blend"
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        return None
    output_path = job_dir / ".gpu-control-source-inspection.json"
    helper = Path(__file__).with_name("inspect_retopology_source.py")
    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    try:
        completed = subprocess.run(  # noqa: S603 - trusted Worker executable and fixed argv
            [
                blender,
                "--background",
                str(source_path),
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(helper),
                "--",
                "--output",
                str(output_path),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        if completed.returncode != 0 or not output_path.is_file():
            return None
        inspected = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    finally:
        output_path.unlink(missing_ok=True)
    records = inspected.get("high_objects")
    if (
        not isinstance(records, list)
        or len(records) != 1
        or not isinstance(records[0], str)
        or not records[0]
    ):
        return None
    return records[0]


def _source_high_object(
    job_dir: Path,
    source_inspector: Callable[[Path], str | None] | None = None,
) -> str | None:
    manifest_path = job_dir / "source-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = manifest.get("prepared_high_object")
        return value if isinstance(value, str) and value else None
    inspector = source_inspector or inspect_direct_blend_source
    return inspector(job_dir)


def _planned_method(job_dir: Path) -> str | None:
    methods: set[str] = set()
    for path in sorted((job_dir / "plans").glob("*.json")):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = plan.get("method_decision")
        if value in ALLOWED_METHODS:
            methods.add(value)
    return next(iter(methods)) if len(methods) == 1 else None


def inspect_blend_delivery(job_dir: Path, high_object: str) -> list[dict[str, object]]:
    """Read delivery identity and mesh counters from the generated Blend."""

    report_path = job_dir / "generation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    candidate = report.get("output_blend")
    blend_path = Path(candidate).resolve() if isinstance(candidate, str) else Path()
    try:
        blend_path.relative_to(job_dir.resolve())
    except (ValueError, OSError):
        blend_path = Path()
    if not blend_path.is_file():
        blend_path = job_dir / "artifacts" / "result.blend"
    if not blend_path.is_file() or blend_path.stat().st_size <= 0:
        return []
    output_path = job_dir / ".gpu-control-delivery-inspection.json"
    helper = Path(__file__).with_name("inspect_retopology_delivery.py")
    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    completed = subprocess.run(  # noqa: S603 - trusted Worker executable and fixed argv
        [
            blender,
            "--background",
            str(blend_path),
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(helper),
            "--",
            "--output",
            str(output_path),
            "--high-object",
            high_object,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0 or not output_path.is_file():
        return []
    inspected = json.loads(output_path.read_text(encoding="utf-8"))
    output_path.unlink(missing_ok=True)
    records = inspected.get("low_objects")
    return records if isinstance(records, list) else []


def normalize_generation_report(
    job_dir: Path,
    delivery_inspector: Callable[[Path, str], list[dict[str, object]]] | None = None,
    source_inspector: Callable[[Path], str | None] | None = None,
) -> bool:
    """Normalize known report aliases without touching geometry.

    The approved upstream verifier requires ``assets`` records, while an
    otherwise successful agent may emit the same facts under ``objects`` with
    explicit high/low prefixes. Preserve that raw evidence and keep fail-closed
    checks on the fields that identify the delivered object and construction
    method. Optional diagnostic counters stay explicit ``null`` when an agent
    omits them; they must not discard an otherwise identified Blend delivery.
    """

    report_path = job_dir / "generation_report.json"
    if not report_path.is_file():
        return False
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "generated_for_user_inspection":
        return False
    high_authority = _source_high_object(job_dir, source_inspector)
    if high_authority is None:
        return False
    objects = report.get("assets")
    source_kind = "assets"
    if not isinstance(objects, list) or not objects:
        objects = report.get("objects")
        source_kind = "objects"
    if not isinstance(objects, list):
        objects = []
    planned_method = _planned_method(job_dir)
    top_level_method = report.get("method_decision")
    fallback_method = top_level_method if top_level_method in ALLOWED_METHODS else planned_method
    assets: list[dict[str, object]] = []
    missing_diagnostics: list[dict[str, object]] = []
    for item in objects:
        if not isinstance(item, dict):
            return False
        low_object = item.get("low_object", item.get("low_name"))
        method_decision = item.get("method_decision", fallback_method)
        if not isinstance(low_object, str) or not low_object:
            continue
        if method_decision not in ALLOWED_METHODS:
            return False
        normalized = {
            "high_object": high_authority,
            "low_object": low_object,
            "faces": item.get("faces", item.get("low_faces")),
            "triangles": item.get("triangles", item.get("low_triangles")),
            "method_decision": method_decision,
            "actual_plugin_use": item.get("actual_plugin_use"),
        }
        # The v3 package requires these source-coordinate declarations and
        # validates them fail-closed before Blender finalization.  Preserve
        # agent evidence verbatim; never infer or fabricate it here.
        for field in (
            "coordinate_space",
            "coordinate_authority",
            "presentation_offset_applied",
        ):
            if field in item:
                normalized[field] = item[field]
        missing = [
            field
            for field in ("faces", "triangles", "actual_plugin_use")
            if normalized[field] is None
        ]
        if missing:
            missing_diagnostics.append({"low_object": low_object, "fields": missing})
        assets.append(normalized)

    inspection_used = False
    if not assets:
        inspector = delivery_inspector or inspect_blend_delivery
        inspected = inspector(job_dir, high_authority)
        for item in inspected:
            if not isinstance(item, dict):
                return False
            low_object = item.get("low_object")
            method_decision = item.get("method_decision", fallback_method)
            if not isinstance(low_object, str) or not low_object:
                return False
            if method_decision not in ALLOWED_METHODS:
                return False
            assets.append(
                {
                    "high_object": high_authority,
                    "low_object": low_object,
                    "faces": item.get("faces"),
                    "triangles": item.get("triangles"),
                    "method_decision": method_decision,
                    "actual_plugin_use": item.get(
                        "actual_plugin_use", report.get("actual_plugin_use")
                    ),
                }
            )
        inspection_used = bool(assets)
    if not assets:
        return False
    if len(assets) != 1:
        return False

    original_path = job_dir / "generation_report.original.json"
    if not original_path.exists():
        shutil.copy2(report_path, original_path)
    report["assets"] = assets
    report["gpu_control_compatibility"] = {
        "adapter": "generation-report-delivery-evidence-v3",
        "original_report": original_path.name,
        "source_kind": source_kind,
        "source_high_authority": high_authority,
        "blend_inspection_used": inspection_used,
        "missing_diagnostics": missing_diagnostics,
    }
    temporary = job_dir / ".generation_report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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

    source_auth_sha256 = sha256(auth_source)
    writeback_value = os.environ.get("CODEX_AUTH_WRITEBACK_DESTINATION")
    writeback_destination = Path(writeback_value).resolve() if writeback_value else None

    real_codex = os.environ.get("GPU_CONTROL_REAL_CODEX_BIN", "/usr/local/bin/codex")
    completed = subprocess.run(  # noqa: S603 - immutable Worker setting
        [real_codex, *sys.argv[1:]], check=False
    )
    writeback_status = persist_refreshed_auth(
        auth_source,
        auth_destination,
        source_auth_sha256,
        writeback_destination,
    )
    print(f"CODEX_AUTH_WRITEBACK:{writeback_status}", file=sys.stderr)
    if completed.returncode == 0:
        normalize_generation_report(Path.cwd().resolve())
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
