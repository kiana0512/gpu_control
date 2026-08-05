#!/usr/bin/env python3
"""One-click server adapter for the complete blender-retopology skill."""

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
}
DEFAULT_CODEX_ARGS = ["exec", "--full-auto", "--json", "-C", "{job_dir}", "-"]


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


def validate_generation_report(path: Path, requested_highs: list[str]) -> dict:
    if not path.is_file():
        raise RuntimeError("generation_report.json was not created")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "generated_for_user_inspection":
        raise RuntimeError("generation report has the wrong status")
    assets = report.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("generation report has no asset records")
    if requested_highs:
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
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one complete retopology skill job")
    parser.add_argument("--input", type=Path, required=True, help="source .blend")
    parser.add_argument("--output", type=Path, required=True, help="new result .blend")
    parser.add_argument("--high", action="append", default=[], help="high object name; repeatable")
    parser.add_argument("--job-root", type=Path, default=Path(os.environ.get("JOB_ROOT", "jobs")))
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("RETOPOLOGY_TIMEOUT_SECONDS", "7200")))
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source = args.input.resolve()
    destination = args.output.resolve()
    if source.suffix.lower() != ".blend" or not source.is_file():
        raise SystemExit(f"input must be an existing .blend file: {source}")
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
    input_copy = job_dir / "input" / "source.blend"
    job_output = job_dir / "artifacts" / "result.blend"
    plans_dir = job_dir / "plans"
    codex_home = job_dir / "codex-home"
    installed_skill = codex_home / "skills" / SKILL_ID
    for directory in (input_copy.parent, job_output.parent, plans_dir, installed_skill.parent):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, input_copy)
    shutil.copytree(bundled_skill, installed_skill)
    if skill_inventory(installed_skill) != source_inventory:
        raise SystemExit("job-local skill installation failed hash verification")

    # GPU Control provisions the authenticated Codex credential as a read-only
    # secret.  The upstream package intentionally creates an isolated
    # CODEX_HOME per job, so seed that private home without sharing mutable
    # state between concurrent jobs.
    auth_source = Path(os.environ.get("CODEX_AUTH_SOURCE", "/run/secrets/codex-auth.json"))
    if not auth_source.is_file():
        raise SystemExit(f"Codex auth source is missing: {auth_source}")
    auth_destination = codex_home / "auth.json"
    shutil.copyfile(auth_source, auth_destination)
    auth_destination.chmod(0o600)

    blender = os.environ.get("BLENDER_EXECUTABLE", "/opt/blender/blender")
    codex = os.environ.get("CODEX_BIN", "/usr/local/bin/codex")
    prompt = render_prompt(
        prompt_template.read_text(encoding="utf-8"),
        {
            "INPUT_BLEND": str(input_copy),
            "OUTPUT_BLEND": str(job_output),
            "HIGH_OBJECTS": json.dumps(args.high or ["ALL_HIGH_MESH_OBJECTS"], ensure_ascii=False),
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
            "RETOPOLOGY_INPUT_BLEND": str(input_copy),
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

    result_path = job_dir / "result.json"
    if completed is None:
        result = {
            "job_id": job_id,
            "status": "failed",
            "error": "codex_timeout",
            "automatic_retry": False,
        }
        atomic_write(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
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
        atomic_write(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return completed.returncode
    if not job_output.is_file() or job_output.stat().st_size == 0:
        raise SystemExit("Codex completed but did not create the output Blend")

    report_path = job_dir / "generation_report.json"
    report = validate_generation_report(report_path, args.high)
    if sha256(source) != sha256(input_copy):
        raise SystemExit("source copy hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(job_output, temporary_output)
    temporary_output.replace(destination)

    result = {
        "job_id": job_id,
        "status": "generated_for_user_inspection",
        "input_sha256": sha256(source),
        "output": str(destination),
        "output_sha256": sha256(destination),
        "generation_report": str(report_path),
        "assets": report["assets"],
        "skill_id": SKILL_ID,
        "skill_sha256": source_inventory,
        "automatic_post_generation_review": False,
        "automatic_retry": False,
    }
    atomic_write(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
