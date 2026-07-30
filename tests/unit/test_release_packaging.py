import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.package_control_plane_release import (
    COMPONENTS,
    ReleasePackagingError,
    _assert_empty_destination,
    build_command,
    confirmation_token,
    default_lfs_directory,
    extract_offline_attestations,
    select_repo_digest,
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


def _oci_fixture(path: Path, *, valid_subject: bool = True) -> None:
    image_digest, image_manifest = _blob({"schemaVersion": 2, "layers": []})
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
    with tarfile.open(path, "w") as archive:
        _add_tar_file(archive, "index.json", json.dumps(index).encode())
        for digest, raw in (
            (image_digest, image_manifest),
            (attestation_digest, attestation_manifest),
            (sbom_digest, sbom),
            (provenance_digest, provenance),
        ):
            _add_tar_file(archive, f"blobs/sha256/{digest[7:]}", raw)


def test_confirmation_token_binds_version_and_full_revision() -> None:
    assert confirmation_token("1.5.5", REVISION) == f"PACKAGE_CONTROL_PLANE_1.5.5_{REVISION}"


def test_base_image_resolution_requires_one_matching_digest() -> None:
    digest = "1" * 64
    assert select_repo_digest("python:3.11", [f"python@sha256:{digest}"]) == (
        f"python@sha256:{digest}"
    )
    with pytest.raises(ReleasePackagingError, match="one immutable local RepoDigest"):
        select_repo_digest("python:3.11", [f"other@sha256:{digest}"])


def test_build_commands_are_source_bound_and_never_deploy_or_push(tmp_path: Path) -> None:
    bases = {
        "PYTHON_BASE_IMAGE": f"python@sha256:{'1' * 64}",
        "NODE_BASE_IMAGE": f"node@sha256:{'2' * 64}",
        "NGINX_BASE_IMAGE": f"nginx@sha256:{'3' * 64}",
    }
    generator = f"example/sbom-generator@sha256:{'4' * 64}"
    for component in COMPONENTS:
        command = build_command(
            REPOSITORY,
            component,
            "1.5.5",
            REVISION,
            "default",
            bases,
            tmp_path / f"{component.key}.docker.tar",
            tmp_path / f"{component.key}.oci.tar",
            tmp_path / f"{component.key}.json",
            generator,
        )
        joined = " ".join(command)
        assert command[:3] == ["/usr/bin/docker", "buildx", "build"]
        assert "GPU_CONTROL_VERSION=1.5.5" in command
        assert f"GPU_CONTROL_REVISION={REVISION}" in command
        assert "--provenance=mode=max" in command
        assert f"--attest=type=sbom,generator={generator}" in command
        assert "--output=type=docker" in joined
        assert "--output=type=oci" in joined
        for forbidden in (" compose ", " up ", " restart ", "--push", " lfs "):
            assert forbidden not in f" {joined} "


def test_offline_attestations_are_bound_to_oci_manifest(tmp_path: Path) -> None:
    path = tmp_path / "candidate.oci.tar"
    _oci_fixture(path)
    attestations, index_digest = extract_offline_attestations(path, require_sbom=True)
    assert set(attestations) == {"provenance", "sbom"}
    assert attestations["sbom"].subject_digest == attestations["provenance"].subject_digest
    assert index_digest.startswith("sha256:")


def test_offline_attestation_subject_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "candidate.oci.tar"
    _oci_fixture(path, valid_subject=False)
    with pytest.raises(ReleasePackagingError, match="subject is not bound"):
        extract_offline_attestations(path, require_sbom=True)


def test_release_dockerfiles_allow_digest_pinned_base_images() -> None:
    expected = {
        "apps/api/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/scheduler/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/asset_api/Dockerfile": "ARG PYTHON_BASE_IMAGE=",
        "apps/web/Dockerfile": "ARG NODE_BASE_IMAGE=",
    }
    for relative, marker in expected.items():
        dockerfile = (REPOSITORY / relative).read_text(encoding="utf-8")
        assert marker in dockerfile
        assert "FROM ${" in dockerfile
    assert "ARG NGINX_BASE_IMAGE=" in (REPOSITORY / "apps/web/Dockerfile").read_text(
        encoding="utf-8"
    )


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
