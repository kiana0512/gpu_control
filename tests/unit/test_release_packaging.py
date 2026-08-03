import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.package_control_plane_release import (
    COMPONENTS,
    DEFAULT_ASSET_WORKER_VERSION,
    ReleasePackagingError,
    _assert_empty_destination,
    _run,
    confirmation_token,
    default_lfs_directory,
    docker_archive_config_digest,
    docker_build_command,
    extract_offline_attestations,
    oci_build_command,
    plan,
    select_repo_digest,
    source_worker_versions,
    validate_docker_oci_config_identity,
)

REPOSITORY = Path(__file__).resolve().parents[2]
REVISION = "a" * 40


def _blob(payload: object) -> tuple[str, bytes]:
    import hashlib

    raw = json.dumps(payload, sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}", raw


def _add_tar_file(archive: tarfile.TarFile, name: str, raw: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(raw)
    archive.addfile(member, io.BytesIO(raw))


def _oci_fixture(
    path: Path,
    *,
    valid_subject: bool = True,
    nested_index: bool = False,
) -> tuple[str, str]:
    config_digest, image_config = _blob({"architecture": "amd64", "rootfs": {"diff_ids": []}})
    image_digest, image_manifest = _blob(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
            },
            "layers": [],
        }
    )
    subject_digest = image_digest if valid_subject else f"sha256:{'f' * 64}"
    sbom_digest, sbom = _blob(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "candidate", "digest": {"sha256": subject_digest[7:]}}],
            "predicateType": "https://spdx.dev/Document",
            "predicate": {"spdxVersion": "SPDX-2.3"},
        }
    )
    provenance_digest, provenance = _blob(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "candidate", "digest": {"sha256": subject_digest[7:]}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {"buildDefinition": {}},
        }
    )
    attestation_digest, attestation_manifest = _blob(
        {
            "schemaVersion": 2,
            "layers": [
                {"mediaType": "application/vnd.in-toto+json", "digest": sbom_digest},
                {"mediaType": "application/vnd.in-toto+json", "digest": provenance_digest},
            ],
        }
    )
    index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": image_digest,
            },
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": attestation_digest,
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": image_digest,
                },
            },
        ],
    }
    root_index = index
    extra_blobs: tuple[tuple[str, bytes], ...] = ()
    if nested_index:
        nested_digest, nested_raw = _blob(index)
        root_index = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "digest": nested_digest,
                }
            ],
        }
        extra_blobs = ((nested_digest, nested_raw),)
    with tarfile.open(path, "w") as archive:
        _add_tar_file(archive, "index.json", json.dumps(root_index).encode())
        for digest, raw in (
            (config_digest, image_config),
            (image_digest, image_manifest),
            (attestation_digest, attestation_manifest),
            (sbom_digest, sbom),
            (provenance_digest, provenance),
            *extra_blobs,
        ):
            _add_tar_file(archive, f"blobs/sha256/{digest[7:]}", raw)
    return image_digest, config_digest


def _docker_fixture(path: Path, reference: str) -> str:
    config_digest, config_raw = _blob(
        {"architecture": "amd64", "config": {"Labels": {"fixture": "true"}}}
    )
    config_path = f"blobs/sha256/{config_digest[7:]}"
    with tarfile.open(path, "w") as archive:
        _add_tar_file(
            archive,
            "manifest.json",
            json.dumps([{"Config": config_path, "RepoTags": [reference]}]).encode(),
        )
        _add_tar_file(archive, config_path, config_raw)
    return config_digest


def test_confirmation_token_binds_version_and_full_revision() -> None:
    assert confirmation_token("1.5.5", REVISION) == f"PACKAGE_CONTROL_PLANE_1.5.5_{REVISION}"
    assert confirmation_token("1.5.9", REVISION, "1.2.5") == (
        f"PACKAGE_GPU_CONTROL_1.5.9_WORKER_1.2.5_{REVISION}"
    )


def test_base_image_resolution_requires_one_matching_digest() -> None:
    digest = "1" * 64
    assert select_repo_digest("python:3.11", [f"python@sha256:{digest}"]) == (
        f"python@sha256:{digest}"
    )
    with pytest.raises(ReleasePackagingError, match="one immutable local RepoDigest"):
        select_repo_digest("python:3.11", [f"other@sha256:{digest}"])


