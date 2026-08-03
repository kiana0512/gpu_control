from __future__ import annotations

import json
import stat
import textwrap
from pathlib import Path

import pytest
from gpu_control_blender_worker.main import (
    WorkerSettings,
    classify_codex_error,
    inspect_codex_runtime,
    prepare_codex_runtime_home,
    run_codex_health_probe,
)

FAKE_CODEX = """\
#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path


if "--version" in sys.argv:
    print("codex-cli fake-1.0")
    raise SystemExit(0)

mode = os.environ.get("FAKE_CODEX_MODE", "success")
marker = Path(os.environ["FAKE_CODEX_MARKER"]) if os.environ.get("FAKE_CODEX_MARKER") else None

if mode == "timeout":
    if marker is not None:
        marker.write_text("started", encoding="utf-8")

    def terminated(_signum, _frame):
        if marker is not None:
            marker.write_text("terminated", encoding="utf-8")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminated)
    while True:
        time.sleep(0.05)

if mode == "refresh_reused":
    print("refresh token was already used", file=sys.stderr)
    raise SystemExit(1)

output_index = sys.argv.index("--output-last-message") + 1
message = "CODEX_HEALTH_OK extra" if mode == "output_mismatch" else "CODEX_HEALTH_OK"
Path(sys.argv[output_index]).write_text(message, encoding="utf-8")
if mode == "success_refresh":
    auth_path = Path(os.environ["CODEX_HOME"]) / "auth.json"
    auth_path.write_text(json.dumps({"credential": "refreshed"}), encoding="utf-8")
raise SystemExit(0)
"""


def fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
    executable.chmod(0o755)
    return executable


def codex_settings(tmp_path: Path) -> WorkerSettings:
    auth_source = tmp_path / "seed-auth.json"
    auth_source.write_text(json.dumps({"credential": "seed"}), encoding="utf-8")
    return WorkerSettings(
        asset_worker_hmac_secret="worker-secret-that-is-at-least-32-bytes",
        codex_binary=str(fake_codex(tmp_path)),
        codex_auth_source=auth_source,
        codex_runtime_home=tmp_path / "runtime-home",
    )


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"refresh token was already used", ("EXPIRED", "AUTH_REFRESH_REUSED")),
        (b"server returned token_expired", ("EXPIRED", "AUTH_UNAUTHORIZED")),
        (b"401 Unauthorized", ("EXPIRED", "AUTH_UNAUTHORIZED")),
        (b"HTTP 429 rate limit", ("PRESENT", "RATE_LIMITED")),
        (b"UnknownIssuer", ("PRESENT", "NETWORK_TLS")),
        (b"request timed out", ("PRESENT", "NETWORK_TIMEOUT")),
        (b"unclassified failure", ("PRESENT", "PROBE_FAILED")),
    ],
)
def test_classify_codex_error(stderr: bytes, expected: tuple[str, str]) -> None:
    assert classify_codex_error(stderr) == expected


def test_prepare_codex_runtime_home_seeds_once_and_enforces_private_modes(
    tmp_path: Path,
) -> None:
    settings = codex_settings(tmp_path)

    runtime_home = prepare_codex_runtime_home(settings)
    runtime_auth = runtime_home / "auth.json"

    assert json.loads(runtime_auth.read_text("utf-8")) == {"credential": "seed"}
    assert stat.S_IMODE(runtime_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(runtime_auth.stat().st_mode) == 0o600

    runtime_auth.write_text(json.dumps({"credential": "refreshed"}), encoding="utf-8")
    runtime_auth.chmod(0o644)
    settings.codex_auth_source.write_text(
        json.dumps({"credential": "replacement-seed"}), encoding="utf-8"
    )

    assert prepare_codex_runtime_home(settings) == runtime_home
    assert json.loads(runtime_auth.read_text("utf-8")) == {"credential": "refreshed"}
    assert stat.S_IMODE(runtime_auth.stat().st_mode) == 0o600


async def test_inspect_codex_runtime_reports_json_as_present_not_authenticated(
    tmp_path: Path,
) -> None:
    settings = codex_settings(tmp_path)

    health = await inspect_codex_runtime(settings)

    assert health["codex_cli_version"] == "codex-cli fake-1.0"
    assert health["codex_auth_status"] == "PRESENT"
    assert health["codex_probe_status"] == "NOT_RUN"
    assert health["codex_error_code"] is None


async def test_codex_probe_requires_exact_success_and_persists_cli_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = codex_settings(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "success_refresh")
    health = {
        "codex_auth_status": "PRESENT",
        "codex_probe_status": "NOT_RUN",
        "codex_last_success_at": None,
    }

    await run_codex_health_probe(settings, health)

    assert health["codex_auth_status"] == "AUTHENTICATED"
    assert health["codex_probe_status"] == "HEALTHY"
    assert health["codex_error_code"] is None
    assert health["codex_last_success_at"] == health["codex_last_checked_at"]
    runtime_auth = settings.codex_runtime_home / "auth.json"
    assert json.loads(runtime_auth.read_text("utf-8")) == {"credential": "refreshed"}
    assert stat.S_IMODE(runtime_auth.stat().st_mode) == 0o600
    assert json.loads(settings.codex_auth_source.read_text("utf-8")) == {
        "credential": "seed"
    }


async def test_codex_probe_classifies_refresh_token_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = codex_settings(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "refresh_reused")
    health = {
        "codex_auth_status": "PRESENT",
        "codex_probe_status": "NOT_RUN",
        "codex_last_success_at": "2026-08-03T00:00:00+00:00",
    }

    await run_codex_health_probe(settings, health)

    assert health["codex_auth_status"] == "EXPIRED"
    assert health["codex_probe_status"] == "FAILED"
    assert health["codex_error_code"] == "AUTH_REFRESH_REUSED"
    assert health["codex_last_success_at"] == "2026-08-03T00:00:00+00:00"


async def test_codex_probe_rejects_non_exact_success_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = codex_settings(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "output_mismatch")
    health = {
        "codex_auth_status": "PRESENT",
        "codex_probe_status": "NOT_RUN",
        "codex_last_success_at": None,
    }

    await run_codex_health_probe(settings, health)

    assert health["codex_auth_status"] == "PRESENT"
    assert health["codex_probe_status"] == "FAILED"
    assert health["codex_error_code"] == "PROBE_OUTPUT_MISMATCH"
    assert health["codex_last_success_at"] is None


async def test_codex_probe_timeout_terminates_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = codex_settings(tmp_path)
    marker = tmp_path / "probe-process-state"
    monkeypatch.setenv("FAKE_CODEX_MODE", "timeout")
    monkeypatch.setenv("FAKE_CODEX_MARKER", str(marker))
    object.__setattr__(settings, "codex_health_probe_timeout_seconds", 0.25)
    health = {
        "codex_auth_status": "PRESENT",
        "codex_probe_status": "NOT_RUN",
        "codex_last_success_at": None,
    }

    await run_codex_health_probe(settings, health)

    assert health["codex_auth_status"] == "PRESENT"
    assert health["codex_probe_status"] == "FAILED"
    assert health["codex_error_code"] == "PROBE_TIMEOUT"
    assert marker.read_text("utf-8") == "terminated"
