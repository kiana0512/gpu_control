from pathlib import Path
import difflib
import hashlib
import shutil

root = Path(__file__).parent
base = (root / 'baseline/main.py').read_text()
assert hashlib.sha256(base.encode()).hexdigest() == '918da248d79f1e41c572d0148e07c0abff317b8bd587315b9f3f69734c216c87'
patched = base

def replace(old, new, count=1):
    global patched
    assert patched.count(old) == count, (old[:80], patched.count(old))
    patched = patched.replace(old, new)

replace('def codex_environment(settings: WorkerSettings) -> dict[str, str]:', '''def inspect_codex_auth(settings: WorkerSettings) -> tuple[Path | None, str, str | None]:
    """Inspect local credentials without treating their presence as authentication."""
    try:
        codex_home = prepare_codex_runtime_home(settings)
        auth = json.loads((codex_home / "auth.json").read_text("utf-8"))
        if not isinstance(auth, dict) or not auth:
            raise ValueError("empty auth object")
    except FileNotFoundError:
        return None, "MISSING", "AUTH_MISSING"
    except (OSError, ValueError):
        return None, "INVALID", "AUTH_INVALID"
    return codex_home, "PRESENT", None


def codex_environment(settings: WorkerSettings) -> dict[str, str]:''')
replace('''    if "token_expired" in diagnostic or "401 unauthorized" in diagnostic:
        return "EXPIRED", "AUTH_UNAUTHORIZED"
''', '''    if "not logged in" in diagnostic or "missing bearer or basic authentication" in diagnostic:
        return "MISSING", "AUTH_MISSING"
    if "invalid_api_key" in diagnostic or "incorrect api key" in diagnostic:
        return "INVALID", "AUTH_INVALID"
    if any(marker in diagnostic for marker in (
        "token_expired", "401 unauthorized", "refresh_token_expired", "refresh_token_invalidated"
    )):
        return "EXPIRED", "AUTH_UNAUTHORIZED"
''')
replace('''    codex_home: Path | None = None
    try:
        codex_home = prepare_codex_runtime_home(settings)
        auth_path = codex_home / "auth.json"
        auth = json.loads(auth_path.read_text("utf-8"))
        if not isinstance(auth, dict) or not auth:
            raise ValueError("empty auth object")
        auth_status = "PRESENT"
    except (OSError, ValueError, json.JSONDecodeError):
        auth_status = "INVALID"
''', '''    codex_home, auth_status, auth_error = inspect_codex_auth(settings)
''')
replace('''    if not skill_mount_valid:
        return {
''', '''    if auth_error is not None:
        return {
            "codex_cli_version": version,
            "codex_auth_status": auth_status,
            "codex_probe_status": "BLOCKED",
            "codex_probe_latency_ms": None,
            "codex_last_checked_at": checked_at,
            "codex_last_success_at": None,
            "codex_error_code": auth_error,
        }
    if not skill_mount_valid:
        return {
''')
replace('''    process: asyncio.subprocess.Process | None = None
    try:
        codex_home = prepare_codex_runtime_home(settings)
        auth_path = codex_home / "auth.json"
        auth = json.loads(auth_path.read_text("utf-8"))
        if not isinstance(auth, dict) or not auth:
            raise ValueError("empty auth object")
''', '''    process: asyncio.subprocess.Process | None = None
    codex_home, auth_status, auth_error = inspect_codex_auth(settings)
    if auth_error is not None:
        health.update(
            codex_auth_status=auth_status,
            codex_probe_status="BLOCKED",
            codex_error_code=auth_error,
            codex_last_checked_at=checked_at,
            codex_probe_latency_ms=int((time.monotonic() - started) * 1000),
        )
        return
    try:
''')
replace('''                    codex_auth_status=auth_status,
                    codex_probe_status="FAILED",
                    codex_error_code=error_code,
''', '''                    codex_auth_status=auth_status,
                    codex_probe_status="BLOCKED" if auth_status == "MISSING" else "FAILED",
                    codex_error_code=error_code,
''')
replace('''    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        health.update(
            codex_auth_status="INVALID",
            codex_probe_status="FAILED",
            codex_error_code="AUTH_INVALID",
''', '''    except (OSError, RuntimeError, ValueError):
        # Auth was checked above. Spawn/output/local runtime errors do not
        # demonstrate an invalid credential, and must not suggest re-login.
        health.update(
            codex_auth_status="PRESENT",
            codex_probe_status="FAILED",
            codex_error_code="PROBE_RUNTIME_ERROR",
''')
(root / 'patched/main.py').write_text(patched)
(root / 'worker-main.patch').write_text(''.join(difflib.unified_diff(base.splitlines(True), patched.splitlines(True), fromfile='baseline/main.py', tofile='patched/main.py')))
shutil.copyfile(Path('/opt/gpu-control/tests/unit/test_blender_worker_codex_probe.py'), root / 'tests/test_blender_worker_codex_probe.py')
