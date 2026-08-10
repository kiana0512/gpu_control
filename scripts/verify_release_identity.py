#!/usr/bin/env python3
"""Fail closed unless source versions and local OCI provenance are aligned."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
DEFAULT_ASSET_WORKER_VERSION = "1.4.14-retopology-uniform-scale-refine-v1"
REQUIRED_IMAGE_COMPONENTS = frozenset({"api", "scheduler", "asset-api", "web", "blender-worker"})
OCI_TITLES = {
    "api": "GPU Control API",
    "scheduler": "GPU Control Scheduler",
    "asset-api": "GPU Control Asset API",
    "web": "GPU Control Web",
    "blender-worker": "GPU Control Blender Worker",
}
OCI_SOURCE = "https://github.com/kiana0512/gpu_control.git"
GIT_EXECUTABLE = "/usr/bin/git"
DOCKER_EXECUTABLE = "/usr/bin/docker"


def _versions_from_text(pyproject: str, package: str, package_lock: str) -> dict[str, str]:
    project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", pyproject)
    version_match = (
        re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project_match.group(1))
        if project_match
        else None
    )
    if version_match is None:
        raise ValueError("pyproject.toml is missing project.version")
    python_version = version_match.group(1)
    web = json.loads(package)
    lock = json.loads(package_lock)
    return {
        "python": python_version,
        "web": str(web["version"]),
        "web_lock": str(lock["version"]),
    }


def source_versions(repository: Path) -> dict[str, str]:
    return _versions_from_text(
        (repository / "pyproject.toml").read_text(encoding="utf-8"),
        (repository / "apps/web/package.json").read_text(encoding="utf-8"),
        (repository / "apps/web/package-lock.json").read_text(encoding="utf-8"),
    )


def _run(command: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 -- fixed git/docker executables and argv, no shell
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def committed_source_versions(repository: Path, revision: str) -> dict[str, str]:
    values = [
        _run([GIT_EXECUTABLE, "-C", str(repository), "show", f"{revision}:{path}"])
        for path in ("pyproject.toml", "apps/web/package.json", "apps/web/package-lock.json")
    ]
    return _versions_from_text(*values)


def _required_worker_version(text: str, pattern: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ValueError(f"worker release metadata is missing {label}")
    return match.group(1)


def _worker_versions_from_text(
    dockerfile: str,
    environment: str,
    node_environment: str,
    control_compose: str,
    node_compose: str,
) -> dict[str, str]:
    return {
        "dockerfile": _required_worker_version(
            dockerfile,
            r"^ARG ASSET_WORKER_VERSION=([^\s]+)$",
            "Dockerfile ASSET_WORKER_VERSION",
        ),
        "environment_version": _required_worker_version(
            environment,
            r"^ASSET_WORKER_VERSION=([^\s]+)$",
            ".env.example ASSET_WORKER_VERSION",
        ),
        "environment_tag": _required_worker_version(
            environment,
            r"^ASSET_WORKER_IMAGE_TAG=([^\s]+)$",
            ".env.example ASSET_WORKER_IMAGE_TAG",
        ),
        "node_environment_version": _required_worker_version(
            node_environment,
            r"^ASSET_WORKER_VERSION=([^\s]+)$",
            ".env.node.example ASSET_WORKER_VERSION",
        ),
        "node_environment_tag": _required_worker_version(
            node_environment,
            r"^ASSET_WORKER_IMAGE_TAG=([^\s]+)$",
            ".env.node.example ASSET_WORKER_IMAGE_TAG",
        ),
        "control_compose_version": _required_worker_version(
            control_compose,
            r"ASSET_WORKER_VERSION: \$\{ASSET_WORKER_VERSION:-([^}]+)\}",
            "control Compose ASSET_WORKER_VERSION",
        ),
        "control_compose_tag": _required_worker_version(
            control_compose,
            r"li3d/blender-worker:\$\{ASSET_WORKER_IMAGE_TAG:-([^}]+)\}",
            "control Compose ASSET_WORKER_IMAGE_TAG",
        ),
        "node_compose_version": _required_worker_version(
            node_compose,
            r"ASSET_WORKER_VERSION: \$\{ASSET_WORKER_VERSION:-([^}]+)\}",
            "node Compose ASSET_WORKER_VERSION",
        ),
        "node_compose_tag": _required_worker_version(
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
    return _worker_versions_from_text(values[0], values[1], values[2], values[3], values[4])


def committed_source_worker_versions(repository: Path, revision: str) -> dict[str, str]:
    paths = (
        "apps/blender_worker/Dockerfile",
        ".env.example",
        ".env.node.example",
        "deploy/control-plane/compose.yaml",
        "deploy/gpu-node/compose.yaml",
    )
    values = tuple(
        _run([GIT_EXECUTABLE, "-C", str(repository), "show", f"{revision}:{path}"])
        for path in paths
    )
    return _worker_versions_from_text(values[0], values[1], values[2], values[3], values[4])


def inspect_image(reference: str) -> dict[str, Any]:
    rows = json.loads(_run([DOCKER_EXECUTABLE, "image", "inspect", reference]))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError(f"unexpected docker inspect result for {reference}")
    return dict(rows[0])


def _reference_repository(reference: str) -> str:
    value = reference.split("@", 1)[0]
    last_slash = value.rfind("/")
    last_colon = value.rfind(":")
    return value[:last_colon] if last_colon > last_slash else value


def _bound_sbom(path: Path, manifest_digests: set[str]) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"SBOM must be a JSON object: {path}")
    predicate_type = str(payload.get("predicateType") or "").lower()
    if "spdx" not in predicate_type and "cyclonedx" not in predicate_type:
        raise ValueError(f"SBOM is not an in-toto SPDX/CycloneDX statement: {path}")
    subjects = payload.get("subject")
    if not isinstance(subjects, list):
        raise ValueError(f"SBOM does not contain an in-toto subject: {path}")
    bound_digests = {
        f"sha256:{digest}"
        for subject in subjects
        if isinstance(subject, dict)
        for digest in [dict(subject.get("digest") or {}).get("sha256")]
        if isinstance(digest, str)
    }
    if not (bound_digests & manifest_digests):
        raise ValueError(f"SBOM subject is not bound to the image manifest digest: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def verify(
    repository: Path,
    expected_version: str,
    expected_revision: str,
    images: dict[str, str],
    sboms: dict[str, Path],
    remote_ref: str = "origin/main",
    expected_worker_version: str = DEFAULT_ASSET_WORKER_VERSION,
) -> dict[str, Any]:
    if not VERSION_PATTERN.fullmatch(expected_version):
        raise ValueError("expected version must be a concrete semantic version")
    if not VERSION_PATTERN.fullmatch(expected_worker_version):
        raise ValueError("expected Worker version must be a concrete semantic version")
    if not REVISION_PATTERN.fullmatch(expected_revision):
        raise ValueError("expected revision must be a full lowercase 40-character Git SHA")
    head = _run([GIT_EXECUTABLE, "-C", str(repository), "rev-parse", "HEAD"]).strip()
    if head != expected_revision:
        raise ValueError(f"expected revision {expected_revision} is not current HEAD {head}")
    dirty = _run(
        [
            GIT_EXECUTABLE,
            "-C",
            str(repository),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ]
    ).strip()
    if dirty:
        raise ValueError("repository has uncommitted or untracked release inputs")
    remote_url = _run(
        [GIT_EXECUTABLE, "-C", str(repository), "remote", "get-url", "origin"]
    ).strip()
    accepted_remote_urls = {
        OCI_SOURCE,
        OCI_SOURCE.removesuffix(".git"),
        "git@github.com:kiana0512/gpu_control.git",
    }
    if remote_url not in accepted_remote_urls:
        raise ValueError(f"unexpected origin repository: {remote_url}")
    remote_revision = _run(
        [GIT_EXECUTABLE, "-C", str(repository), "rev-parse", "--verify", remote_ref]
    ).strip()
    ancestor = subprocess.run(  # noqa: S603 -- fixed git executable and argv, no shell
        [
            GIT_EXECUTABLE,
            "-C",
            str(repository),
            "merge-base",
            "--is-ancestor",
            expected_revision,
            remote_ref,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise ValueError(f"release revision is not present in {remote_ref} ({remote_revision})")
    versions = source_versions(repository)
    committed_versions = committed_source_versions(repository, expected_revision)
    if set(versions.values()) != {expected_version} or committed_versions != versions:
        raise ValueError(
            f"source version mismatch: working={versions!r}, committed={committed_versions!r}"
        )
    worker_versions = source_worker_versions(repository)
    committed_worker_versions = committed_source_worker_versions(repository, expected_revision)
    if (
        set(worker_versions.values()) != {expected_worker_version}
        or committed_worker_versions != worker_versions
    ):
        raise ValueError(
            "Worker source version mismatch: "
            f"working={worker_versions!r}, committed={committed_worker_versions!r}"
        )
    missing_images = REQUIRED_IMAGE_COMPONENTS - set(images)
    missing_sboms = REQUIRED_IMAGE_COMPONENTS - set(sboms)
    if missing_images or missing_sboms:
        raise ValueError(
            f"missing release evidence: images={sorted(missing_images)}, "
            f"sboms={sorted(missing_sboms)}"
        )
    evidence: dict[str, Any] = {
        "schema_version": "gpu-control-release-identity.v1",
        "expected_version": expected_version,
        "expected_worker_version": expected_worker_version,
        "expected_revision": expected_revision,
        "source_repository": OCI_SOURCE,
        "verified_remote_ref": remote_ref,
        "verified_remote_revision": remote_revision,
        "source_versions": versions,
        "worker_source_versions": worker_versions,
        "images": {},
    }
    image_ids: set[str] = set()
    for component in sorted(REQUIRED_IMAGE_COMPONENTS):
        reference = images[component]
        inspected = inspect_image(reference)
        config = dict(inspected.get("Config") or {})
        labels = dict(config.get("Labels") or {})
        actual_version = str(labels.get("org.opencontainers.image.version") or "")
        actual_revision = str(labels.get("org.opencontainers.image.revision") or "")
        component_version = (
            expected_worker_version if component == "blender-worker" else expected_version
        )
        if actual_version != component_version or actual_revision != expected_revision:
            raise ValueError(
                f"{reference} provenance mismatch: version={actual_version!r}, "
                f"revision={actual_revision!r}"
            )
        if labels.get("org.opencontainers.image.title") != OCI_TITLES[component]:
            raise ValueError(f"{reference} has the wrong OCI component title")
        if labels.get("org.opencontainers.image.source") != OCI_SOURCE:
            raise ValueError(f"{reference} has the wrong OCI source repository")
        environment = {
            key: value
            for item in config.get("Env") or []
            if "=" in item
            for key, value in [item.split("=", 1)]
        }
        if component != "web":
            runtime_version_name = (
                "ASSET_WORKER_BUILD_VERSION"
                if component == "blender-worker"
                else "GPU_CONTROL_BUILD_VERSION"
            )
            if environment.get(runtime_version_name) != component_version:
                raise ValueError(f"{reference} runtime build version is not aligned")
            if environment.get("GPU_CONTROL_BUILD_REVISION") != expected_revision:
                raise ValueError(f"{reference} runtime build revision is not aligned")
        image_id = str(inspected.get("Id") or "")
        repo_digests = [str(value) for value in (inspected.get("RepoDigests") or [])]
        if not image_id.startswith("sha256:"):
            raise ValueError(f"{reference} does not expose a Docker image ID")
        if not repo_digests or any("@sha256:" not in value for value in repo_digests):
            raise ValueError(f"{reference} is missing an immutable registry manifest digest")
        reference_repository = _reference_repository(reference)
        matching_repo_digests = [
            value for value in repo_digests if value.split("@", 1)[0] == reference_repository
        ]
        if not matching_repo_digests:
            raise ValueError(f"{reference} has no manifest digest for its requested repository")
        manifest_digests = {value.split("@", 1)[1] for value in matching_repo_digests}
        sbom_path = sboms[component]
        if not sbom_path.is_file() or sbom_path.stat().st_size < 1:
            raise ValueError(f"{component} SBOM is missing or empty: {sbom_path}")
        _, sbom_sha256 = _bound_sbom(sbom_path, manifest_digests)
        if image_id in image_ids:
            raise ValueError(f"multiple components resolve to the same image ID: {image_id}")
        image_ids.add(image_id)
        evidence["images"][component] = {
            "reference": reference,
            "docker_image_id": inspected.get("Id"),
            "registry_manifest_digests": matching_repo_digests,
            "oci_version": actual_version,
            "oci_revision": actual_revision,
            "runtime_version_environment": {
                key: environment.get(key)
                for key in (
                    "GPU_CONTROL_BUILD_VERSION",
                    "ASSET_WORKER_BUILD_VERSION",
                    "GPU_CONTROL_BUILD_REVISION",
                )
                if key in environment
            },
            "sbom_path": str(sbom_path),
            "sbom_sha256": sbom_sha256,
        }
    return evidence


def _named_values(values: list[str], option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        component, separator, item = value.partition("=")
        if not separator or not component or not item or component in parsed:
            raise ValueError(f"{option} must use one unique COMPONENT=VALUE per component")
        parsed[component] = item
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-worker-version", default=DEFAULT_ASSET_WORKER_VERSION)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--remote-ref", default="origin/main")
    parser.add_argument("--image", action="append", default=[], metavar="COMPONENT=REFERENCE")
    parser.add_argument("--sbom", action="append", default=[], metavar="COMPONENT=PATH")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        images = _named_values(list(args.image), "--image")
        sboms = {
            component: Path(path).resolve()
            for component, path in _named_values(list(args.sbom), "--sbom").items()
        }
        evidence = verify(
            args.repository.resolve(),
            args.expected_version,
            args.expected_revision,
            images,
            sboms,
            args.remote_ref,
            args.expected_worker_version,
        )
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"release identity verification failed: {exc}", file=sys.stderr)
        return 1
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.output)
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
