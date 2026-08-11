#!/usr/bin/env python3
"""Backward-compatible one-click retopology plus source-coordinate finalization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


SKILL_ID = "blender-auto-retopo-align"
EXPECTED_SKILL_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/coordinate-restoration-contract.md",
    "references/direct-output-construction-rules.md",
    "references/execution-plan-schema.md",
    "references/learned-asset-lessons.md",
    "scripts/align_bake_models.py",
    "scripts/finalize_generated_pair.py",
    "scripts/guard_shape_authority_plan.py",
    "scripts/prepare_fbx_source.py",
    "scripts/render_alignment_views.py",
    "scripts/validate_bake_pair.py",
}
DEFAULT_CODEX_ARGS = ["exec", "--full-auto", "--json", "-C", "{job_dir}", "-"]
SUPPORTED_INPUTS = {".fbx", ".blend"}
ALLOWED_METHODS = {
    "controlled_direct_reduction",
    "semantic_reconstruction",
    "per_component_hybrid",
}
REQUIRED_BAKE_OUTPUTS = {
    "bake_alignment.blend",
    "bake_alignment_report.json",
    "bake_high.fbx",
    "bake_low.fbx",
}


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def skill_inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def file_inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_result(path: Path, payload: dict) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def render_prompt(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    if "{{" in rendered or "}}" in rendered:
        raise RuntimeError("agent prompt contains unresolved placeholders")
    if "$blender-auto-retopo-align" not in rendered:
        raise RuntimeError("agent prompt does not invoke the merged skill")
    return rendered


def load_codex_args(job_dir: Path) -> list[str]:
    raw = os.environ.get("CODEX_EXEC_ARGS_JSON")
    values = json.loads(raw) if raw else DEFAULT_CODEX_ARGS
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(item, str) for item in values)
    ):
        raise RuntimeError("CODEX_EXEC_ARGS_JSON must be a non-empty JSON string array")
    return [item.replace("{job_dir}", str(job_dir)) for item in values]


def valid_blend(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 7:
        return False
    with path.open("rb") as handle:
        header = handle.read(7)
    return (
        header == b"BLENDER"
        or header.startswith(b"\x28\xb5\x2f\xfd")  # Zstandard-compressed Blend
        or header.startswith(b"\x1f\x8b")  # legacy gzip-compressed Blend
    )


def _read_json_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def recover_declared_output_blend(job_dir: Path, generated_blend: Path) -> str | None:
    """Recover a valid agent Blend saved under one approved legacy alias.

    The model is still required to create the geometry and generation report.
    This adapter only corrects an output filename mismatch inside the job's
    artifact directory; it never accepts the source/work Blend or searches
    outside the isolated job.
    """

    if valid_blend(generated_blend):
        return None
    report = _read_json_object(job_dir / "generation_report.json")
    if report is None or report.get("status") != "generated_for_user_inspection":
        return None
    artifacts = (job_dir / "artifacts").resolve()
    candidates: list[Path] = []
    declared = report.get("output_blend")
    if isinstance(declared, str) and declared:
        declared_path = Path(declared)
        candidates.append(declared_path if declared_path.is_absolute() else job_dir / declared_path)
    candidates.append(artifacts / "result.blend")
    candidates.extend(sorted(artifacts.glob("*.blend")))

    valid_candidates: dict[Path, Path] = {}
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(artifacts)
        except (OSError, ValueError):
            continue
        if resolved == generated_blend.resolve() or resolved.parent != artifacts:
            continue
        if valid_blend(resolved):
            valid_candidates[resolved] = resolved
    if len(valid_candidates) != 1:
        return None
    recovered = next(iter(valid_candidates))
    temporary = generated_blend.with_name(f".{generated_blend.name}.recovered")
    shutil.copy2(recovered, temporary)
    temporary.replace(generated_blend)
    if not valid_blend(generated_blend):
        generated_blend.unlink(missing_ok=True)
        return None
    relative = recovered.relative_to(job_dir.resolve()).as_posix()
    atomic_write(
        job_dir / "output_contract_recovery.json",
        json.dumps(
            {
                "schema": "li3d-retopology-output-recovery-v1",
                "recovered_from": relative,
                "recovered_to": generated_blend.relative_to(job_dir).as_posix(),
                "sha256": sha256(generated_blend),
                "geometry_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return relative


def codex_failure_diagnostic(job_dir: Path) -> dict[str, object]:
    """Return bounded, secret-free execution evidence for a failed adapter."""

    error_category = "OUTPUT_CONTRACT_MISSING"
    last_event_type: str | None = None
    events_path = job_dir / "agent_events.jsonl"
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event_type = event.get("type")
                if isinstance(event_type, str):
                    last_event_type = event_type
                message = event.get("message")
                if not isinstance(message, str):
                    nested = event.get("error")
                    message = nested.get("message") if isinstance(nested, dict) else ""
                diagnostic = message.lower() if isinstance(message, str) else ""
                if "refresh token" in diagnostic or "token_expired" in diagnostic:
                    error_category = "CODEX_AUTH_EXPIRED"
                elif "401 unauthorized" in diagnostic:
                    error_category = "CODEX_AUTH_UNAUTHORIZED"
                elif "rate limit" in diagnostic or "429" in diagnostic:
                    error_category = "CODEX_RATE_LIMITED"
    except OSError:
        pass
    try:
        stderr = (
            (job_dir / "agent_stderr.log")
            .read_text(encoding="utf-8", errors="replace")[-16000:]
            .lower()
        )
    except OSError:
        stderr = ""
    if "refresh token" in stderr or "token_expired" in stderr:
        error_category = "CODEX_AUTH_EXPIRED"
    elif "401 unauthorized" in stderr:
        error_category = "CODEX_AUTH_UNAUTHORIZED"
    elif "rate limit" in stderr or " 429" in stderr:
        error_category = "CODEX_RATE_LIMITED"

    files: list[dict[str, object]] = []
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file() or "codex-home" in path.parts:
            continue
        relative = path.relative_to(job_dir).as_posix()
        if relative.startswith("input/") or relative.startswith("work/"):
            continue
        files.append({"path": relative, "size_bytes": path.stat().st_size})
        if len(files) >= 80:
            break
    return {
        "error_category": error_category,
        "last_event_type": last_event_type,
        "files": files,
    }


def run_logged(
    command: list[str], cwd: Path, stdout_path: Path, stderr_path: Path, timeout: int
) -> tuple[int | None, bool]:
    with (
        stdout_path.open("w", encoding="utf-8", newline="\n") as stdout,
        stderr_path.open("w", encoding="utf-8", newline="\n") as stderr,
    ):
        try:
            completed = subprocess.run(
                command,
                stdout=stdout,
                stderr=stderr,
                cwd=cwd,
                timeout=timeout,
                check=False,
                env={**os.environ, "PYTHONNOUSERSITE": "1"},
            )
        except subprocess.TimeoutExpired:
            return None, True
    return completed.returncode, False


def prepare_fbx(
    blender: str,
    installed_skill: Path,
    input_copy: Path,
    working_blend: Path,
    manifest_path: Path,
    job_dir: Path,
) -> tuple[bool, str | None]:
    command = [
        blender,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(installed_skill / "scripts" / "prepare_fbx_source.py"),
        "--",
        "--input",
        str(input_copy),
        "--output",
        str(working_blend),
        "--manifest",
        str(manifest_path),
    ]
    returncode, timed_out = run_logged(
        command,
        job_dir,
        job_dir / "fbx_import_stdout.log",
        job_dir / "fbx_import_stderr.log",
        int(os.environ.get("RETOPOLOGY_FBX_IMPORT_TIMEOUT_SECONDS", "600")),
    )
    if timed_out:
        return False, "fbx_import_timeout"
    if returncode != 0:
        return False, "fbx_import_failed"
    if not valid_blend(working_blend) or not manifest_path.is_file():
        return False, "fbx_import_missing_artifact"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("prepared_high_object") != "SOURCE_HIGH":
        return False, "fbx_import_wrong_high_object"
    return True, None


def validate_generation_report(path: Path, requested_highs: list[str]) -> dict:
    if not path.is_file():
        raise RuntimeError("generation_report.json was not created")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "generated_for_user_inspection":
        raise RuntimeError("generation report has the wrong status")
    assets = report.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("generation report has no asset records")
    delivered = {item.get("high_object") for item in assets if isinstance(item, dict)}
    missing = sorted(set(requested_highs) - delivered)
    if missing:
        raise RuntimeError(f"generation report misses requested highs: {missing}")
    for index, item in enumerate(assets):
        if not isinstance(item, dict):
            raise RuntimeError(f"generation report asset {index} is invalid")
        for field in (
            "high_object",
            "low_object",
            "faces",
            "triangles",
            "method_decision",
            "actual_plugin_use",
            "coordinate_space",
            "coordinate_authority",
            "presentation_offset_applied",
        ):
            if field not in item:
                raise RuntimeError(f"generation report asset {index} misses {field}")
        if item.get("method_decision") not in ALLOWED_METHODS:
            raise RuntimeError(f"generation report asset {index} has unsupported method_decision")
        if item.get("coordinate_space") != "source_high_local":
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: low is not in source_high_local")
        if item.get("coordinate_authority") != "high_object_matrix_world":
            raise RuntimeError(
                "RETOPOLOGY_COORDINATE_MISMATCH: high matrix is not coordinate authority"
            )
        if item.get("presentation_offset_applied") is not False:
            raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: presentation offset is forbidden")
    return report


def validate_alignment_report(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError("bake_alignment_report.json was not created")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not (
        report.get("pass") is True
        and report.get("transform_only_alignment") is True
        and report.get("icp_used") is False
        and report.get("topology_uv_unchanged") is True
        and report.get("fbx_readback", {}).get("pass") is True
    ):
        raise RuntimeError("RETOPOLOGY_COORDINATE_MISMATCH: finalization invariants failed")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one retopology job and restore source coordinates"
    )
    parser.add_argument("--input", type=Path, required=True, help="source .fbx or .blend")
    parser.add_argument("--output", type=Path, required=True, help="legacy aligned result .blend")
    parser.add_argument(
        "--high", action="append", default=[], help="Blend high object name; repeatable"
    )
    parser.add_argument("--job-root", type=Path, default=Path(os.environ.get("JOB_ROOT", "jobs")))
    parser.add_argument("--job-id", default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("RETOPOLOGY_TIMEOUT_SECONDS", "7200")),
    )
    parser.add_argument(
        "--finalize-timeout-seconds",
        type=int,
        default=int(os.environ.get("RETOPOLOGY_FINALIZE_TIMEOUT_SECONDS", "1800")),
    )
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source = args.input.resolve()
    destination = args.output.resolve()
    input_suffix = source.suffix.lower()
    if input_suffix not in SUPPORTED_INPUTS or not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"input must be a non-empty FBX or Blend file: {source}")
    if destination.suffix.lower() != ".blend":
        raise SystemExit("output must use the .blend suffix")
    sidecar_destination = destination.parent / f"{destination.stem}.bake"
    if destination.exists() or sidecar_destination.exists():
        raise SystemExit(
            f"refusing to overwrite existing output: {destination} or {sidecar_destination}"
        )

    package_root = args.package_root.resolve()
    bundled_skill = package_root / SKILL_ID
    prompt_template = package_root / "server" / "agent_prompt.md"
    source_inventory = skill_inventory(bundled_skill)
    if set(source_inventory) != EXPECTED_SKILL_FILES:
        raise SystemExit(
            "server package does not contain the exact merged skill: "
            + json.dumps(sorted(source_inventory), ensure_ascii=False)
        )

    job_id = args.job_id or f"retopo-align-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    job_dir = (args.job_root.resolve() / job_id).resolve()
    if job_dir.exists():
        raise SystemExit(f"job directory already exists: {job_dir}")
    input_copy = job_dir / "input" / f"source{input_suffix}"
    working_blend = input_copy if input_suffix == ".blend" else job_dir / "work" / "source.blend"
    source_manifest = job_dir / "source-manifest.json"
    generated_blend = job_dir / "artifacts" / "generated.blend"
    aligned_dir = job_dir / "artifacts" / "aligned"
    result_path = job_dir / "result.json"
    codex_home = job_dir / "codex-home"
    installed_skill = codex_home / "skills" / SKILL_ID
    for directory in (
        input_copy.parent,
        working_blend.parent,
        generated_blend.parent,
        job_dir / "plans",
        installed_skill.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, input_copy)
    shutil.copytree(
        bundled_skill, installed_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    if skill_inventory(installed_skill) != source_inventory:
        raise SystemExit("job-local skill installation failed hash verification")

    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    codex = os.environ.get("CODEX_BIN", "/usr/local/bin/codex")
    if input_suffix == ".fbx":
        prepared, preparation_error = prepare_fbx(
            blender, installed_skill, input_copy, working_blend, source_manifest, job_dir
        )
        if not prepared:
            write_result(
                result_path,
                {
                    "job_id": job_id,
                    "status": "failed_preparation",
                    "error": preparation_error,
                    "input_format": "fbx",
                    "automatic_retry": False,
                },
            )
            return 2
        requested_highs = ["SOURCE_HIGH"]
        manifest_value = str(source_manifest)
    else:
        if not valid_blend(working_blend):
            raise SystemExit("input does not have a valid Blend signature")
        requested_highs = args.high
        manifest_value = "not_applicable_direct_blend_input"

    prompt = render_prompt(
        prompt_template.read_text(encoding="utf-8"),
        {
            "INPUT_SOURCE": str(input_copy),
            "WORKING_BLEND": str(working_blend),
            "SOURCE_MANIFEST": manifest_value,
            "OUTPUT_BLEND": str(generated_blend),
            "HIGH_OBJECTS": json.dumps(
                requested_highs or ["ALL_HIGH_MESH_OBJECTS"], ensure_ascii=False
            ),
            "BLENDER_EXECUTABLE": blender,
            "JOB_DIR": str(job_dir),
        },
    )
    atomic_write(job_dir / "agent_prompt.md", prompt)
    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "BLENDER_EXECUTABLE": blender,
            "RETOPOLOGY_SKILL_ROOT": str(installed_skill),
            "RETOPOLOGY_INPUT_SOURCE": str(input_copy),
            "RETOPOLOGY_INPUT_BLEND": str(working_blend),
            "RETOPOLOGY_OUTPUT_BLEND": str(generated_blend),
        }
    )
    command = [codex, *load_codex_args(job_dir)]
    with (
        (job_dir / "agent_events.jsonl").open("w", encoding="utf-8", newline="\n") as events,
        (job_dir / "agent_stderr.log").open("w", encoding="utf-8", newline="\n") as errors,
    ):
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                stdout=events,
                stderr=errors,
                cwd=job_dir,
                env=environment,
                timeout=args.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            completed = None
    if completed is None:
        write_result(
            result_path,
            {
                "job_id": job_id,
                "status": "failed",
                "error": "codex_timeout",
                "automatic_retry": False,
            },
        )
        return 124
    if completed.returncode != 0:
        diagnostic = codex_failure_diagnostic(job_dir)
        error = (
            "CODEX_AUTH_FAILED"
            if str(diagnostic.get("error_category", "")).startswith("CODEX_AUTH_")
            else "codex_runner_failed"
        )
        write_result(
            result_path,
            {
                "job_id": job_id,
                "status": "failed",
                "error": error,
                "returncode": completed.returncode,
                "diagnostic": diagnostic,
                "automatic_retry": False,
            },
        )
        return completed.returncode
    recovered_from = recover_declared_output_blend(job_dir, generated_blend)
    if not valid_blend(generated_blend):
        diagnostic = codex_failure_diagnostic(job_dir)
        error = (
            "CODEX_AUTH_FAILED"
            if str(diagnostic.get("error_category", "")).startswith("CODEX_AUTH_")
            else "RETOPOLOGY_OUTPUT_MISSING"
        )
        write_result(
            result_path,
            {
                "job_id": job_id,
                "status": "failed",
                "error": error,
                "detail": "Codex completed but did not create a valid output Blend",
                "diagnostic": diagnostic,
                "automatic_retry": False,
            },
        )
        return 4

    generation_report_path = job_dir / "generation_report.json"
    try:
        generation_report = validate_generation_report(generation_report_path, requested_highs)
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        write_result(
            result_path,
            {
                "job_id": job_id,
                "status": "failed",
                "error": "RETOPOLOGY_COORDINATE_MISMATCH",
                "detail": str(error),
                "automatic_retry": False,
            },
        )
        return 3

    finalize_command = [
        blender,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(installed_skill / "scripts" / "finalize_generated_pair.py"),
        "--",
        "--input-blend",
        str(generated_blend),
        "--generation-report",
        str(generation_report_path),
        "--output-dir",
        str(aligned_dir),
    ]
    finalize_code, finalize_timed_out = run_logged(
        finalize_command,
        job_dir,
        job_dir / "finalize_stdout.log",
        job_dir / "finalize_stderr.log",
        args.finalize_timeout_seconds,
    )
    alignment_report_path = aligned_dir / "bake_alignment_report.json"
    try:
        if finalize_timed_out:
            raise RuntimeError("coordinate finalization timed out")
        if finalize_code != 0:
            finalize_diagnostic = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (
                    job_dir / "finalize_stdout.log",
                    job_dir / "finalize_stderr.log",
                )
                if path.is_file()
            )[-12000:]
            if "RETOPOLOGY_TOPOLOGY_INVALID" in finalize_diagnostic:
                raise RuntimeError(finalize_diagnostic)
            raise RuntimeError(
                f"coordinate finalizer exited {finalize_code}: {finalize_diagnostic}"
            )
        alignment_report = validate_alignment_report(alignment_report_path)
        missing = sorted(
            name for name in REQUIRED_BAKE_OUTPUTS if not (aligned_dir / name).is_file()
        )
        if missing:
            raise RuntimeError("missing bake outputs: " + ",".join(missing))
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        error_detail = str(error)
        error_code = (
            "RETOPOLOGY_TOPOLOGY_INVALID"
            if "RETOPOLOGY_TOPOLOGY_INVALID" in error_detail
            else "RETOPOLOGY_COORDINATE_MISMATCH"
        )
        write_result(
            result_path,
            {
                "job_id": job_id,
                "status": "failed",
                "error": error_code,
                "detail": error_detail[-12000:],
                "alignment_report": str(alignment_report_path),
                "finalize_stderr_log": str(job_dir / "finalize_stderr.log"),
                "automatic_retry": False,
            },
        )
        return 3

    if sha256(source) != sha256(input_copy):
        raise SystemExit("uploaded source copy hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary_sidecar = sidecar_destination.with_name(
        f".{sidecar_destination.name}.{uuid.uuid4().hex}.tmp"
    )
    shutil.copy2(aligned_dir / "bake_alignment.blend", temporary_output)
    shutil.copytree(aligned_dir, temporary_sidecar)
    temporary_output.replace(destination)
    temporary_sidecar.replace(sidecar_destination)

    result = {
        "job_id": job_id,
        "status": "generated_for_user_inspection",
        "bake_alignment_status": "aligned",
        "input_format": input_suffix.removeprefix("."),
        "input_sha256": sha256(source),
        "prepared_blend": str(working_blend),
        "source_manifest": manifest_value,
        "output": str(destination),
        "output_sha256": sha256(destination),
        "bake_output_dir": str(sidecar_destination),
        "bake_files": file_inventory(sidecar_destination),
        "generation_report": str(generation_report_path),
        "alignment_report": str(sidecar_destination / "bake_alignment_report.json"),
        "assets": generation_report["assets"],
        "skill_id": SKILL_ID,
        "skill_sha256": source_inventory,
        "coordinate_authority": "high_object_matrix_world",
        "alignment_mode": alignment_report["alignment_mode"],
        "topology_uv_preserved": True,
        "fbx_readback_passed": True,
        "low_display": "opaque_yellow",
        "automatic_post_generation_review": False,
        "automatic_retry": False,
    }
    if recovered_from is not None:
        result["output_contract_recovered_from"] = recovered_from
    write_result(result_path, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
