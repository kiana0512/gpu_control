#!/usr/bin/env python3
"""Verify that the minimal package contains and invokes the complete skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "blender-retopology-compare-iterate"
EXPECTED = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/direct-output-construction-rules.md",
    "references/execution-plan-schema.md",
    "references/learned-asset-lessons.md",
    "scripts/guard_shape_authority_plan.py",
}


def main() -> int:
    actual = {
        path.relative_to(SKILL).as_posix()
        for path in SKILL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    errors: list[str] = []
    if actual != EXPECTED:
        errors.append(f"skill file mismatch: expected={sorted(EXPECTED)} actual={sorted(actual)}")

    prompt_path = ROOT / "server" / "agent_prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    for token in (
        "$blender-retopology-compare-iterate",
        "guard_shape_authority_plan.py",
        "generated_for_user_inspection",
        "必须真正执行 Blender",
        "已授权的服务器 headless 执行",
        "generation_report.json",
    ):
        if token not in prompt:
            errors.append(f"prompt misses {token}")

    scripts = [
        ROOT / "server" / "one_click_retopology.py",
        ROOT / "server" / "mesh_inventory.py",
        ROOT / "server" / "verify_package.py",
        SKILL / "scripts" / "guard_shape_authority_plan.py",
    ]
    for script in scripts:
        try:
            compile(script.read_text(encoding="utf-8"), str(script), "exec")
        except SyntaxError as exc:
            errors.append(f"syntax error in {script}: {exc}")

    env_text = (ROOT / "server" / "worker.env.example").read_text(encoding="utf-8")
    for name in ("BLENDER_EXECUTABLE", "CODEX_BIN", "CODEX_EXEC_ARGS_JSON", "RETOPOLOGY_TIMEOUT_SECONDS"):
        if f"{name}=" not in env_text:
            errors.append(f"worker.env.example misses {name}")

    largest = max((path.stat().st_size, path) for path in ROOT.rglob("*") if path.is_file())
    if largest[0] > 5 * 1024 * 1024:
        errors.append(f"unexpected large file: {largest[1]} ({largest[0]} bytes)")

    payload = {
        "ok": not errors,
        "skill_id": "blender-retopology-compare-iterate",
        "skill_file_count": len(actual),
        "one_click_entrypoint": str(ROOT / "server" / "one_click_retopology.py"),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
