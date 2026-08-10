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
import signal
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# Direct script execution sets sys.path[0] to ``scripts/`` rather than the
# repository root.  Bind imports to this checkout explicitly so the plan-only
# safety gate works before a development editable install exists.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.gpu_control_core.load_testing import (  # noqa: E402
    LoadTestConfigurationError,
    RuntimeSettings,
    build_plan,
    load_fixture_manifest,
    load_scenario,
    verify_live_load_deployment,
    verify_remote_load_release_evidence,
    write_result_manifest,
)

DEFAULT_SCENARIO = REPOSITORY_ROOT / "tests/load/scenarios/six_api_120.example.yaml"
DEFAULT_FIXTURES = REPOSITORY_ROOT / "tests/load/fixtures/six_api.example.yaml"
SAFE_LOCUST_STOP_TIMEOUT_SECONDS = 30
SAFE_LOCUST_INTERRUPT_GRACE_SECONDS = 360


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


def locust_child_environment(
    runtime: RuntimeSettings,
    scenario_source: Path,
    fixture_source: Path,
    result_dir: Path,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child environment with a non-overridable safe stop timeout."""

    child_environment = dict(os.environ if source is None else source)
    child_environment.update(
        {
            "LOAD_TEST_SCENARIO_FILE": str(scenario_source),
            "LOAD_TEST_FIXTURE_MANIFEST": str(fixture_source),
            "LOAD_TEST_RESULT_DIR": str(result_dir),
            "LOAD_TEST_TARGET": runtime.target,
            "LOCUST_STOP_TIMEOUT": str(SAFE_LOCUST_STOP_TIMEOUT_SECONDS),
        }
    )
    return child_environment


def locust_command(locust_bin: Path, target: str, result_dir: Path) -> list[str]:
    """Return the fixed Locust invocation used by the guarded wrapper."""

    return [
        str(locust_bin),
        "-f",
        str(REPOSITORY_ROOT / "tests/load/locustfile.py"),
        "--headless",
        "--host",
        target,
        "--stop-timeout",
        str(SAFE_LOCUST_STOP_TIMEOUT_SECONDS),
        "--csv",
        str(result_dir / "locust"),
        "--html",
        str(result_dir / "locust.html"),
        "--json-file",
        str(result_dir / "locust.json"),
    ]


def run_locust_process(command: list[str], environment: Mapping[str, str]) -> int:
    """Run Locust while preserving its session teardown on operator Ctrl+C."""

    process = subprocess.Popen(  # noqa: S603 - fixed executable and argv, no shell
        command,
        cwd=REPOSITORY_ROOT,
        env=dict(environment),
    )
    try:
        return int(process.wait())
    except KeyboardInterrupt:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            return int(process.wait(timeout=SAFE_LOCUST_INTERRUPT_GRACE_SECONDS))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=SAFE_LOCUST_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return 130


def main() -> int:
    args = arguments()
    try:
        runtime = RuntimeSettings.from_environment()
        scenario = load_scenario(args.scenario)
        fixtures = load_fixture_manifest(args.fixtures)
        verified_release_evidence = (
            verify_remote_load_release_evidence(REPOSITORY_ROOT, runtime)
            if args.execute and runtime.is_production_target()
            else None
        )
        verified_live_deployment = (
            verify_live_load_deployment(runtime, verified_release_evidence)
            if verified_release_evidence is not None
            else None
        )
        plan = build_plan(
            runtime,
            scenario,
            fixtures,
            repository_root=REPOSITORY_ROOT,
            verified_release_evidence=verified_release_evidence,
            verified_live_deployment=verified_live_deployment,
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
            verified_release_evidence=verified_release_evidence,
            verified_live_deployment=verified_live_deployment,
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

    child_environment = locust_child_environment(
        runtime,
        scenario.source,
        fixtures.source,
        result_dir,
    )
    command = locust_command(args.locust_bin, runtime.target, result_dir)
    exit_code = 2
    try:
        exit_code = run_locust_process(command, child_environment)
    except OSError as exc:
        print(f"Locust could not start: {exc}", file=sys.stderr)
    postrun_deployment: dict[str, object] = {
        "required": runtime.is_production_target(),
        "stable_since_start": True,
        "evidence": None,
    }
    if runtime.is_production_target():
        try:
            final_live_deployment = verify_live_load_deployment(
                runtime,
                verified_release_evidence or {},
            )
            postrun_deployment["evidence"] = final_live_deployment
            postrun_deployment["stable_since_start"] = (
                final_live_deployment == verified_live_deployment
            )
        except LoadTestConfigurationError as exc:
            postrun_deployment.update(
                {
                    "stable_since_start": False,
                    "evidence": {
                        "verified": False,
                        "error": type(exc).__name__,
                    },
                }
            )
        if postrun_deployment["stable_since_start"] is not True:
            exit_code = 2
    write_json(result_dir / "postrun-deployment.json", postrun_deployment)
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
