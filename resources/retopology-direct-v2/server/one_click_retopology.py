#!/usr/bin/env python3
"""One-click FBX/Blend server adapter for the complete retopology skill."""

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


SKILL_ID = "blender-retopology-compare-iterate"
EXPECTED_SKILL_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/direct-output-construction-rules.md",
    "references/execution-plan-schema.md",
    "references/learned-asset-lessons.md",
    "scripts/guard_shape_authority_plan.py",
    "scripts/prepare_fbx_source.py",
}
DEFAULT_CODEX_ARGS = ["exec", "--full-auto", "--json", "-C", "{job_dir}", "-"]
SUPPORTED_INPUTS = {".fbx", ".blend"}
ALLOWED_METHODS = {
    "controlled_direct_reduction",
    "semantic_reconstruction",
    "per_component_hybrid",
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


def render_prompt(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    if "{{" in rendered or "}}" in rendered:
        raise RuntimeError("agent prompt contains unresolved placeholders")
    if "$blender-retopology-compare-iterate" not in rendered:
        raise RuntimeError("agent prompt does not invoke the required skill")
    return rendered


def load_codex_args(job_dir: Path) -> list[str]:
    raw = os.environ.get("CODEX_EXEC_ARGS_JSON")
    values = json.loads(raw) if raw else DEFAULT_CODEX_ARGS
    if not isinstance(values, list) or not values or not all(isinstance(item, str) for item in values):
        raise RuntimeError("CODEX_EXEC_ARGS_JSON must be a non-empty JSON string array")
    return [item.replace("{job_dir}", str(job_dir)) for item in values]


def valid_blend(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 7:
        return False
    with path.open("rb") as handle:
        return handle.read(7) == b"BLENDER"


def prepare_fbx(
    blender: str,
    installed_skill: Path,
    input_copy: Path,
    working_blend: Path,
    manifest_path: Path,
    job_dir: Path,
) -> tuple[bool, str | None]:
    stdout_path = job_dir / "fbx_import_stdout.log"
    stderr_path = job_dir / "fbx_import_stderr.log"
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
    timeout = int(os.environ.get("RETOPOLOGY_FBX_IMPORT_TIMEOUT_SECONDS", "600"))
    with stdout_path.open("w", encoding="utf-8", newline="\n") as stdout, stderr_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as stderr:
        try:
            completed = subprocess.run(
                command,
                stdout=stdout,
                stderr=stderr,
                cwd=job_dir,
                timeout=timeout,
                check=False,
                env={**os.environ, "PYTHONNOUSERSITE": "1"},
            )
        except subprocess.TimeoutExpired:
            return False, "fbx_import_timeout"
    if completed.returncode != 0:
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
        ):
            if field not in item:
                raise RuntimeError(f"generation report asset {index} misses {field}")
        if item.get("method_decision") not in ALLOWED_METHODS:
            raise RuntimeError(
                f"generation report asset {index} has unsupported method_decision"
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one complete FBX/Blend retopology job")
    parser.add_argument("--input", type=Path, required=True, help="source .fbx or .blend")
    parser.add_argument("--output", type=Path, required=True, help="new result .blend")
    parser.add_argument("--high", action="append", default=[], help="Blend high object name; repeatable")
    parser.add_argument("--job-root", type=Path, default=Path(os.environ.get("JOB_ROOT", "jobs")))
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("RETOPOLOGY_TIMEOUT_SECONDS", "7200")))
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source = args.input.resolve()
    destination = args.output.resolve()
    input_suffix = source.suffix.lower()
    if input_suffix not in SUPPORTED_INPUTS or not source.is_file() or source.stat().st_size == 0:
        raise SystemExit(f"input must be a non-empty FBX or Blend file: {source}")
    if destination.suffix.lower() != ".blend":
        raise SystemExit("output must use the .blend suffix")
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing output: {destination}")

    package_root = args.package_root.resolve()
    bundled_skill = package_root / SKILL_ID
    prompt_template = package_root / "server" / "agent_prompt.md"
    source_inventory = skill_inventory(bundled_skill)
    if set(source_inventory) != EXPECTED_SKILL_FILES:
        raise SystemExit(
            "server package does not contain the exact complete skill: "
            + json.dumps(sorted(source_inventory), ensure_ascii=False)
        )

    job_id = args.job_id or f"retopo-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    job_dir = (args.job_root.resolve() / job_id).resolve()
    if job_dir.exists():
        raise SystemExit(f"job directory already exists: {job_dir}")
    input_copy = job_dir / "input" / f"source{input_suffix}"
    working_blend = input_copy if input_suffix == ".blend" else job_dir / "work" / "source.blend"
    source_manifest = job_dir / "source-manifest.json"
    job_output = job_dir / "artifacts" / "result.blend"
    result_path = job_dir / "result.json"
    codex_home = job_dir / "codex-home"
    installed_skill = codex_home / "skills" / SKILL_ID
    for directory in (
        input_copy.parent,
        working_blend.parent,
        job_output.parent,
        job_dir / "plans",
        installed_skill.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, input_copy)
    shutil.copytree(bundled_skill, installed_skill)
    if skill_inventory(installed_skill) != source_inventory:
        raise SystemExit("job-local skill installation failed hash verification")

    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    codex = os.environ.get("CODEX_BIN", "/usr/local/bin/codex")
    if input_suffix == ".fbx":
        prepared, preparation_error = prepare_fbx(
            blender,
            installed_skill,
            input_copy,
            working_blend,
            source_manifest,
            job_dir,
        )
        if not prepared:
            result = {
                "job_id": job_id,
                "status": "failed_preparation",
                "error": preparation_error,
                "input_format": "fbx",
                "stderr_log": str(job_dir / "fbx_import_stderr.log"),
                "automatic_retry": False,
            }
            write_result(result_path, result)
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
            "OUTPUT_BLEND": str(job_output),
            "HIGH_OBJECTS": json.dumps(requested_highs or ["ALL_HIGH_MESH_OBJECTS"], ensure_ascii=False),
            "BLENDER_EXECUTABLE": blender,
            "JOB_DIR": str(job_dir),
        },
    )
    prompt_path = job_dir / "agent_prompt.md"
    atomic_write(prompt_path, prompt)

    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "BLENDER_EXECUTABLE": blender,
            "RETOPOLOGY_SKILL_ROOT": str(installed_skill),
            "RETOPOLOGY_INPUT_SOURCE": str(input_copy),
            "RETOPOLOGY_INPUT_BLEND": str(working_blend),
            "RETOPOLOGY_OUTPUT_BLEND": str(job_output),
        }
    )
    command = [codex, *load_codex_args(job_dir)]
    events_path = job_dir / "agent_events.jsonl"
    stderr_path = job_dir / "agent_stderr.log"
    with events_path.open("w", encoding="utf-8", newline="\n") as events, stderr_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as errors:
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
        result = {
            "job_id": job_id,
            "status": "failed",
            "error": "codex_timeout",
            "automatic_retry": False,
        }
        write_result(result_path, result)
        return 124
    if completed.returncode != 0:
        result = {
            "job_id": job_id,
            "status": "failed",
            "error": "codex_runner_failed",
            "returncode": completed.returncode,
            "automatic_retry": False,
            "stderr_log": str(stderr_path),
        }
        write_result(result_path, result)
        return completed.returncode
    if not valid_blend(job_output):
        raise SystemExit("Codex completed but did not create a valid output Blend")

    report_path = job_dir / "generation_report.json"
    report = validate_generation_report(report_path, requested_highs)
    if sha256(source) != sha256(input_copy):
        raise SystemExit("uploaded source copy hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(job_output, temporary_output)
    temporary_output.replace(destination)

    result = {
        "job_id": job_id,
        "status": "generated_for_user_inspection",
        "input_format": input_suffix.removeprefix("."),
        "input_sha256": sha256(source),
        "prepared_blend": str(working_blend),
        "source_manifest": manifest_value,
        "output": str(destination),
        "output_sha256": sha256(destination),
        "generation_report": str(report_path),
        "assets": report["assets"],
        "skill_id": SKILL_ID,
        "skill_sha256": source_inventory,
        "automatic_post_generation_review": False,
        "automatic_retry": False,
    }
    write_result(result_path, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
