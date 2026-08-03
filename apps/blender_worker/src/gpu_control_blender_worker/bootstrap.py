"""Fail-closed startup bootstrap for the persistent Blender Worker runtime."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

DEFAULT_CODEX_HOME = Path("/home/assetworker/.codex")
DEFAULT_CODEX_SKILLS_ROOT = Path("/opt/codex/skills")

# This manifest is the in-image trust root for the two externally owned Skills.
# It deliberately mirrors scripts/verify_asset_skills.sh so a host-side in-place
# edit cannot be hidden behind an otherwise correct bind mount or child link.
# Updating any entry is a release decision and must travel with a new Worker
# image/version; runtime environment variables cannot override these digests.
APPROVED_SKILL_FILE_SHA256: Mapping[str, Mapping[str, str]] = {
    "blender-pbr-uv": {
        "SKILL.md": "37de0b496030e7b20151c7d5cbcf340ed4cd2ea36c132e50fc57743f5b4d427e",
        "agents/openai.yaml": (
            "8c2940dcf2a9d0058ff5b8bec03e99185f3758a0f0b3ea60e7ce9345f243d5ac"
        ),
        "references/pbr-uv-standard.md": (
            "06872924e99f2e856c36e3e5e0aefce23c06554e6809125a4fa7ec41970c75cb"
        ),
        "scripts/unwrap_fbx.py": (
            "ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758"
        ),
        "scripts/qa_uv.py": (
            "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
        ),
    },
    "blender-retopology-compare-iterate": {
        "SKILL.md": "e0bb19bcd35ec20a810cdc9f72905e2823052df429dde98110ec8b352ac3d7e4",
        "agents/openai.yaml": (
            "b6cc3e9094c75b8acf778c282def96c620a36347515176d794fb42c1ecbfd81a"
        ),
        "scripts/audit_pair.py": (
            "a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4"
        ),
    },
}


class BootstrapError(RuntimeError):
    """Raised when the persistent runtime cannot be initialized safely."""


def _absolute_path(value: str, *, variable: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise BootstrapError(f"{variable} must be an absolute path")
    return path


def _require_directory(path: Path, *, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(f"{description} is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise BootstrapError(f"{description} is not a directory: {path}")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise BootstrapError(f"approved Skill file cannot be read: {path}") from exc
    return digest.hexdigest()


def validate_approved_skill_contents(approved_skill: Path) -> Path:
    """Verify every release-approved file without changing the mounted Skill."""

    resolved_skill = _require_directory(
        approved_skill, description="approved business Skill"
    )
    expected_files = APPROVED_SKILL_FILE_SHA256.get(resolved_skill.name)
    if expected_files is None:
        raise BootstrapError(f"business Skill is not approved: {resolved_skill.name}")
    for relative_path, expected_digest in expected_files.items():
        candidate = resolved_skill / relative_path
        try:
            resolved_file = candidate.resolve(strict=True)
            resolved_file.relative_to(resolved_skill)
        except (OSError, ValueError) as exc:
            raise BootstrapError(
                f"approved Skill file is missing or escapes its root: "
                f"{resolved_skill.name}/{relative_path}"
            ) from exc
        # Reject file- or parent-directory symlinks. The package mount itself is
        # already resolved above; every manifest member must be a normal file in
        # that exact immutable tree.
        if resolved_file != candidate or not resolved_file.is_file():
            raise BootstrapError(
                f"approved Skill file is not a regular in-root file: "
                f"{resolved_skill.name}/{relative_path}"
            )
        actual_digest = _file_sha256(resolved_file)
        if actual_digest != expected_digest:
            raise BootstrapError(
                f"approved Skill SHA-256 mismatch: "
                f"{resolved_skill.name}/{relative_path}"
            )
    return resolved_skill


def _validate_required_skill(
    value: str | None,
    *,
    variable: str,
    skills_root: Path,
) -> Path:
    if not value:
        raise BootstrapError(f"{variable} is required")
    configured = _absolute_path(value, variable=variable)
    resolved = validate_approved_skill_contents(configured)
    try:
        resolved.relative_to(skills_root)
    except ValueError as exc:
        raise BootstrapError(f"{variable} must resolve below CODEX_SKILLS_ROOT") from exc
    return resolved


def _prepare_codex_home(codex_home: Path) -> Path:
    if codex_home.is_symlink():
        raise BootstrapError("CODEX_RUNTIME_HOME must not be a symbolic link")
    try:
        codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        codex_home.chmod(0o700)
    except OSError as exc:
        raise BootstrapError(f"CODEX_RUNTIME_HOME cannot be prepared: {codex_home}") from exc
    if not codex_home.is_dir():
        raise BootstrapError(f"CODEX_RUNTIME_HOME is not a directory: {codex_home}")
    return codex_home


def validate_codex_skill_link(codex_home: Path, approved_skill: Path) -> None:
    """Validate one business Skill identity without touching system Skills."""

    approved_skill = validate_approved_skill_contents(approved_skill)
    skills_dir = codex_home / "skills"
    if skills_dir.is_symlink() or not skills_dir.is_dir():
        raise BootstrapError("CODEX_HOME skills must be a normal directory")
    skill_link = skills_dir / approved_skill.name
    if not skill_link.is_symlink():
        raise BootstrapError(f"business Skill link is missing or unmanaged: {approved_skill.name}")
    try:
        resolved = skill_link.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(f"business Skill link is dangling: {approved_skill.name}") from exc
    if resolved != approved_skill:
        raise BootstrapError(f"business Skill link has an unapproved target: {approved_skill.name}")


def ensure_codex_skill_links(codex_home: Path, approved_skills: Sequence[Path]) -> None:
    """Install exact business Skill links while preserving ``skills/.system``.

    Codex owns the normal ``CODEX_HOME/skills`` directory and may populate its
    ``.system`` child. GPU Control owns only the named business Skill links.
    Existing unmanaged children are never removed or overwritten.
    """

    codex_home = _prepare_codex_home(codex_home)
    skills_dir = codex_home / "skills"
    if skills_dir.is_symlink():
        raise BootstrapError("CODEX_HOME skills must not be a symbolic link")
    try:
        skills_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise BootstrapError("CODEX_HOME skills directory cannot be prepared") from exc
    if not skills_dir.is_dir():
        raise BootstrapError("CODEX_HOME skills path is not a directory")

    resolved_skills = [validate_approved_skill_contents(skill) for skill in approved_skills]
    names = [skill.name for skill in resolved_skills]
    if len(set(names)) != len(names):
        raise BootstrapError("approved business Skill names must be unique")

    missing: list[tuple[Path, Path]] = []
    for approved_skill in resolved_skills:
        skill_link = skills_dir / approved_skill.name
        if skill_link.is_symlink():
            try:
                current_target = skill_link.resolve(strict=True)
            except OSError as exc:
                raise BootstrapError(
                    f"business Skill link is dangling: {approved_skill.name}"
                ) from exc
            if current_target != approved_skill:
                raise BootstrapError(
                    f"business Skill link has an unapproved target: {approved_skill.name}"
                )
            continue
        if skill_link.exists():
            raise BootstrapError(
                f"business Skill path already exists and is unmanaged: {approved_skill.name}"
            )
        missing.append((skill_link, approved_skill))

    created: list[Path] = []
    try:
        for skill_link, approved_skill in missing:
            skill_link.symlink_to(approved_skill, target_is_directory=True)
            created.append(skill_link)
        for approved_skill in resolved_skills:
            validate_codex_skill_link(codex_home, approved_skill)
    except (OSError, BootstrapError) as exc:
        for skill_link in reversed(created):
            try:
                skill_link.unlink()
            except OSError:
                pass
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError("cannot install business Skill links") from exc


def bootstrap_from_environment(environment: Mapping[str, str]) -> None:
    codex_home = _absolute_path(
        environment.get("CODEX_RUNTIME_HOME", str(DEFAULT_CODEX_HOME)),
        variable="CODEX_RUNTIME_HOME",
    )
    skills_root = _absolute_path(
        environment.get("CODEX_SKILLS_ROOT", str(DEFAULT_CODEX_SKILLS_ROOT)),
        variable="CODEX_SKILLS_ROOT",
    )
    resolved_skills_root = _require_directory(skills_root, description="CODEX_SKILLS_ROOT")
    uv_skill = _validate_required_skill(
        environment.get("UV_SKILL_ROOT"),
        variable="UV_SKILL_ROOT",
        skills_root=resolved_skills_root,
    )
    retopology_skill = _validate_required_skill(
        environment.get("RETOPOLOGY_SKILL_ROOT"),
        variable="RETOPOLOGY_SKILL_ROOT",
        skills_root=resolved_skills_root,
    )
    ensure_codex_skill_links(codex_home, (uv_skill, retopology_skill))


def _exec(command: Sequence[str]) -> NoReturn:
    if not command:
        raise BootstrapError("worker command is required")
    # This module is the container entrypoint; replacing the bootstrap process
    # is intentional and preserves signal delivery to the worker as PID 1.
    os.execvp(command[0], list(command))  # noqa: S606


def main() -> NoReturn:
    try:
        bootstrap_from_environment(os.environ)
        _exec(sys.argv[1:])
    except BootstrapError as exc:
        print(f"gpu-control worker bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc


if __name__ == "__main__":
    main()
