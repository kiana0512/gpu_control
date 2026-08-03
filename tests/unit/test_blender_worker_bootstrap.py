import hashlib
from pathlib import Path

import gpu_control_blender_worker.bootstrap as bootstrap_module
import pytest
from gpu_control_blender_worker.bootstrap import (
    BootstrapError,
    bootstrap_from_environment,
    ensure_codex_skill_links,
    validate_approved_skill_contents,
    validate_codex_skill_link,
)


@pytest.fixture(autouse=True)
def use_portable_test_skill_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {
        name: {
            "SKILL.md": hashlib.sha256(f"# {name}\n".encode()).hexdigest(),
        }
        for name in ("blender-pbr-uv", "blender-retopology-compare-iterate")
    }
    monkeypatch.setattr(bootstrap_module, "APPROVED_SKILL_FILE_SHA256", manifest)


def make_skill_root(tmp_path: Path) -> Path:
    root = tmp_path / "release-skills"
    for name in ("blender-pbr-uv", "blender-retopology-compare-iterate"):
        skill = root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def approved_skills(root: Path) -> tuple[Path, Path]:
    return (
        root / "blender-pbr-uv",
        root / "blender-retopology-compare-iterate",
    )


def test_bootstrap_preserves_auth_and_codex_system_skills(tmp_path: Path) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "codex-home"
    system_skills = home / "skills" / ".system"
    system_skills.mkdir(parents=True)
    marker = system_skills / "marker"
    marker.write_text("preserve me", encoding="utf-8")
    auth = home / "auth.json"
    auth.write_text("auth stays untouched", encoding="utf-8")

    ensure_codex_skill_links(home, approved_skills(root))

    assert not (home / "skills").is_symlink()
    assert marker.read_text(encoding="utf-8") == "preserve me"
    for skill in approved_skills(root):
        link = home / "skills" / skill.name
        assert link.is_symlink()
        assert link.resolve() == skill.resolve()
    assert auth.read_text(encoding="utf-8") == "auth stays untouched"


def test_bootstrap_creates_missing_home_and_is_idempotent(tmp_path: Path) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "missing-home"

    ensure_codex_skill_links(home, approved_skills(root))
    ensure_codex_skill_links(home, approved_skills(root))

    assert home.stat().st_mode & 0o777 == 0o700
    assert (home / "skills").is_dir()
    assert not (home / "skills").is_symlink()


def test_bootstrap_rejects_wrong_or_dangling_business_link(
    tmp_path: Path,
) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "codex-home"
    (home / "skills").mkdir(parents=True)
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    skill = root / "blender-retopology-compare-iterate"
    link = home / "skills" / skill.name
    link.symlink_to(wrong, target_is_directory=True)

    with pytest.raises(BootstrapError, match="unapproved target"):
        ensure_codex_skill_links(home, (skill,))

    assert link.resolve() == wrong.resolve()


def test_bootstrap_rejects_unmanaged_business_skill_without_data_loss(
    tmp_path: Path,
) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "codex-home"
    skill = root / "blender-retopology-compare-iterate"
    current = home / "skills" / skill.name
    current.mkdir(parents=True)
    marker = current / "current"
    marker.write_text("current", encoding="utf-8")

    with pytest.raises(BootstrapError, match="already exists and is unmanaged"):
        ensure_codex_skill_links(home, (skill,))

    assert marker.read_text(encoding="utf-8") == "current"


def test_bootstrap_rejects_whole_skills_directory_symlink(
    tmp_path: Path,
) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "codex-home"
    replacement = tmp_path / "replacement-skills"
    replacement.mkdir()
    home.mkdir()
    (home / "skills").symlink_to(replacement, target_is_directory=True)

    with pytest.raises(BootstrapError, match="must not be a symbolic link"):
        ensure_codex_skill_links(home, approved_skills(root))

    assert (home / "skills").is_symlink()
    assert (home / "skills").resolve() == replacement.resolve()


def test_bootstrap_rejects_missing_approved_skill(tmp_path: Path) -> None:
    with pytest.raises(BootstrapError, match="approved business Skill is unavailable"):
        ensure_codex_skill_links(tmp_path / "home", (tmp_path / "missing",))


def test_bootstrap_rejects_in_place_skill_content_drift_without_modifying_it(
    tmp_path: Path,
) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "codex-home"
    skill = root / "blender-pbr-uv"
    ensure_codex_skill_links(home, approved_skills(root))
    drifted = "# operator-edited content\n"
    (skill / "SKILL.md").write_text(drifted, encoding="utf-8")

    with pytest.raises(BootstrapError, match="SHA-256 mismatch"):
        validate_codex_skill_link(home, skill)

    assert (skill / "SKILL.md").read_text("utf-8") == drifted
    assert (home / "skills" / skill.name).is_symlink()


def test_approved_manifest_member_cannot_escape_skill_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_skill_root(tmp_path)
    skill = root / "blender-pbr-uv"
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    (skill / "SKILL.md").unlink()
    (skill / "SKILL.md").symlink_to(outside)
    monkeypatch.setattr(
        bootstrap_module,
        "APPROVED_SKILL_FILE_SHA256",
        {
            skill.name: {
                "SKILL.md": hashlib.sha256(b"outside\n").hexdigest(),
            }
        },
    )

    with pytest.raises(BootstrapError, match="escapes its root"):
        validate_approved_skill_contents(skill)


def test_environment_contract_requires_both_immutable_skills(tmp_path: Path) -> None:
    root = make_skill_root(tmp_path)
    (root / "blender-retopology-compare-iterate" / "SKILL.md").unlink()
    environment = {
        "CODEX_RUNTIME_HOME": str(tmp_path / "home"),
        "CODEX_SKILLS_ROOT": str(root),
        "UV_SKILL_ROOT": str(root / "blender-pbr-uv"),
        "RETOPOLOGY_SKILL_ROOT": str(root / "blender-retopology-compare-iterate"),
    }

    with pytest.raises(BootstrapError, match="missing or escapes its root"):
        bootstrap_from_environment(environment)

    assert not (tmp_path / "home").exists()


def test_environment_contract_installs_exact_business_links(tmp_path: Path) -> None:
    root = make_skill_root(tmp_path)
    home = tmp_path / "home"
    environment = {
        "CODEX_RUNTIME_HOME": str(home),
        "CODEX_SKILLS_ROOT": str(root),
        "UV_SKILL_ROOT": str(root / "blender-pbr-uv"),
        "RETOPOLOGY_SKILL_ROOT": str(root / "blender-retopology-compare-iterate"),
    }

    bootstrap_from_environment(environment)

    assert not (home / "skills").is_symlink()
    for skill in approved_skills(root):
        validate_codex_skill_link(home, skill)
        assert (home / "skills" / skill.name).resolve() == skill.resolve()
