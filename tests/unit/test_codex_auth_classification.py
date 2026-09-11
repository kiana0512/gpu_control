import json
from pathlib import Path

import gpu_control_blender_worker.main as worker_main
import pytest
from test_blender_worker_codex_probe import codex_settings
from test_blender_worker_codex_probe import (
    use_portable_test_skill_manifest as use_portable_test_skill_manifest,
)


@pytest.mark.parametrize("invalid", [None, "", "{bad", "{}", "[]", "null"])
async def test_missing_and_invalid_auth_are_blocked_before_execution(
    tmp_path, monkeypatch, invalid
):
    settings = codex_settings(tmp_path)
    settings.codex_auth_source.unlink()
    if invalid is not None:
        (settings.codex_runtime_home / "auth.json").write_text(invalid)
    expected_status = "MISSING" if invalid is None else "INVALID"
    expected_error = "AUTH_MISSING" if invalid is None else "AUTH_INVALID"
    inspected = await worker_main.inspect_codex_runtime(settings)
    assert inspected["codex_cli_version"] == "codex-cli fake-1.0"
    assert inspected["codex_auth_status"] == expected_status
    assert inspected["codex_probe_status"] == "BLOCKED"
    assert inspected["codex_error_code"] == expected_error

    async def forbidden_exec(*args, **kwargs):
        raise AssertionError("Missing or malformed credentials must not start a model probe")

    monkeypatch.setattr(worker_main.asyncio, "create_subprocess_exec", forbidden_exec)
    health = {"codex_last_success_at": "2026-08-03T00:00:00+00:00"}
    await worker_main.run_codex_health_probe(settings, health)
    assert health["codex_auth_status"] == expected_status
    assert health["codex_probe_status"] == "BLOCKED"
    assert health["codex_error_code"] == expected_error
    assert health["codex_last_success_at"] == "2026-08-03T00:00:00+00:00"


async def test_unreadable_credentials_are_invalid(tmp_path, monkeypatch):
    settings = codex_settings(tmp_path)
    real_read_text = Path.read_text

    def unreadable_auth(path, *args, **kwargs):
        if path.name == "auth.json":
            raise PermissionError("denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable_auth)
    health = {}
    await worker_main.run_codex_health_probe(settings, health)
    assert health["codex_auth_status"] == "INVALID"
    assert health["codex_error_code"] == "AUTH_INVALID"
    assert health["codex_probe_status"] == "BLOCKED"


async def test_local_exec_failure_does_not_invalidate_credentials(tmp_path, monkeypatch):
    settings = codex_settings(tmp_path)

    async def broken_exec(*args, **kwargs):
        raise OSError("Cannot spawn executable")

    monkeypatch.setattr(worker_main.asyncio, "create_subprocess_exec", broken_exec)
    health = {}
    await worker_main.run_codex_health_probe(settings, health)
    assert health["codex_auth_status"] == "PRESENT"
    assert health["codex_error_code"] == "PROBE_RUNTIME_ERROR"
    assert health["codex_probe_status"] == "FAILED"


@pytest.mark.parametrize(
    ("diagnostic", "status", "error"),
    [
        (b"Not logged in", "MISSING", "AUTH_MISSING"),
        (b"Missing bearer or basic authentication", "MISSING", "AUTH_MISSING"),
        (b"401 Unauthorized: invalid_api_key", "INVALID", "AUTH_INVALID"),
        (b"Incorrect API key provided", "INVALID", "AUTH_INVALID"),
        (b"refresh_token_expired", "EXPIRED", "AUTH_UNAUTHORIZED"),
        (b"refresh_token_invalidated", "EXPIRED", "AUTH_UNAUTHORIZED"),
    ],
)
def test_auth_diagnostics(diagnostic, status, error):
    assert worker_main.classify_codex_error(diagnostic) == (status, error)


def test_codex_public_tls_uses_system_store_without_changing_worker_env(tmp_path, monkeypatch):
    settings = codex_settings(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", "/run/certs/lan-ca.crt")
    environment = worker_main.codex_environment(settings)
    assert "SSL_CERT_FILE" not in environment
    assert worker_main.os.environ["SSL_CERT_FILE"] == "/run/certs/lan-ca.crt"
    assert environment["CODEX_HOME"] == str(settings.codex_runtime_home)


async def test_independent_device_login_can_provision_missing_home(tmp_path):
    settings = codex_settings(tmp_path)
    settings.codex_auth_source.unlink()
    health = await worker_main.inspect_codex_runtime(settings)
    assert health["codex_auth_status"] == "MISSING"
    auth_path = settings.codex_runtime_home / "auth.json"
    auth_path.write_text(json.dumps({"credential": "independently-authorized"}))
    await worker_main.run_codex_health_probe(settings, health)
    assert health["codex_auth_status"] == "AUTHENTICATED"
    assert health["codex_probe_status"] == "HEALTHY"
    assert not settings.codex_auth_source.exists()