def test_split_build_commands_are_source_bound_and_never_deploy_or_push(tmp_path: Path) -> None:
    bases = {
        "PYTHON_BASE_IMAGE": f"python@sha256:{'1' * 64}",
        "NODE_BASE_IMAGE": f"node@sha256:{'2' * 64}",
        "NGINX_BASE_IMAGE": f"nginx@sha256:{'3' * 64}",
        "BLENDER_BASE_IMAGE": f"li3d/blender-runtime@sha256:{'5' * 64}",
    }
    generator = f"example/sbom-generator@sha256:{'4' * 64}"
    for component in COMPONENTS:
        cache_dir = tmp_path / f"{component.key}.cache"
        oci_command = oci_build_command(
            REPOSITORY,
            component,
            "1.5.5",
            REVISION,
            "default",
            bases,
            tmp_path / f"{component.key}.oci.tar",
            tmp_path / f"{component.key}.json",
            cache_dir,
            generator,
        )
        docker_command = docker_build_command(
            REPOSITORY,
            component,
            "1.5.5",
            REVISION,
            "default",
            bases,
            tmp_path / f"{component.key}.docker.tar",
            cache_dir,
        )
        for command in (oci_command, docker_command):
            joined = " ".join(command)
            assert command[:3] == ["/usr/bin/docker", "buildx", "build"]
            expected_version_argument = (
                f"ASSET_WORKER_VERSION={DEFAULT_ASSET_WORKER_VERSION}"
                if component.key == "blender-worker"
                else "GPU_CONTROL_VERSION=1.5.5"
            )
            assert expected_version_argument in command
            assert f"GPU_CONTROL_REVISION={REVISION}" in command
            for forbidden in (" compose ", " up ", " restart ", "--push", " lfs "):
                assert forbidden not in f" {joined} "

        oci_joined = " ".join(oci_command)
        assert "--provenance=mode=max" in oci_command
        assert f"--attest=type=sbom,generator={generator}" in oci_command
        assert "--output=type=oci" in oci_joined
        assert "--output=type=docker" not in oci_joined
        assert f"--cache-to=type=local,dest={cache_dir},mode=max" in oci_command

        docker_joined = " ".join(docker_command)
        assert "--provenance=false" in docker_command
        assert not any(value.startswith("--attest=") for value in docker_command)
        assert "--output=type=docker" in docker_joined
        assert "--output=type=oci" not in docker_joined
        assert f"--cache-from=type=local,src={cache_dir}" in docker_command


def test_offline_attestations_are_bound_to_oci_manifest(tmp_path: Path) -> None:
    path = tmp_path / "candidate.oci.tar"
    image_digest, config_digest = _oci_fixture(path)
    evidence = extract_offline_attestations(path, require_sbom=True)
    assert set(evidence.attestations) == {"provenance", "sbom"}
    assert (
        evidence.attestations["sbom"].subject_digest
        == evidence.attestations["provenance"].subject_digest
    )
    assert evidence.index_digest.startswith("sha256:")
    assert evidence.image_manifest_digest == image_digest
    assert evidence.config_digest == config_digest


def test_offline_attestations_support_current_buildx_nested_index(tmp_path: Path) -> None:
    path = tmp_path / "nested-candidate.oci.tar"
    image_digest, config_digest = _oci_fixture(path, nested_index=True)

    evidence = extract_offline_attestations(path, require_sbom=True)

    assert set(evidence.attestations) == {"provenance", "sbom"}
    assert evidence.image_manifest_digest == image_digest
    assert evidence.config_digest == config_digest


def test_offline_attestation_subject_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "candidate.oci.tar"
    _oci_fixture(path, valid_subject=False)
    with pytest.raises(ReleasePackagingError, match="subject is not bound"):
        extract_offline_attestations(path, require_sbom=True)


def test_docker_archive_config_must_equal_oci_config_digest() -> None:
    digest = f"sha256:{'1' * 64}"
    validate_docker_oci_config_identity("gpu-control-api:1.5.5", digest, digest)
    with pytest.raises(ReleasePackagingError, match="does not match OCI config digest"):
        validate_docker_oci_config_identity(
            "gpu-control-api:1.5.5",
            digest,
            f"sha256:{'2' * 64}",
        )


