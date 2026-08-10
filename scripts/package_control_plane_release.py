#!/usr/bin/env python3
"""Build and archive a source-bound first-party release without deploying it.

The default mode is a read-only plan.  ``--execute`` is deliberately gated by
an exact confirmation token and a clean, remotely published Git revision.  The
script never invokes Docker Compose, starts application containers, pushes an
image, or runs Git LFS.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GIT_EXECUTABLE = "/usr/bin/git"
DOCKER_EXECUTABLE = "/usr/bin/docker"
OCI_SOURCE = "https://github.com/kiana0512/gpu_control.git"
ACCEPTED_ORIGIN_URLS = frozenset(
    {
        OCI_SOURCE,
        OCI_SOURCE.removesuffix(".git"),
        "git@github.com:kiana0512/gpu_control.git",
    }
)
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_REFERENCE_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
DEFAULT_ASSET_WORKER_VERSION = "1.4.8-retopology-alignment-v3"
OCI_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
OCI_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
ATTESTATION_REFERENCE_TYPE = "attestation-manifest"
SPLIT_SIZE = 128 * 1024 * 1024
MINIMUM_FREE_BYTES = 2 * 1024 * 1024 * 1024
SUBPROCESS_STDERR_LIMIT = 4096
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SENSITIVE_DIAGNOSTIC_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+"),
    re.compile(r"(?i)\b(token|password|secret|api[_-]?key)=([^\s&]+)"),
)


class ReleasePackagingError(ValueError):
    """Raised when a release gate or generated artifact is invalid."""


@dataclass(frozen=True)
class Component:
    key: str
    image_repository: str
    title: str
    context: str
    dockerfile: str
    base_arguments: tuple[str, ...]
    version_argument: str = "GPU_CONTROL_VERSION"
    runtime_version_environment: str | None = "GPU_CONTROL_BUILD_VERSION"

    def release_version(self, version: str, worker_version: str) -> str:
        return worker_version if self.key == "blender-worker" else version

    def image_reference(self, version: str, worker_version: str = DEFAULT_ASSET_WORKER_VERSION) -> str:
        return f"{self.image_repository}:{self.release_version(version, worker_version)}"


COMPONENTS = (
    Component(
        key="api",
        image_repository="gpu-control-api",
        title="GPU Control API",
        context=".",
        dockerfile="apps/api/Dockerfile",
        base_arguments=("PYTHON_BASE_IMAGE",),
    ),
    Component(
        key="scheduler",
        image_repository="gpu-control-scheduler",
        title="GPU Control Scheduler",
        context=".",
        dockerfile="apps/scheduler/Dockerfile",
        base_arguments=("PYTHON_BASE_IMAGE",),
    ),
    Component(
        key="asset-api",
        image_repository="unified-scheduler-asset-api",
        title="GPU Control Asset API",
        context=".",
        dockerfile="apps/asset_api/Dockerfile",
        base_arguments=("PYTHON_BASE_IMAGE",),
    ),
    Component(
        key="web",
        image_repository="gpu-control-web",
        title="GPU Control Web",
        context="apps/web",
        dockerfile="apps/web/Dockerfile",
        base_arguments=("NODE_BASE_IMAGE", "NGINX_BASE_IMAGE"),
        runtime_version_environment=None,
    ),
    Component(
        key="blender-worker",
        image_repository="li3d/blender-worker",
        title="GPU Control Blender Worker",
        context=".",
        dockerfile="apps/blender_worker/Dockerfile",
        base_arguments=("BLENDER_BASE_IMAGE",),
        version_argument="ASSET_WORKER_VERSION",
        runtime_version_environment="ASSET_WORKER_BUILD_VERSION",
    ),
)

BASE_IMAGE_TAGS = {
    "PYTHON_BASE_IMAGE": "python:3.11.13-slim-bookworm",
    "NODE_BASE_IMAGE": "node:22.17.1-alpine3.22",
    "NGINX_BASE_IMAGE": "nginx:1.28.0-alpine",
    "BLENDER_BASE_IMAGE": "li3d/blender-runtime:5.1.2",
}


@dataclass(frozen=True)
class Attestation:
    kind: str
    predicate_type: str
    subject_digest: str
    raw: bytes


@dataclass(frozen=True)
class OfflineOciEvidence:
    attestations: dict[str, Attestation]
    index_digest: str
    image_manifest_digest: str
    config_digest: str


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(  # noqa: S603 -- argv uses fixed git/docker binaries; no shell
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        executable = Path(command[0]).name if command else "subprocess"
        operation = " ".join(command[1:3]) if len(command) > 1 else ""
        label = f"{executable} {operation}".strip()
        diagnostic = _safe_subprocess_stderr(completed.stderr)
        raise ReleasePackagingError(
            f"subprocess failed ({label}, exit {completed.returncode}); stderr: {diagnostic}"
        )
    return completed


def _safe_subprocess_stderr(value: str) -> str:
    """Return a bounded, control-free, credential-redacted subprocess diagnostic."""

    cleaned = ANSI_ESCAPE_PATTERN.sub("", value or "")
    cleaned = "".join(
        character if character in {"\n", "\t"} or character.isprintable() else "�"
        for character in cleaned
    )
    for pattern in SENSITIVE_DIAGNOSTIC_PATTERNS:
        if "authorization" in pattern.pattern.lower():
            cleaned = pattern.sub(r"\1<redacted>", cleaned)
        else:
            cleaned = pattern.sub(r"\1=<redacted>", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return "<empty>"
    if len(cleaned) > SUBPROCESS_STDERR_LIMIT:
        omitted = len(cleaned) - SUBPROCESS_STDERR_LIMIT
        cleaned = cleaned[-SUBPROCESS_STDERR_LIMIT:]
        return f"<truncated {omitted} chars> {cleaned}"
    return cleaned


def _output(command: list[str], *, cwd: Path | None = None) -> str:
    return _run(command, cwd=cwd).stdout.strip()


def _git(repository: Path, *arguments: str) -> str:
    return _output([GIT_EXECUTABLE, "-C", str(repository), *arguments])


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def confirmation_token(
    version: str,
    revision: str,
    worker_version: str | None = None,
) -> str:
    """Bind an execution confirmation to every first-party image version.

    ``None`` retains the historical helper result for callers that only render
    old records.  Current CLI paths always pass the explicit Worker version.
    """

    if worker_version is None:
        return f"PACKAGE_CONTROL_PLANE_{version}_{revision}"
    return f"PACKAGE_GPU_CONTROL_{version}_WORKER_{worker_version}_{revision}"


def _reference_repository(reference: str) -> str:
    without_digest = reference.split("@", 1)[0]
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    return without_digest[:last_colon] if last_colon > last_slash else without_digest


def select_repo_digest(reference: str, repo_digests: list[str]) -> str:
    repository = _reference_repository(reference)
    matches = [
        value
        for value in repo_digests
        if DIGEST_REFERENCE_PATTERN.fullmatch(value) and value.split("@", 1)[0] == repository
    ]
    if len(matches) != 1:
        raise ReleasePackagingError(
            f"{reference} must resolve to one immutable local RepoDigest; found {matches!r}"
        )
    return matches[0]


def _versions_from_text(pyproject: str, package: str, package_lock: str) -> dict[str, str]:
    project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", pyproject)
    version_match = (
        re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project_match.group(1))
        if project_match
        else None
    )
    if version_match is None:
        raise ReleasePackagingError("pyproject.toml is missing project.version")
    python_version = version_match.group(1)
    return {
        "python": python_version,
        "web": str(json.loads(package)["version"]),
        "web_lock": str(json.loads(package_lock)["version"]),
    }


def source_versions(repository: Path) -> dict[str, str]:
    return _versions_from_text(
        (repository / "pyproject.toml").read_text(encoding="utf-8"),
        (repository / "apps/web/package.json").read_text(encoding="utf-8"),
        (repository / "apps/web/package-lock.json").read_text(encoding="utf-8"),
    )


def committed_source_versions(repository: Path, revision: str) -> dict[str, str]:
    values = [
        _git(repository, "show", f"{revision}:{path}")
        for path in ("pyproject.toml", "apps/web/package.json", "apps/web/package-lock.json")
    ]
    return _versions_from_text(*values)


def _required_version(text: str, pattern: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ReleasePackagingError(f"worker release metadata is missing {label}")
    return match.group(1)


def _worker_versions_from_text(
    dockerfile: str,
    environment: str,
    node_environment: str,
    control_compose: str,
    node_compose: str,
) -> dict[str, str]:
    """Return every source-controlled Worker version selector.

    A tag alone is not release identity.  Requiring the Docker build argument,
    both environment templates and both Compose defaults to agree prevents a
    rebuilt node from silently falling back to an older or ``unknown`` Worker.
    """

    return {
        "dockerfile": _required_version(
            dockerfile,
            r"^ARG ASSET_WORKER_VERSION=([^\s]+)$",
            "Dockerfile ASSET_WORKER_VERSION",
        ),
        "environment_version": _required_version(
            environment,
            r"^ASSET_WORKER_VERSION=([^\s]+)$",
            ".env.example ASSET_WORKER_VERSION",
        ),
        "environment_tag": _required_version(
            environment,
            r"^ASSET_WORKER_IMAGE_TAG=([^\s]+)$",
            ".env.example ASSET_WORKER_IMAGE_TAG",
        ),
        "node_environment_version": _required_version(
            node_environment,
            r"^ASSET_WORKER_VERSION=([^\s]+)$",
            ".env.node.example ASSET_WORKER_VERSION",
        ),
        "node_environment_tag": _required_version(
            node_environment,
            r"^ASSET_WORKER_IMAGE_TAG=([^\s]+)$",
            ".env.node.example ASSET_WORKER_IMAGE_TAG",
        ),
        "control_compose_version": _required_version(
            control_compose,
            r"ASSET_WORKER_VERSION: \$\{ASSET_WORKER_VERSION:-([^}]+)\}",
            "control Compose ASSET_WORKER_VERSION",
        ),
        "control_compose_tag": _required_version(
            control_compose,
            r"li3d/blender-worker:\$\{ASSET_WORKER_IMAGE_TAG:-([^}]+)\}",
            "control Compose ASSET_WORKER_IMAGE_TAG",
        ),
        "node_compose_version": _required_version(
            node_compose,
            r"ASSET_WORKER_VERSION: \$\{ASSET_WORKER_VERSION:-([^}]+)\}",
            "node Compose ASSET_WORKER_VERSION",
        ),
        "node_compose_tag": _required_version(
            node_compose,
            r"li3d/blender-worker:\$\{ASSET_WORKER_IMAGE_TAG:-([^}]+)\}",
            "node Compose ASSET_WORKER_IMAGE_TAG",
        ),
    }


def source_worker_versions(repository: Path) -> dict[str, str]:
    paths = (
        "apps/blender_worker/Dockerfile",
        ".env.example",
        ".env.node.example",
        "deploy/control-plane/compose.yaml",
        "deploy/gpu-node/compose.yaml",
    )
    values = tuple((repository / path).read_text(encoding="utf-8") for path in paths)
    return _worker_versions_from_text(
        values[0], values[1], values[2], values[3], values[4]
    )


def committed_source_worker_versions(repository: Path, revision: str) -> dict[str, str]:
    paths = (
        "apps/blender_worker/Dockerfile",
        ".env.example",
        ".env.node.example",
        "deploy/control-plane/compose.yaml",
        "deploy/gpu-node/compose.yaml",
    )
    values = tuple(_git(repository, "show", f"{revision}:{path}") for path in paths)
    return _worker_versions_from_text(
        values[0], values[1], values[2], values[3], values[4]
    )


def _inspect_image(reference: str) -> dict[str, Any]:
    rows = json.loads(_output([DOCKER_EXECUTABLE, "image", "inspect", reference]))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ReleasePackagingError(f"unexpected docker inspect result for {reference}")
    return dict(rows[0])


def resolve_base_images() -> dict[str, str]:
    resolved: dict[str, str] = {}
    for argument, tag in BASE_IMAGE_TAGS.items():
        inspected = _inspect_image(tag)
        repo_digests = [str(value) for value in (inspected.get("RepoDigests") or [])]
        resolved[argument] = select_repo_digest(tag, repo_digests)
    return resolved


def _remote_branch_sha(repository: Path, remote_head: str) -> str:
    output = _output(
        [GIT_EXECUTABLE, "-C", str(repository), "ls-remote", "--exit-code", "origin", remote_head]
    )
    rows = [line.split() for line in output.splitlines() if line.strip()]
    matches = [sha for sha, ref in rows if ref == remote_head]
    if len(matches) != 1 or not REVISION_PATTERN.fullmatch(matches[0]):
        raise ReleasePackagingError(f"origin did not return one full SHA for {remote_head}")
    return matches[0]


def _assert_empty_destination(path: Path, label: str) -> None:
    if path.exists():
        raise ReleasePackagingError(f"{label} already exists; refusing to overwrite: {path}")
    if not path.parent.is_dir():
        raise ReleasePackagingError(f"{label} parent directory does not exist: {path.parent}")
    if shutil.disk_usage(path.parent).free < MINIMUM_FREE_BYTES:
        raise ReleasePackagingError(f"{label} parent has less than 2 GiB free: {path.parent}")


def _ensure_repository_child(repository: Path, path: Path) -> Path:
    try:
        return path.relative_to(repository)
    except ValueError as exc:
        raise ReleasePackagingError(
            f"Git LFS output must be inside the repository: {path}"
        ) from exc


def preflight(
    repository: Path,
    version: str,
    revision: str,
    remote_ref: str,
    remote_head: str,
    archive_dir: Path,
    lfs_dir: Path,
    builder: str,
    sbom_generator: str | None,
    allow_pending_sbom: bool,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> dict[str, Any]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ReleasePackagingError("version must be a concrete semantic version")
    if not VERSION_PATTERN.fullmatch(worker_version):
        raise ReleasePackagingError("worker version must be a concrete semantic version")
    if not REVISION_PATTERN.fullmatch(revision):
        raise ReleasePackagingError("revision must be a full lowercase 40-character Git SHA")
    repository = repository.resolve()
    if _git(repository, "rev-parse", "HEAD") != revision:
        raise ReleasePackagingError("release revision is not current HEAD")
    if _git(repository, "status", "--porcelain", "--untracked-files=normal"):
        raise ReleasePackagingError("repository has uncommitted or untracked release inputs")
    origin_url = _git(repository, "remote", "get-url", "origin")
    if origin_url not in ACCEPTED_ORIGIN_URLS:
        raise ReleasePackagingError(f"unexpected origin repository: {origin_url}")
    local_remote_sha = _git(repository, "rev-parse", "--verify", remote_ref)
    remote_sha = _remote_branch_sha(repository, remote_head)
    if remote_sha != local_remote_sha:
        raise ReleasePackagingError(
            f"local {remote_ref} ({local_remote_sha}) is stale versus origin ({remote_sha})"
        )
    ancestor = _run(
        [
            GIT_EXECUTABLE,
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            revision,
            remote_ref,
        ],
        check=False,
    )
    if ancestor.returncode != 0:
        raise ReleasePackagingError(f"release revision is not published in {remote_ref}")
    versions = source_versions(repository)
    committed_versions = committed_source_versions(repository, revision)
    if set(versions.values()) != {version} or committed_versions != versions:
        raise ReleasePackagingError(
            f"source versions are not aligned: working={versions!r}, committed={committed_versions!r}"
        )
    worker_versions = source_worker_versions(repository)
    committed_worker_versions = committed_source_worker_versions(repository, revision)
    if set(worker_versions.values()) != {worker_version} or committed_worker_versions != worker_versions:
        raise ReleasePackagingError(
            "Worker source versions are not aligned: "
            f"working={worker_versions!r}, committed={committed_worker_versions!r}"
        )
    archive_dir = archive_dir.resolve()
    lfs_dir = lfs_dir.resolve()
    _assert_empty_destination(archive_dir, "archive output")
    _assert_empty_destination(lfs_dir, "Git LFS output")
    lfs_relative = _ensure_repository_child(repository, lfs_dir)
    proposed_part = lfs_relative / f"gpu-control-control-plane-{version}-images.tar.gz.part-00"
    lfs_attribute = _git(repository, "check-attr", "filter", "--", proposed_part.as_posix())
    if not lfs_attribute.endswith(": filter: lfs"):
        raise ReleasePackagingError(f"release part path is not covered by Git LFS: {proposed_part}")
    buildx_version = _output([DOCKER_EXECUTABLE, "buildx", "version"])
    builder_details = _output([DOCKER_EXECUTABLE, "buildx", "inspect", builder])
    if any(
        re.search(rf"(?m)^\s*{re.escape(feature)}:\s+true\s*$", builder_details) is None
        for feature in ("OCI exporter", "Docker exporter")
    ):
        raise ReleasePackagingError("Buildx builder does not expose OCI and Docker exporters")
    if sbom_generator is None and not allow_pending_sbom:
        raise ReleasePackagingError(
            "no digest-pinned SBOM generator was supplied; use --sbom-generator or explicitly "
            "acknowledge the non-release candidate with --allow-pending-sbom"
        )
    if sbom_generator is not None:
        if not DIGEST_REFERENCE_PATTERN.fullmatch(sbom_generator):
            raise ReleasePackagingError("SBOM generator must be an immutable name@sha256 reference")
        _inspect_image(sbom_generator)
    for component in COMPONENTS:
        reference = component.image_reference(version, worker_version)
        existing = _run(
            [DOCKER_EXECUTABLE, "image", "inspect", reference],
            check=False,
        )
        if existing.returncode == 0:
            raise ReleasePackagingError(
                f"candidate image already exists; refusing to overwrite ambiguous tag: {reference}"
            )
    base_images = resolve_base_images()
    return {
        "schema_version": "gpu-control-release-packaging-preflight.v1",
        "version": version,
        "worker_version": worker_version,
        "revision": revision,
        "origin_url": origin_url,
        "remote_ref": remote_ref,
        "remote_head": remote_head,
        "remote_sha": remote_sha,
        "source_versions": versions,
        "worker_source_versions": worker_versions,
        "buildx_version": buildx_version,
        "builder": builder,
        "base_images": base_images,
        "sbom_generator": sbom_generator or "PENDING_PINNED_GENERATOR",
        "sbom_status": "ENABLED" if sbom_generator else "PENDING_SBOM",
        "lfs_attribute": lfs_attribute,
    }


def _build_command_prefix(
    repository: Path,
    component: Component,
    version: str,
    revision: str,
    builder: str,
    base_images: dict[str, str],
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> list[str]:
    component_version = component.release_version(version, worker_version)
    command = [
        DOCKER_EXECUTABLE,
        "buildx",
        "build",
        "--builder",
        builder,
        "--progress=plain",
        "--platform=linux/amd64",
        "--file",
        str(repository / component.dockerfile),
        "--tag",
        component.image_reference(version, worker_version),
        "--build-arg",
        f"{component.version_argument}={component_version}",
        "--build-arg",
        f"GPU_CONTROL_REVISION={revision}",
    ]
    for argument in component.base_arguments:
        command.extend(["--build-arg", f"{argument}={base_images[argument]}"])
    return command


def oci_build_command(
    repository: Path,
    component: Component,
    version: str,
    revision: str,
    builder: str,
    base_images: dict[str, str],
    oci_tar: Path,
    metadata_path: Path,
    cache_dir: Path,
    sbom_generator: str | None,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> list[str]:
    """Create the attested OCI export and populate a reusable local build cache."""

    command = _build_command_prefix(
        repository,
        component,
        version,
        revision,
        builder,
        base_images,
        worker_version,
    )
    command.append("--provenance=mode=max")
    if sbom_generator is not None:
        command.append(f"--attest=type=sbom,generator={sbom_generator}")
    command.extend(
        [
            f"--metadata-file={metadata_path}",
            f"--cache-to=type=local,dest={cache_dir},mode=max",
            f"--output=type=oci,dest={oci_tar}",
            str(repository / component.context),
        ]
    )
    return command


def docker_build_command(
    repository: Path,
    component: Component,
    version: str,
    revision: str,
    builder: str,
    base_images: dict[str, str],
    docker_tar: Path,
    cache_dir: Path,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> list[str]:
    """Create a Docker-loadable tar without an attestation manifest list."""

    command = _build_command_prefix(
        repository,
        component,
        version,
        revision,
        builder,
        base_images,
        worker_version,
    )
    command.extend(
        [
            "--provenance=false",
            f"--cache-from=type=local,src={cache_dir}",
            f"--output=type=docker,dest={docker_tar}",
            str(repository / component.context),
        ]
    )
    return command


def _tar_member_bytes(archive: tarfile.TarFile, member_name: str) -> bytes:
    try:
        member = archive.getmember(member_name)
    except KeyError as exc:
        raise ReleasePackagingError(f"OCI archive is missing {member_name}") from exc
    if not member.isfile() or member.size < 1:
        raise ReleasePackagingError(f"OCI archive member is not a non-empty file: {member_name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ReleasePackagingError(f"cannot read OCI archive member: {member_name}")
    return stream.read()


def _oci_blob(archive: tarfile.TarFile, digest: str) -> bytes:
    algorithm, separator, value = digest.partition(":")
    if separator != ":" or algorithm != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReleasePackagingError(f"unsupported OCI digest: {digest}")
    raw = _tar_member_bytes(archive, f"blobs/sha256/{value}")
    if _sha256_bytes(raw) != value:
        raise ReleasePackagingError(f"OCI blob content does not match descriptor: {digest}")
    return raw


def docker_archive_config_digest(path: Path, reference: str) -> str:
    """Return and verify the config digest embedded in one Docker archive."""

    with tarfile.open(path, mode="r:*") as archive:
        manifest = json.loads(_tar_member_bytes(archive, "manifest.json"))
        if not isinstance(manifest, list):
            raise ReleasePackagingError("Docker archive manifest is not a list")
        candidates = [
            entry
            for entry in manifest
            if isinstance(entry, dict)
            and reference in [str(value) for value in entry.get("RepoTags") or []]
        ]
        if len(candidates) != 1:
            raise ReleasePackagingError(
                f"Docker archive must contain exactly one manifest for {reference}"
            )
        config_path = str(candidates[0].get("Config") or "")
        config_raw = _tar_member_bytes(archive, config_path)
        digest = _sha256_bytes(config_raw)
        accepted_paths = {f"{digest}.json", f"blobs/sha256/{digest}"}
        if config_path not in accepted_paths:
            raise ReleasePackagingError(
                f"{reference} Docker config path does not match its content digest"
            )
        if not isinstance(json.loads(config_raw), dict):
            raise ReleasePackagingError(f"{reference} Docker config is not a JSON object")
        return f"sha256:{digest}"


def _statement_kind(predicate_type: str) -> str | None:
    lowered = predicate_type.lower()
    if "spdx" in lowered or "cyclonedx" in lowered:
        return "sbom"
    if "slsa.dev/provenance" in lowered or "in-toto.io/provenance" in lowered:
        return "provenance"
    return None


def extract_offline_attestations(
    oci_path: Path,
    *,
    require_sbom: bool,
) -> OfflineOciEvidence:
    with tarfile.open(oci_path, mode="r:*") as archive:
        index_raw = _tar_member_bytes(archive, "index.json")
        index = json.loads(index_raw)
        descriptors = index.get("manifests") if isinstance(index, dict) else None
        if not isinstance(descriptors, list):
            raise ReleasePackagingError("OCI index does not contain manifests")

        # Current Buildx OCI exports wrap the requested single-platform image
        # and its attestation manifest in one additional image index. Older
        # Buildx releases emitted those descriptors directly at the root. Walk
        # only an unambiguous single-index chain so both layouts are accepted
        # without weakening the one-image fail-closed contract.
        seen_indexes: set[str] = set()
        while True:
            nested_indexes = [
                descriptor
                for descriptor in descriptors
                if isinstance(descriptor, dict)
                and str(descriptor.get("mediaType")) in OCI_INDEX_MEDIA_TYPES
                and dict(descriptor.get("annotations") or {}).get(
                    "vnd.docker.reference.type"
                )
                != ATTESTATION_REFERENCE_TYPE
            ]
            direct_images = [
                descriptor
                for descriptor in descriptors
                if isinstance(descriptor, dict)
                and str(descriptor.get("mediaType")) in OCI_MANIFEST_MEDIA_TYPES
                and dict(descriptor.get("annotations") or {}).get(
                    "vnd.docker.reference.type"
                )
                != ATTESTATION_REFERENCE_TYPE
            ]
            if direct_images:
                if nested_indexes:
                    raise ReleasePackagingError(
                        "OCI index mixes nested indexes with image manifests"
                    )
                break
            if len(nested_indexes) != 1:
                raise ReleasePackagingError(
                    "OCI index must contain exactly one image or nested image index"
                )
            nested_digest = str(nested_indexes[0].get("digest") or "")
            if nested_digest in seen_indexes:
                raise ReleasePackagingError("OCI index contains a descriptor cycle")
            seen_indexes.add(nested_digest)
            nested_raw = _oci_blob(archive, nested_digest)
            nested = json.loads(nested_raw)
            descriptors = nested.get("manifests") if isinstance(nested, dict) else None
            if not isinstance(descriptors, list):
                raise ReleasePackagingError("nested OCI index does not contain manifests")

        image_descriptors = [
            descriptor
            for descriptor in descriptors
            if isinstance(descriptor, dict)
            and str(descriptor.get("mediaType")) in OCI_MANIFEST_MEDIA_TYPES
            and dict(descriptor.get("annotations") or {}).get("vnd.docker.reference.type")
            != ATTESTATION_REFERENCE_TYPE
        ]
        if len(image_descriptors) != 1:
            raise ReleasePackagingError(
                "OCI index must contain exactly one non-attestation image manifest"
            )
        image_manifest_digest = str(image_descriptors[0].get("digest") or "")
        image_manifest_raw = _oci_blob(archive, image_manifest_digest)
        image_manifest = json.loads(image_manifest_raw)
        config_descriptor = (
            image_manifest.get("config") if isinstance(image_manifest, dict) else None
        )
        if not isinstance(config_descriptor, dict):
            raise ReleasePackagingError("OCI image manifest does not contain a config descriptor")
        config_digest = str(config_descriptor.get("digest") or "")
        config_raw = _oci_blob(archive, config_digest)
        if not isinstance(json.loads(config_raw), dict):
            raise ReleasePackagingError("OCI image config is not a JSON object")
        results: dict[str, Attestation] = {}
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            annotations = dict(descriptor.get("annotations") or {})
            if annotations.get("vnd.docker.reference.type") != ATTESTATION_REFERENCE_TYPE:
                continue
            subject_digest = str(annotations.get("vnd.docker.reference.digest") or "")
            if subject_digest != image_manifest_digest:
                raise ReleasePackagingError("attestation does not reference an OCI image manifest")
            manifest_raw = _oci_blob(archive, str(descriptor.get("digest") or ""))
            manifest = json.loads(manifest_raw)
            layers = manifest.get("layers") if isinstance(manifest, dict) else None
            if not isinstance(layers, list):
                raise ReleasePackagingError("attestation manifest does not contain layers")
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                raw = _oci_blob(archive, str(layer.get("digest") or ""))
                statement = json.loads(raw)
                if not isinstance(statement, dict):
                    continue
                predicate_type = str(statement.get("predicateType") or "")
                kind = _statement_kind(predicate_type)
                if kind is None:
                    continue
                subjects = statement.get("subject")
                if not isinstance(subjects, list):
                    raise ReleasePackagingError(f"{kind} statement does not contain subjects")
                subject_digests = {
                    f"sha256:{digest}"
                    for subject in subjects
                    if isinstance(subject, dict)
                    for digest in [dict(subject.get("digest") or {}).get("sha256")]
                    if isinstance(digest, str)
                }
                if subject_digest not in subject_digests:
                    raise ReleasePackagingError(
                        f"{kind} subject is not bound to its OCI image manifest"
                    )
                if kind in results:
                    raise ReleasePackagingError(f"OCI archive contains duplicate {kind} statements")
                results[kind] = Attestation(kind, predicate_type, subject_digest, raw)
        if "provenance" not in results:
            raise ReleasePackagingError("OCI archive is missing provenance")
        if require_sbom and "sbom" not in results:
            raise ReleasePackagingError("OCI archive is missing the requested SBOM")
        return OfflineOciEvidence(
            attestations=results,
            index_digest=f"sha256:{_sha256_bytes(index_raw)}",
            image_manifest_digest=image_manifest_digest,
            config_digest=config_digest,
        )


def _validate_image(
    component: Component,
    reference: str,
    inspected: dict[str, Any],
    version: str,
    revision: str,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> dict[str, Any]:
    component_version = component.release_version(version, worker_version)
    config = dict(inspected.get("Config") or {})
    labels = dict(config.get("Labels") or {})
    expected_labels = {
        "org.opencontainers.image.title": component.title,
        "org.opencontainers.image.version": component_version,
        "org.opencontainers.image.revision": revision,
        "org.opencontainers.image.source": OCI_SOURCE,
    }
    for key, expected in expected_labels.items():
        if labels.get(key) != expected:
            raise ReleasePackagingError(
                f"{reference} label {key} is {labels.get(key)!r}, expected {expected!r}"
            )
    environment = {
        value.split("=", 1)[0]: value.split("=", 1)[1]
        for value in config.get("Env") or []
        if "=" in value
    }
    runtime_version_environment = component.runtime_version_environment
    if runtime_version_environment is not None:
        if environment.get(runtime_version_environment) != component_version:
            raise ReleasePackagingError(f"{reference} runtime build version is not aligned")
        if environment.get("GPU_CONTROL_BUILD_REVISION") != revision:
            raise ReleasePackagingError(f"{reference} runtime build revision is not aligned")
    image_id = str(inspected.get("Id") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ReleasePackagingError(f"{reference} does not expose an immutable local image ID")
    return {
        "reference": reference,
        "local_image_id": image_id,
        "repo_digests": [str(value) for value in inspected.get("RepoDigests") or []],
        "oci_labels": expected_labels,
        "runtime_version_env": {
            key: environment.get(key)
            for key in (
                "GPU_CONTROL_BUILD_VERSION",
                "ASSET_WORKER_BUILD_VERSION",
                "GPU_CONTROL_BUILD_REVISION",
            )
            if key in environment
        },
        "registry_manifest_digest": "PENDING_REGISTRY_PUSH",
    }


def validate_docker_oci_config_identity(
    reference: str,
    docker_config_digest: str,
    oci_config_digest: str,
) -> None:
    """Fail unless the Docker-loadable export and attested OCI export share one config."""

    if docker_config_digest != oci_config_digest:
        raise ReleasePackagingError(
            f"{reference} Docker archive config does not match OCI config digest: "
            f"{docker_config_digest} != {oci_config_digest}"
        )


def _gzip_file(source: Path, destination: Path) -> None:
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=outgoing, compresslevel=6, mtime=0
        ) as zipped:
            shutil.copyfileobj(incoming, zipped, length=1024 * 1024)
    with gzip.open(destination, "rb") as stream:
        while stream.read(1024 * 1024):
            pass


def _split_archive(source: Path, destination: Path) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    with source.open("rb") as stream:
        index = 0
        while chunk := stream.read(SPLIT_SIZE):
            if index > 99:
                raise ReleasePackagingError("archive requires more than 100 parts")
            part = destination / f"{source.name}.part-{index:02d}"
            part.write_bytes(chunk)
            digest = _sha256_bytes(chunk)
            parts.append(
                {
                    "path": part.name,
                    "size": len(chunk),
                    "sha256": digest,
                    "lfs_oid_candidate": f"sha256:{digest}",
                    "lfs_pointer_status": "NOT_YET_STAGED_OR_PUSHED",
                }
            )
            index += 1
    if not parts:
        raise ReleasePackagingError("cannot split an empty archive")
    return parts


def _release_readme(evidence: dict[str, Any]) -> str:
    images = "\n".join(
        f"| `{value['reference']}` | `{value['local_image_id']}` | `PENDING_REGISTRY_PUSH` |"
        for value in evidence["images"].values()
    )
    parts = "\n".join(
        f"- `{part['path']}` — `{part['sha256']}`, {part['size']} bytes"
        for part in evidence["archive"]["parts"]
    )
    sbom_status = evidence["attestations"]["sbom_status"]
    return f"""# GPU Control control-plane {evidence["version"]} candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `{evidence["revision"]}`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
{images}

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `{evidence["version"]}` and the Blender Worker uses
`{evidence["worker_version"]}`. Each component uses one
attested OCI solve followed by a Docker-loadable solve that imports the first solve's local cache.
The config bytes inside each Docker archive must hash to the attested OCI config digest; a mismatch
fails closed. Docker Engine 29 with the containerd image store may expose a local manifest/content
identity as `.Id`, so that engine-local value is recorded but is not misidentified as a config digest.
OCI labels and each applicable runtime build-version environment were checked before the combined
`docker image save` archive was created. No Compose command, service restart, production migration,
registry push, or Git LFS push is performed by the packager.

