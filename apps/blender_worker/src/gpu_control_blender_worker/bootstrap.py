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

# This manifest is the in-image trust root for the three externally owned Skills.
# It deliberately mirrors scripts/verify_asset_skills.sh so a host-side in-place
# edit cannot be hidden behind an otherwise correct bind mount or child link.
# Updating any entry is a release decision and must travel with a new Worker
# image/version; runtime environment variables cannot override these digests.
APPROVED_SKILL_FILE_SHA256: Mapping[str, Mapping[str, str]] = {
    "blender-align-bake-models": {
        "SKILL.md": "5a32c7759ae998504056e41bc14e556675bbbe6b828b72c9c44a8f2918106aba",
        "agents/openai.yaml": (
            "a8e61cb47f50eef2fb97b6f29c5d3f3d900e63154e2c2f6257293c1c2e62962a"
        ),
        "scripts/align_bake_models.py": (
            "ea0588e81fa50772080bc19ff096ee29cb5b6dbc67cdb303b9d32cdbf6a99a78"
        ),
        "scripts/create_synthetic_pair.py": (
            "e214969e47c929f80e4f7b84e474d2872bb512a8de0169e777e634521541e8dc"
        ),
        "scripts/render_alignment_views.py": (
            "cf4cf07003b030f6bb6c3c9023f03cfda38da837973f57dfa2f8eb4a71baba0b"
        ),
        "scripts/validate_bake_pair.py": (
            "d8ee19f1fa0e3c93fbf6aa3f846afe1df7162f9569baabbdfaa9fea9f07ed358"
        ),
    },
    "blender-pbr-uv": {
        "SKILL.md": "2c099600e1281434cb067ef6cd22f92a93decd4cb5632a87646a2e4704b9685c",
        "agents/openai.yaml": (
            "8c2940dcf2a9d0058ff5b8bec03e99185f3758a0f0b3ea60e7ce9345f243d5ac"
        ),
        "references/pbr-uv-standard.md": (
            "06872924e99f2e856c36e3e5e0aefce23c06554e6809125a4fa7ec41970c75cb"
        ),
        "references/mof-wrapper-notes.md": (
            "5264b8f81a40b4604a092cbcb6253bc976d2a03ee815244867d9ecd8696050a1"
        ),
        "scripts/unwrap_fbx.py": (
            "04c09e0907ad8ad3838be2ece177b8c9c4b4d33c151633849bfd6262a70748c9"
        ),
        "scripts/qa_uv.py": (
            "a263d0fc05947d70988317972f9b0bb38e7c85a165274756d3c4dbf4e05f91c3"
        ),
        "scripts/mof_unwrap.py": (
            "70e98027f64b4389ec1f7086bb363e5d4a7a686b9472d17fa840ecb01dbd946d"
        ),
        "scripts/preflight_mof.py": (
            "d4639ebd34128b02496599eef55c21ed1eab295c6117fc234c819003e491db40"
        ),
    },
    "blender-retopology-compare-iterate": {
        "SKILL.md": "7decec51b40620bc053e30d3643bd2f64c37705877a43a22573ffd38ab17b593",
        "agents/openai.yaml": (
            "1b6047328ec5a05c87d909b75849a0ca65ff12c95e9660f357c6fb5e889e4656"
        ),
        "references/high-only-game-topology.md": (
            "9944e4d8c6b35d90454fa4aaa6401f7e4457c8918b264e3dbdaa5021861485f7"
        ),
        "references/n01-n08-training-lessons.md": (
            "edfdc92fe99e08ab6cc2ca7d63852ead42829ac03532147a3189c476301ac297"
        ),
        "references/production-runbook.md": (
            "9c7fdfedd8d0132199fc5b64acd27b1165503663b24456fcef3fa33d16c46cac"
        ),
        "references/validated-batch-retrospective.md": (
            "9af3d1ebbe4ac304d82c65729f1301bf786fe975c69019b2b071065d8ca99558"
        ),
        "scripts/audit_batch_layout.py": (
            "c400add092827aff84b66915200b406d2501a3db6583d458dff6541fc60d4092"
        ),
        "scripts/audit_pair.py": (
            "bbc9990a045284be799df2f56f29b4a52f066c923eda0c65f2a88fe2d3128f1b"
        ),
        "scripts/audit_topology_flow.py": (
            "cd1b9f59f3d8ccc65375e453c881a8776d8fbe4b48e47499754f861f5075b789"
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
    alignment_skill = _validate_required_skill(
        environment.get("ALIGNMENT_SKILL_ROOT"),
        variable="ALIGNMENT_SKILL_ROOT",
        skills_root=resolved_skills_root,
    )
    ensure_codex_skill_links(codex_home, (uv_skill, retopology_skill, alignment_skill))


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
