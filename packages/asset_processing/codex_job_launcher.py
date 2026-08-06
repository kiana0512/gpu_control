#!/usr/bin/env python3
"""Seed one isolated Codex job home, then exec the approved Codex binary.

The upstream retopology package deliberately creates a new CODEX_HOME for
every asset. GPU Control keeps the production credential read-only outside
that directory, so this launcher performs only the control-plane integration
step needed before the unmodified upstream adapter invokes Codex.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    codex_home_value = os.environ.get("CODEX_HOME")
    if not codex_home_value:
        raise SystemExit("CODEX_HOME is required")
    codex_home = Path(codex_home_value).resolve()
    codex_home.mkdir(parents=True, exist_ok=True)

    auth_source = Path(
        os.environ.get("CODEX_AUTH_SOURCE", "/run/secrets/codex-auth.json")
    ).resolve()
    if not auth_source.is_file() or auth_source.stat().st_size <= 0:
        raise SystemExit("approved Codex authentication source is unavailable")
    auth_destination = codex_home / "auth.json"
    temporary = codex_home / ".auth.json.tmp"
    shutil.copyfile(auth_source, temporary)
    temporary.chmod(0o600)
    temporary.replace(auth_destination)
    if sha256(auth_source) != sha256(auth_destination):
        raise SystemExit("Codex authentication copy failed hash verification")

    real_codex = os.environ.get("GPU_CONTROL_REAL_CODEX_BIN", "/usr/local/bin/codex")
    os.execv(  # noqa: S606 - executable is the immutable Worker setting
        real_codex, [real_codex, *sys.argv[1:]]
    )


if __name__ == "__main__":
    main()