## Offline attestation state

- BuildKit provenance: `VERIFIED_OFFLINE_OCI`
- SBOM: `{sbom_status}`
- Registry-bound SBOM/manifest identity: `PENDING_REGISTRY_PUSH`

An offline OCI digest is evidence about the local OCI export only. It is **not** a registry digest
and must never be copied into the registry fields in the V4.1 receipt.

## Reassemble

```bash
cat gpu-control-control-plane-{evidence["version"]}-images.tar.gz.part-* \\
  > gpu-control-control-plane-{evidence["version"]}-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-{evidence["version"]}-images.tar.gz
docker image load --input gpu-control-control-plane-{evidence["version"]}-images.tar.gz
```

Combined archive: `{evidence["archive"]["filename"]}`
SHA-256: `{evidence["archive"]["sha256"]}`
Size: `{evidence["archive"]["size"]}` bytes

## Git LFS candidate parts

{parts}

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
"""


def execute_package(
    repository: Path,
    version: str,
    revision: str,
    archive_dir: Path,
    lfs_dir: Path,
    builder: str,
    preflight_evidence: dict[str, Any],
    sbom_generator: str | None,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> dict[str, Any]:
    archive_dir = archive_dir.resolve()
    lfs_dir = lfs_dir.resolve()
    base_images = dict(preflight_evidence["base_images"])
    built_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017 -- host plan uses Python 3.10
    with tempfile.TemporaryDirectory(prefix=f"gpu-control-{version}-build-") as temporary:
        staging = Path(temporary)
        component_evidence: dict[str, Any] = {}
        docker_tars: list[Path] = []
        for component in COMPONENTS:
            docker_tar = staging / f"{component.key}.docker.tar"
            oci_tar = staging / f"{component.key}.oci.tar"
            metadata_path = staging / f"{component.key}.build-metadata.json"
            cache_dir = staging / f"{component.key}.build-cache"
            oci_command = oci_build_command(
                repository,
                component,
                version,
                revision,
                builder,
                base_images,
                oci_tar,
                metadata_path,
                cache_dir,
                sbom_generator,
                worker_version,
            )
            _run(oci_command, cwd=repository)
            for path in (oci_tar, metadata_path):
                if not path.is_file() or path.stat().st_size < 1:
                    raise ReleasePackagingError(f"Buildx did not create {path.name}")
            if not cache_dir.is_dir() or not (cache_dir / "index.json").is_file():
                raise ReleasePackagingError(
                    f"Buildx did not create the reusable cache for {component.key}"
                )
            offline_oci = extract_offline_attestations(
                oci_tar,
                require_sbom=sbom_generator is not None,
            )
            docker_command = docker_build_command(
                repository,
                component,
                version,
                revision,
                builder,
                base_images,
                docker_tar,
                cache_dir,
                worker_version,
            )
            _run(docker_command, cwd=repository)
            if not docker_tar.is_file() or docker_tar.stat().st_size < 1:
                raise ReleasePackagingError(f"Buildx did not create {docker_tar.name}")
            docker_config_digest = docker_archive_config_digest(
                docker_tar,
                component.image_reference(version, worker_version),
            )
            validate_docker_oci_config_identity(
                component.image_reference(version, worker_version),
                docker_config_digest,
                offline_oci.config_digest,
            )
            component_evidence[component.key] = {
                "build_metadata_sha256": _sha256_file(metadata_path),
                "oci_export_sha256": _sha256_file(oci_tar),
                "docker_export_sha256": _sha256_file(docker_tar),
                "offline_oci_index_digest": offline_oci.index_digest,
                "oci_image_manifest_digest": offline_oci.image_manifest_digest,
                "oci_config_digest": offline_oci.config_digest,
                "docker_archive_config_digest": docker_config_digest,
                "offline_oci_digest_is_registry_digest": False,
                "solve_strategy": "SPLIT_OCI_ATTESTED_AND_DOCKER_SHARED_CACHE",
                "docker_oci_config_match": False,
                "attestations": {
                    key: {
                        "predicate_type": value.predicate_type,
                        "subject_digest": value.subject_digest,
                        "sha256": _sha256_bytes(value.raw),
                    }
                    for key, value in sorted(offline_oci.attestations.items())
                },
                "_metadata_path": metadata_path,
                "_attestations": offline_oci.attestations,
            }
            docker_tars.append(docker_tar)
        for docker_tar in docker_tars:
            _run([DOCKER_EXECUTABLE, "image", "load", "--input", str(docker_tar)])
        image_evidence: dict[str, Any] = {}
        image_ids: set[str] = set()
        inspect_payloads: dict[str, bytes] = {}
        for component in COMPONENTS:
            reference = component.image_reference(version, worker_version)
            inspected = _inspect_image(reference)
            validated = _validate_image(
                component,
                reference,
                inspected,
                version,
                revision,
                worker_version,
            )
            component_details = component_evidence[component.key]
            oci_config_digest = str(component_details["oci_config_digest"])
            if validated["local_image_id"] in image_ids:
                raise ReleasePackagingError(
                    "multiple components resolved to the same local image ID"
                )
            image_ids.add(validated["local_image_id"])
            validated["oci_image_manifest_digest"] = component_details["oci_image_manifest_digest"]
            validated["oci_config_digest"] = oci_config_digest
            validated["docker_archive_config_digest"] = component_details[
                "docker_archive_config_digest"
            ]
            validated["local_image_id_semantics"] = (
                "ENGINE_LOCAL_CONTENT_ID_NOT_ASSUMED_CONFIG_DIGEST"
            )
            validated["docker_oci_config_match"] = True
            component_details["docker_oci_config_match"] = True
            image_evidence[component.key] = validated
            inspect_payloads[component.key] = _json_bytes(inspected)
        archive_parent_staging = Path(
            tempfile.mkdtemp(prefix=f".{archive_dir.name}.", dir=archive_dir.parent)
        )
        lfs_parent_staging = Path(tempfile.mkdtemp(prefix=f".{lfs_dir.name}.", dir=lfs_dir.parent))
        try:
            evidence_dir = archive_parent_staging / "evidence"
            evidence_dir.mkdir()
            for component in COMPONENTS:
                details = component_evidence[component.key]
                (evidence_dir / f"{component.key}.inspect.json").write_bytes(
                    inspect_payloads[component.key]
                )
                metadata_path = details.pop("_metadata_path")
                shutil.copyfile(
                    metadata_path, evidence_dir / f"{component.key}.build-metadata.json"
                )
                attestations = details.pop("_attestations")
                for kind, statement in attestations.items():
                    (evidence_dir / f"{component.key}.{kind}.intoto.json").write_bytes(
                        statement.raw
                    )
            uncompressed = staging / f"gpu-control-control-plane-{version}-images.tar"
            archive_name = f"gpu-control-control-plane-{version}-images.tar.gz"
            combined_archive = archive_parent_staging / archive_name
            _run(
                [
                    DOCKER_EXECUTABLE,
                    "image",
                    "save",
                    "--output",
                    str(uncompressed),
                    *[
                        component.image_reference(version, worker_version)
                        for component in COMPONENTS
                    ],
                ]
            )
            if not uncompressed.is_file() or uncompressed.stat().st_size < 1:
                raise ReleasePackagingError("docker image save produced no archive")
            _gzip_file(uncompressed, combined_archive)
            archive_sha256 = _sha256_file(combined_archive)
            parts = _split_archive(combined_archive, lfs_parent_staging)
            evidence: dict[str, Any] = {
                "schema_version": "gpu-control-release-candidate.v2",
                "release_status": "CANDIDATE_ARCHIVE_ONLY",
                "production_accepted": False,
                "deployed": False,
                "version": version,
                "worker_version": worker_version,
                "revision": revision,
                "built_at": built_at,
                "source": {
                    "repository": OCI_SOURCE,
                    "remote_ref": preflight_evidence["remote_ref"],
                    "remote_sha": preflight_evidence["remote_sha"],
                    "versions": preflight_evidence["source_versions"],
                    "worker_versions": preflight_evidence["worker_source_versions"],
                },
                "builder": {
                    "name": builder,
                    "buildx_version": preflight_evidence["buildx_version"],
                    "base_images": base_images,
                },
                "images": image_evidence,
                "offline_oci_exports": component_evidence,
                "attestations": {
                    "provenance_status": "VERIFIED_OFFLINE_OCI",
                    "sbom_status": (
                        "VERIFIED_OFFLINE_OCI"
                        if sbom_generator is not None
                        else "PENDING_PINNED_SBOM_GENERATOR"
                    ),
                    "registry_binding_status": "PENDING_REGISTRY_PUSH",
                    "strict_release_identity_status": "PENDING_REGISTRY_SBOM_BINDING",
                },
                "archive": {
                    "filename": archive_name,
                    "size": combined_archive.stat().st_size,
                    "sha256": archive_sha256,
                    "split_size_bytes": SPLIT_SIZE,
                    "parts": parts,
                },
                "git_lfs": {
                    "directory": str(lfs_dir.relative_to(repository)),
                    "tracking_rule_verified": True,
                    "pointers_staged": False,
                    "objects_pushed": False,
                },
                "forbidden_actions_performed": [],
                "actions_not_performed": [
                    "docker compose up/restart",
                    "production service restart",
                    "database migration",
                    "registry push",
                    "git commit/push",
                    "git lfs add/push",
                ],
            }
            evidence_raw = _json_bytes(evidence)
            (archive_parent_staging / "release-candidate-evidence.json").write_bytes(evidence_raw)
            (lfs_parent_staging / "release-candidate-evidence.json").write_bytes(evidence_raw)
            checksums = [f"{archive_sha256}  {archive_name}"]
            checksums.extend(f"{part['sha256']}  {part['path']}" for part in parts)
            (lfs_parent_staging / "SHA256SUMS.txt").write_text(
                "\n".join(checksums) + "\n", encoding="utf-8"
            )
            (lfs_parent_staging / "README.md").write_text(
                _release_readme(evidence), encoding="utf-8"
            )
            archive_parent_staging.replace(archive_dir)
            lfs_parent_staging.replace(lfs_dir)
        except Exception:
            shutil.rmtree(archive_parent_staging, ignore_errors=True)
            shutil.rmtree(lfs_parent_staging, ignore_errors=True)
            raise
    return evidence


def plan(
    repository: Path,
    version: str,
    revision: str,
    archive_dir: Path,
    lfs_dir: Path,
    builder: str,
    sbom_generator: str | None,
    worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> dict[str, Any]:
    placeholder_bases = {
        key: f"<{tag}-resolved-to-name@sha256>" for key, tag in BASE_IMAGE_TAGS.items()
    }
    commands: dict[str, dict[str, list[str]]] = {}
    for component in COMPONENTS:
        cache_dir = Path(f"<temporary>/{component.key}.build-cache")
        commands[component.key] = {
            "oci_attested": oci_build_command(
                repository,
                component,
                version,
                revision,
                builder,
                placeholder_bases,
                Path(f"<temporary>/{component.key}.oci.tar"),
                Path(f"<temporary>/{component.key}.build-metadata.json"),
                cache_dir,
                sbom_generator,
                worker_version,
            ),
            "docker_loadable": docker_build_command(
                repository,
                component,
                version,
                revision,
                builder,
                placeholder_bases,
                Path(f"<temporary>/{component.key}.docker.tar"),
                cache_dir,
                worker_version,
            ),
        }
    return {
        "schema_version": "gpu-control-release-packaging-plan.v2",
        "mode": "PLAN_ONLY_NO_MUTATIONS",
        "version": version,
        "worker_version": worker_version,
        "revision": revision,
        "confirmation_token": confirmation_token(version, revision, worker_version),
        "archive_dir": str(archive_dir),
        "git_lfs_dir": str(lfs_dir),
        "builder": builder,
        "sbom_generator": sbom_generator or "PENDING_PINNED_GENERATOR",
        "build_commands": commands,
        "post_build_actions": [
            "validate OCI provenance and optional SBOM subjects",
            "extract the OCI image config digest",
            "load five candidate image tars without starting containers",
            "require every Docker archive config digest to equal its OCI config digest",
            "validate OCI labels and build-version metadata",
            "docker image save five images, deterministic gzip, split into 128 MiB parts",
            "write SHA256SUMS, evidence, README, and Git LFS candidate paths",
        ],
        "never_performed": [
            "docker compose up/restart",
            "container start",
            "production mutation",
            "registry push",
            "git push",
            "git lfs push",
        ],
    }


def default_lfs_directory(repository: Path, version: str) -> Path:
    """Keep image parts isolated from retained test and release evidence."""

    return repository / "artifacts" / "control-plane" / version / "release-parts"


def main() -> int:
    repository_default = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=repository_default)
    parser.add_argument("--version", required=True)
    parser.add_argument("--worker-version", default=DEFAULT_ASSET_WORKER_VERSION)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--remote-ref", default="origin/main")
    parser.add_argument("--remote-head", default="refs/heads/main")
    parser.add_argument("--builder", default="default")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--lfs-dir", type=Path)
    parser.add_argument("--sbom-generator")
    parser.add_argument("--allow-pending-sbom", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    repository = args.repository.resolve()
    archive_dir = (
        args.archive_dir.resolve()
        if args.archive_dir
        else Path(tempfile.gettempdir()) / f"gpu-control-control-plane-{args.version}-candidate"
    )
    lfs_dir = (
        args.lfs_dir.resolve() if args.lfs_dir else default_lfs_directory(repository, args.version)
    )
    try:
        if not args.preflight and not args.execute:
            payload = plan(
                repository,
                args.version,
                args.revision,
                archive_dir,
                lfs_dir,
                args.builder,
                args.sbom_generator,
                args.worker_version,
            )
        else:
            if args.execute and args.confirm != confirmation_token(
                args.version,
                args.revision,
                args.worker_version,
            ):
                raise ReleasePackagingError("--execute requires the exact plan confirmation token")
            preflight_evidence = preflight(
                repository,
                args.version,
                args.revision,
                args.remote_ref,
                args.remote_head,
                archive_dir,
                lfs_dir,
                args.builder,
                args.sbom_generator,
                bool(args.allow_pending_sbom),
                args.worker_version,
            )
            payload = (
                execute_package(
                    repository,
                    args.version,
                    args.revision,
                    archive_dir,
                    lfs_dir,
                    args.builder,
                    preflight_evidence,
                    args.sbom_generator,
                    args.worker_version,
                )
                if args.execute
                else preflight_evidence
            )
    except (
        OSError,
        ReleasePackagingError,
        json.JSONDecodeError,
        KeyError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as exc:
        print(f"release packaging failed closed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
