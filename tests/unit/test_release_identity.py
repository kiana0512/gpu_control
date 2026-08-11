import hashlib
import json
from pathlib import Path

from scripts.verify_release_identity import (
    REQUIRED_IMAGE_COMPONENTS,
    _bound_sbom,
    _named_values,
    source_versions,
    source_worker_versions,
    verify,
)

REPOSITORY = Path(__file__).resolve().parents[2]


def test_source_release_versions_match_current_component_versions() -> None:
    assert source_versions(REPOSITORY) == {
        "python": "1.5.12",
        "web": "1.5.11",
        "web_lock": "1.5.11",
    }


def test_control_plane_build_defaults_match_release_version() -> None:
    expected_version = "1.5.12"
    for dockerfile in (
        "apps/api/Dockerfile",
        "apps/scheduler/Dockerfile",
        "apps/web/Dockerfile",
    ):
        contents = (REPOSITORY / dockerfile).read_text(encoding="utf-8")
        assert f"ARG GPU_CONTROL_VERSION={expected_version}" in contents
    asset_api = (REPOSITORY / "apps/asset_api/Dockerfile").read_text(encoding="utf-8")
    assert "ARG GPU_CONTROL_VERSION=1.6.33-retopo-retry-method-v1" in asset_api

    environment = (REPOSITORY / ".env.example").read_text(encoding="utf-8")
    assert f"APP_IMAGE_TAG={expected_version}" in environment
    assert f"GPU_CONTROL_VERSION={expected_version}" in environment

    compose = (REPOSITORY / "deploy/control-plane/compose.yaml").read_text(encoding="utf-8")
    assert compose.count(f"GPU_CONTROL_VERSION: ${{GPU_CONTROL_VERSION:-{expected_version}}}") == 3
    assert compose.count(f"APP_IMAGE_TAG:-{expected_version}") == 2
    assert "GPU_CONTROL_VERSION: ${ASSET_API_VERSION:-1.6.33-retopo-retry-method-v1}" in compose
    assert "ASSET_API_IMAGE_TAG:-1.6.33-retopo-retry-method-v1" in compose
    api_service = compose.split("\n  api:\n", 1)[1].split("\n  asset-api:\n", 1)[0]
    asset_api_service = compose.split("\n  asset-api:\n", 1)[1].split(
        "\n  asset-worker-control:\n", 1
    )[0]
    assert "ASSET_API_VERSION" not in api_service
    assert "GPU_CONTROL_VERSION: ${GPU_CONTROL_VERSION:-1.5.12}" in api_service
    assert "ASSET_API_VERSION:-1.6.33-retopo-retry-method-v1" in asset_api_service


def test_worker_release_versions_and_evidence_contract_are_aligned() -> None:
    versions = source_worker_versions(REPOSITORY)
    assert len(versions) == 9
    assert len(set(versions.values())) == 1
    assert REQUIRED_IMAGE_COMPONENTS == {
        "api",
        "scheduler",
        "asset-api",
        "web",
        "blender-worker",
    }


def test_release_parts_are_tracked_by_git_lfs() -> None:
    attributes = (REPOSITORY / ".gitattributes").read_text(encoding="utf-8")
    assert (
        "artifacts/control-plane/**/release-parts/*.part-* filter=lfs diff=lfs merge=lfs -text"
    ) in attributes
    assert ("artifacts/asset-worker/**/*.part-* filter=lfs diff=lfs merge=lfs -text") in attributes


def test_release_revision_must_be_a_full_git_sha() -> None:
    try:
        verify(REPOSITORY, "1.5.5", "unknown", {}, {})
    except ValueError as exc:
        assert "40-character Git SHA" in str(exc)
    else:
        raise AssertionError("release verification must reject an unbound revision")


def test_release_evidence_names_must_be_unique() -> None:
    try:
        _named_values(["api=one", "api=two"], "--image")
    except ValueError as exc:
        assert "unique COMPONENT=VALUE" in str(exc)
    else:
        raise AssertionError("duplicate component evidence must be rejected")


def test_sbom_must_be_bound_to_the_registry_manifest(tmp_path: Path) -> None:
    digest = "a" * 64
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "registry/gpu-control-api", "digest": {"sha256": digest}}],
        "predicateType": "https://spdx.dev/Document",
        "predicate": {"spdxVersion": "SPDX-2.3"},
    }
    path = tmp_path / "api.intoto.json"
    raw = json.dumps(statement).encode()
    path.write_bytes(raw)
    _, actual = _bound_sbom(path, {f"sha256:{digest}"})
    assert actual == hashlib.sha256(raw).hexdigest()