def test_docker_archive_config_digest_is_bound_to_config_bytes(tmp_path: Path) -> None:
    reference = "gpu-control-api:1.5.5"
    path = tmp_path / "candidate.docker.tar"
    expected = _docker_fixture(path, reference)

    assert docker_archive_config_digest(path, reference) == expected


def test_plan_exposes_two_safe_solves_per_component(tmp_path: Path) -> None:
    payload = plan(
        REPOSITORY,
        "1.5.5",
        REVISION,
        tmp_path / "archive",
        tmp_path / "lfs",
        "default",
        None,
    )
    assert payload["schema_version"] == "gpu-control-release-packaging-plan.v2"
    assert payload["mode"] == "PLAN_ONLY_NO_MUTATIONS"
    assert payload["worker_version"] == DEFAULT_ASSET_WORKER_VERSION
    assert payload["confirmation_token"] == confirmation_token(
        "1.5.5", REVISION, DEFAULT_ASSET_WORKER_VERSION
    )
    assert set(payload["build_commands"]) == {
        "api",
        "scheduler",
        "asset-api",
        "web",
        "blender-worker",
    }
    for commands in payload["build_commands"].values():
        assert set(commands) == {"oci_attested", "docker_loadable"}
        oci_joined = " ".join(commands["oci_attested"])
        docker_joined = " ".join(commands["docker_loadable"])
        assert "--output=type=oci" in oci_joined
        assert "--output=type=docker" not in oci_joined
        assert "--output=type=docker" in docker_joined
        assert "--output=type=oci" not in docker_joined
    assert "registry push" in payload["never_performed"]
    assert "container start" in payload["never_performed"]


def test_subprocess_failure_reports_redacted_bounded_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = (
        "x" * 5000
        + "\nAuthorization: Bearer top-secret-token"
        + "\napi_key=second-secret"
        + "\n\x1b[31museful-tail\x1b[0m"
    )

    def fail(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 17, stdout="", stderr=stderr)

    monkeypatch.setattr("scripts.package_control_plane_release.subprocess.run", fail)
    with pytest.raises(ReleasePackagingError) as captured:
        _run(["/usr/bin/docker", "buildx", "build"])
    message = str(captured.value)
    assert "docker buildx build, exit 17" in message
    assert "<truncated " in message
    assert "<redacted>" in message
    assert "top-secret-token" not in message
    assert "second-secret" not in message
    assert "\x1b" not in message
    assert "useful-tail" in message
    assert len(message) < 4300


def test_release_dockerfiles_allow_digest_pinned_base_images() -> None:
    expected = {
        "apps/api/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/scheduler/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/asset_api/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/web/Dockerfile": "ARG NODE_BASE_IMAGE=",
        "apps/blender_worker/Dockerfile": "ARG BLENDER_BASE_IMAGE=",
    }
    for relative, marker in expected.items():
        dockerfile = (REPOSITORY / relative).read_text(encoding="utf-8")
        assert marker in dockerfile
        assert "FROM ${" in dockerfile
    assert "ARG NGINX_BASE_IMAGE=" in (REPOSITORY / "apps/web/Dockerfile").read_text(
        encoding="utf-8"
    )


def test_worker_source_release_selectors_are_aligned() -> None:
    versions = source_worker_versions(REPOSITORY)
    assert len(versions) == 9
    assert len(set(versions.values())) == 1


def test_default_lfs_directory_preserves_existing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_root = tmp_path / "artifacts" / "control-plane" / "1.5.5"
    evidence = version_root / "evidence" / "tests" / "backend.junit.xml"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("retained", encoding="utf-8")
    release_parts = default_lfs_directory(tmp_path, "1.5.5")
    assert release_parts == version_root / "release-parts"
    monkeypatch.setattr("scripts.package_control_plane_release.MINIMUM_FREE_BYTES", 0)
    _assert_empty_destination(release_parts, "Git LFS output")
    assert evidence.read_text(encoding="utf-8") == "retained"
    release_parts.mkdir()
    with pytest.raises(ReleasePackagingError, match="refusing to overwrite"):
        _assert_empty_destination(release_parts, "Git LFS output")
