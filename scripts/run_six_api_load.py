#!/usr/bin/env python3
"""Plan or explicitly launch the guarded six-business-API Locust harness.

Without ``--execute`` this command is network-free and only renders a plan.
The Locust file repeats every safety check, so bypassing this wrapper does not
remove the execution gate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from packages.gpu_control_core.load_testing import (
    LoadTestConfigurationError,
    RuntimeSettings,
    build_plan,
    load_fixture_manifest,
    load_scenario,
    write_result_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = REPOSITORY_ROOT / "tests/load/scenarios/six_api_120.example.yaml"
DEFAULT_FIXTURES = REPOSITORY_ROOT / "tests/load/fixtures/six_api.example.yaml"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a safe plan by default; --execute requires every load gate."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output", type=Path, help="optional plan JSON path")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="launch Locust only after environment gates and fixture validation pass",
    )
    parser.add_argument(
        "--locust-bin",
        type=Path,
        default=REPOSITORY_ROOT / ".venv/bin/locust",
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = arguments()
    try:
        runtime = RuntimeSettings.from_environment()
        scenario = load_scenario(args.scenario)
        fixtures = load_fixture_manifest(args.fixtures)
        plan = build_plan(
            runtime,
            scenario,
            fixtures,
            repository_root=REPOSITORY_ROOT,
        )
    except LoadTestConfigurationError as exc:
        print(f"load-test plan invalid: {exc}", file=sys.stderr)
        return 2

    if not args.execute:
        if args.output:
            write_json(args.output.resolve(), plan)
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        print("PLAN ONLY: no HTTP requests were sent.", file=sys.stderr)
        return 0

    try:
        runtime.assert_execution_allowed(
            scenario,
            fixtures,
            repository_root=REPOSITORY_ROOT,
        )
    except LoadTestConfigurationError as exc:
        print(f"load-test execution refused: {exc}", file=sys.stderr)
        return 2
    if runtime.result_dir is None:
        print("load-test execution refused: result directory is missing", file=sys.stderr)
        return 2
    result_dir = runtime.result_dir.resolve()
    if result_dir.exists():
        print(
            f"load-test execution refused: result directory already exists: {result_dir}",
            file=sys.stderr,
        )
        return 2
    if not args.locust_bin.is_file():
        print(f"Locust binary not found: {args.locust_bin}", file=sys.stderr)
        return 2

    result_dir.mkdir(parents=True, exist_ok=False)
    (result_dir / "configuration").mkdir()
    write_json(result_dir / "plan.json", plan)
    shutil.copy2(scenario.source, result_dir / "configuration/scenario.yaml")
    shutil.copy2(fixtures.source, result_dir / "configuration/fixtures.yaml")

    child_environment = dict(os.environ)
    child_environment.update(
        {
            "LOAD_TEST_SCENARIO_FILE": str(scenario.source),
            "LOAD_TEST_FIXTURE_MANIFEST": str(fixtures.source),
            "LOAD_TEST_RESULT_DIR": str(result_dir),
            "LOAD_TEST_TARGET": runtime.target,
        }
    )
    command = [
        str(args.locust_bin),
        "-f",
        str(REPOSITORY_ROOT / "tests/load/locustfile.py"),
        "--headless",
        "--host",
        runtime.target,
        "--csv",
        str(result_dir / "locust"),
        "--html",
        str(result_dir / "locust.html"),
        "--json-file",
        str(result_dir / "locust.json"),
    ]
    exit_code = 2
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and argv, no shell
            command,
            cwd=REPOSITORY_ROOT,
            env=child_environment,
            check=False,
        )
        exit_code = int(completed.returncode)
    except OSError as exc:
        print(f"Locust could not start: {exc}", file=sys.stderr)
    try:
        # Locust's CSV/HTML writers may flush after test_stop. Refreshing here
        # makes the final checksum inventory cover those last files too.
        write_result_manifest(result_dir, session_id=runtime.session_id)
    except (OSError, LoadTestConfigurationError) as exc:
        print(f"load-test result inventory failed: {exc}", file=sys.stderr)
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
