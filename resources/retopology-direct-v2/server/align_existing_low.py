#!/usr/bin/env python3
"""Align one externally supplied low with the merged skill's transform-only path."""

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
SUPPORTED_INPUTS = {".fbx", ".obj", ".glb", ".gltf"}
REQUIRED_OUTPUTS = {
    "bake_high.fbx",
    "bake_low.fbx",
    "bake_alignment.blend",
    "bake_alignment_report.json",
}
REQUIRED_VIEWS = {
    "front.png",
    "back.png",
    "left.png",
    "right.png",
    "top.png",
    "bottom.png",
    "perspective.png",
    "views.json",
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


def read_report(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_logged(
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> tuple[int | None, bool]:
    environment = {**os.environ, "PYTHONNOUSERSITE": "1"}
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, stderr_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                env=environment,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, True
    return completed.returncode, False


def append_alignment_options(command: list[str], args: argparse.Namespace) -> None:
    if args.require_low_uv:
        command.append("--require-low-uv")
    if args.rigid_only:
        command.append("--rigid-only")
    if args.allow_axis_scale:
        command.extend(
            ["--allow-axis-scale", "--max-axis-scale-delta", str(args.max_axis_scale_delta)]
        )
    if args.prefer_current_orientation:
        command.append("--prefer-current-orientation")
    if args.prefer_source_local_axes:
        command.extend(
            ["--prefer-source-local-axes", "--source-axis-score-gap", str(args.source_axis_score_gap)]
        )
    if args.match_bounds_center:
        command.append("--match-bounds-center")
    if args.straighten_high:
        command.append("--straighten-high")
    if args.manual_high_rotation is not None:
        command.extend(
            ["--manual-high-rotation", *[str(value) for value in args.manual_high_rotation]]
        )


def fail_result(
    result_path: Path,
    job_id: str,
    error: str,
    report_path: Path,
    stderr_log: Path,
    returncode: int = 2,
) -> int:
    report = read_report(report_path)
    payload = {
        "job_id": job_id,
        "status": "failed",
        "error": report.get("error") or error,
        "alignment_report": str(report_path) if report_path.is_file() else None,
        "stderr_log": str(stderr_log),
        "automatic_retry": False,
        "mesh_fallback": False,
    }
    write_result(result_path, payload)
    return returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Align one existing low model to one high model using transforms only"
    )
    parser.add_argument("--high", type=Path, required=True)
    parser.add_argument("--low", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--job-root", type=Path, default=Path(os.environ.get("JOB_ROOT", "jobs")))
    parser.add_argument("--job-id", default=None)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("ALIGNMENT_TIMEOUT_SECONDS", "1800")),
    )
    parser.add_argument(
        "--render-timeout-seconds",
        type=int,
        default=int(os.environ.get("ALIGNMENT_RENDER_TIMEOUT_SECONDS", "600")),
    )
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--require-low-uv", action="store_true")
    parser.add_argument("--rigid-only", action="store_true")
    parser.add_argument("--allow-axis-scale", action="store_true")
    parser.add_argument("--max-axis-scale-delta", type=float, default=0.10)
    parser.add_argument("--prefer-current-orientation", action="store_true")
    parser.add_argument("--prefer-source-local-axes", action="store_true")
    parser.add_argument("--source-axis-score-gap", type=float, default=0.10)
    parser.add_argument("--match-bounds-center", action="store_true")
    parser.add_argument("--straighten-high", action="store_true")
    parser.add_argument("--manual-high-rotation", type=float, nargs=3, metavar=("X", "Y", "Z"))
    args = parser.parse_args()

    if args.rigid_only and args.allow_axis_scale:
        raise SystemExit("--rigid-only and --allow-axis-scale cannot be combined")
    if args.prefer_current_orientation and args.prefer_source_local_axes:
        raise SystemExit("choose only one orientation preference")

    high = args.high.resolve()
    low = args.low.resolve()
    destination = args.output_dir.resolve()
    for role, source in (("high", high), ("low", low)):
        if source.suffix.lower() not in SUPPORTED_INPUTS or not source.is_file() or source.stat().st_size == 0:
            raise SystemExit(f"{role} must be a non-empty FBX, OBJ, GLB, or GLTF: {source}")
    if high == low:
        raise SystemExit("high and low must be two different source files")
    if destination.exists():
        raise SystemExit(f"output directory must not already exist: {destination}")

    package_root = args.package_root.resolve()
    skill_root = package_root / SKILL_ID
    inventory = skill_inventory(skill_root)
    if set(inventory) != EXPECTED_SKILL_FILES:
        raise SystemExit(
            "package does not contain the exact complete alignment skill: "
            + json.dumps(sorted(inventory), ensure_ascii=False)
        )

    job_id = args.job_id or f"align-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    job_dir = (args.job_root.resolve() / job_id).resolve()
    if job_dir.exists():
        raise SystemExit(f"job directory already exists: {job_dir}")
    input_dir = job_dir / "input"
    artifact_dir = job_dir / "artifacts" / "aligned"
    input_dir.mkdir(parents=True)
    artifact_dir.mkdir(parents=True)
    high_copy = input_dir / f"high{high.suffix.lower()}"
    low_copy = input_dir / f"low{low.suffix.lower()}"
    shutil.copy2(high, high_copy)
    shutil.copy2(low, low_copy)

    result_path = job_dir / "result.json"
    report_path = artifact_dir / "bake_alignment_report.json"
    align_stdout = job_dir / "alignment.stdout.log"
    align_stderr = job_dir / "alignment.stderr.log"
    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    command = [
        blender,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(skill_root / "scripts" / "align_bake_models.py"),
        "--",
        "--high",
        str(high_copy),
        "--low",
        str(low_copy),
        "--output-dir",
        str(artifact_dir),
        "--report",
        str(report_path),
    ]
    append_alignment_options(command, args)
    returncode, timed_out = run_logged(
        command, job_dir, align_stdout, align_stderr, args.timeout_seconds
    )
    if timed_out:
        return fail_result(result_path, job_id, "alignment_timeout", report_path, align_stderr, 124)
    if returncode != 0:
        return fail_result(
            result_path,
            job_id,
            f"blender_alignment_exit_{returncode}",
            report_path,
            align_stderr,
            returncode or 2,
        )

    report = read_report(report_path)
    if not (
        report.get("pass") is True
        and report.get("transform_only") is True
        and report.get("low_preservation", {}).get("pass") is True
        and report.get("readback", {}).get("pass") is True
    ):
        return fail_result(
            result_path,
            job_id,
            "alignment_invariants_not_proven",
            report_path,
            align_stderr,
        )
    missing = sorted(name for name in REQUIRED_OUTPUTS if not (artifact_dir / name).is_file())
    if missing:
        return fail_result(
            result_path,
            job_id,
            "alignment_missing_outputs:" + ",".join(missing),
            report_path,
            align_stderr,
        )

    views_dir = artifact_dir / "validation_views"
    render_stdout = job_dir / "render.stdout.log"
    render_stderr = job_dir / "render.stderr.log"
    render_command = [
        blender,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(skill_root / "scripts" / "render_alignment_views.py"),
        "--",
        "--blend",
        str(artifact_dir / "bake_alignment.blend"),
        "--output-dir",
        str(views_dir),
    ]
    render_code, render_timed_out = run_logged(
        render_command, job_dir, render_stdout, render_stderr, args.render_timeout_seconds
    )
    if render_timed_out or render_code != 0:
        return fail_result(
            result_path,
            job_id,
            "validation_render_timeout" if render_timed_out else f"validation_render_exit_{render_code}",
            report_path,
            render_stderr,
            124 if render_timed_out else (render_code or 2),
        )
    missing_views = sorted(name for name in REQUIRED_VIEWS if not (views_dir / name).is_file())
    if missing_views:
        return fail_result(
            result_path,
            job_id,
            "validation_views_missing:" + ",".join(missing_views),
            report_path,
            render_stderr,
        )

    if sha256(high) != sha256(high_copy) or sha256(low) != sha256(low_copy):
        return fail_result(
            result_path, job_id, "source_copy_hash_mismatch", report_path, align_stderr
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(artifact_dir, destination)
    published = {
        path.relative_to(destination).as_posix(): sha256(path)
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    }
    payload = {
        "job_id": job_id,
        "status": "aligned",
        "skill_id": SKILL_ID,
        "transform_only": True,
        "mesh_fallback": False,
        "source_high_sha256": sha256(high),
        "source_low_sha256": sha256(low),
        "output_dir": str(destination),
        "files": published,
        "topology_uv_preserved": True,
        "fbx_readback_passed": True,
        "seven_views_rendered": True,
        "automatic_retry": False,
    }
    write_result(result_path, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
