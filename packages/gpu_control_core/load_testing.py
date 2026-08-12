"""Safety and planning primitives for the six-API mixed load harness.

The Locust entrypoint imports this module to validate all execution gates before
it can create an HTTP client.  Production validation also uses fixed, shell-free
Git, Docker, and SSH argv to bind the approved remote receipt to the checkout
and the live deployment; plan-only and unit-test paths do not send load traffic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import yaml

API_NAMES = (
    "imageclip_batch",
    "modelview_roughness",
    "uv_process",
    "retopology_audit",
    "retopology_process",
    "substance_bake",
)
SYNC_FINAL_API_NAMES = frozenset({"modelview_roughness"})
# A production load run proves automatic delivery, not historical
# downloadability. WAITING_REVIEW remains terminal for legacy records but is
# deliberately not a successful acceptance state.
LOAD_SUCCESS_STATUSES = frozenset({"SUCCEEDED"})
LOAD_TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "WAITING_REVIEW", "REVIEW_REJECTED", "FAILED", "CANCELLED", "TIMED_OUT"}
)
LOAD_ACCEPTABLE_TEARDOWN_STATUSES = LOAD_SUCCESS_STATUSES | frozenset({"CANCELLED"})
LOAD_ACTIVE_STATUSES = frozenset(
    {
        "RECEIVED",
        "VALIDATING",
        "QUEUED",
        "CLAIMED",
        "UPLOADING",
        "SUBMITTED",
        "RUNNING",
        "DOWNLOADING",
        "ASSEMBLING",
        "CANCELLING",
        "RETRY_WAIT",
    }
)
REQUIRED_METRIC_THRESHOLD_NAMES = frozenset(
    {
        "http_failure_rate_percent",
        "submit_p95_ms",
        "poll_p95_ms",
        "artifact_p95_ms",
        "queue_p95_ms",
        "retry_rate_percent",
    }
)
OPTIONAL_METRIC_THRESHOLD_NAMES = frozenset({"sync_e2e_p95_ms"})
METRIC_THRESHOLD_NAMES = REQUIRED_METRIC_THRESHOLD_NAMES | OPTIONAL_METRIC_THRESHOLD_NAMES
LOAD_LIFECYCLE_MODES = frozenset({"all_complete", "bounded_stress"})

ASSET_JOB_TYPE_TO_API = {
    "UV_PROCESS_V2": "uv_process",
    "RETOPOLOGY_AUDIT": "retopology_audit",
    "RETOPOLOGY_PROCESS_V1": "retopology_process",
    "RETOPOLOGY_PROCESS_V2": "retopology_process",
    "SUBSTANCE_BAKE_V1": "substance_bake",
}

API_CONTRACTS: dict[str, dict[str, str]] = {
    "imageclip_batch": {
        "resource": "GPU",
        "submit": "/api/v1/batches/imageclip-rgba",
        "status": "/api/v1/batches/{batch_id}",
        "cancel": "/api/v1/batches/{batch_id}/cancel",
    },
    "modelview_roughness": {
        "resource": "GPU",
        "submit": "/api/v1/services/modelview-roughness",
        "status": "/api/v1/jobs/{job_id}",
        "cancel": "/api/v1/jobs/{job_id}/cancel",
    },
    "uv_process": {
        "resource": "CPU",
        "submit": "/api/v1/assets/uv/process",
        "status": "/api/v1/assets/jobs/{job_id}",
        "cancel": "/api/v1/assets/jobs/{job_id}/cancel",
    },
    "retopology_audit": {
        "resource": "CPU",
        "submit": "/api/v1/assets/retopology/audit",
        "status": "/api/v1/assets/jobs/{job_id}",
        "cancel": "/api/v1/assets/jobs/{job_id}/cancel",
    },
    "retopology_process": {
        "resource": "CPU",
        "submit": "/api/v1/assets/retopology/process",
        "status": "/api/v1/assets/jobs/{job_id}",
        "cancel": "/api/v1/assets/jobs/{job_id}/cancel",
    },
    "substance_bake": {
        "resource": "GPU_FENCED_ASSET",
        "submit": "/api/v1/assets/bake/process",
        "status": "/api/v1/assets/jobs/{job_id}",
        "cancel": "/api/v1/assets/jobs/{job_id}/cancel",
    },
}

# Fixed copies of the public, atomically-published artifact contracts. Keep
# these independent from the FastAPI application module so the network-free
# planner and the Locust entrypoint do not import application/runtime state.
FIXED_LOAD_ARTIFACT_KINDS: dict[str, frozenset[str]] = {
    "imageclip_batch": frozenset({"result_archive"}),
    "modelview_roughness": frozenset({"output"}),
    "uv_process": frozenset({"blend", "fbx", "report", "qa", "fbx_qa"}),
    "retopology_audit": frozenset({"audit", "manifest"}),
}
RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS = frozenset(
    {
        "blend",
        "fbx",
        "high_fbx",
        "alignment_report",
        "generation_report",
        "delivery_manifest",
        "result",
        "source_manifest",
        "agent_events",
        "wrapper_events",
    }
)
SUBSTANCE_LOAD_ARTIFACT_KINDS: dict[str, frozenset[str]] = {
    "ao-self-v1": frozenset({"ao", "result", "log"}),
    "normal-dx-v1": frozenset({"normal_dx", "result", "log"}),
    "pbr-core-v1": frozenset({"ao", "normal_dx", "result", "log"}),
    "li3d-pbr-full-v2": frozenset(
        {
            "base_color",
            "roughness",
            "metallic",
            "ao",
            "normal_dx",
            "normal_gl",
            "world_normal",
            "curvature",
            "thickness",
            "position",
            "result",
            "log",
        }
    ),
}

REQUIRED_FIXTURE_PATHS: dict[str, tuple[str, ...]] = {
    "imageclip_batch": ("archive", "manifest"),
    "modelview_roughness": ("image",),
    "uv_process": ("asset", "metadata"),
    "retopology_audit": ("project", "metadata"),
    "retopology_process": ("project", "metadata"),
    "substance_bake": ("low_mesh", "metadata"),
}

SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
LOAD_RELEASE_EVIDENCE_PATH_PATTERN = re.compile(
    r"^artifacts/control-plane/[0-9A-Za-z.+-]+/deployment/"
    r"live-deployment-receipt\.json$"
)
LOAD_CANDIDATE_EVIDENCE_PATH_PATTERN = re.compile(
    r"^artifacts/control-plane/[0-9A-Za-z.+-]+/release-parts/"
    r"release-candidate-evidence\.json$"
)
CHANGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
DEFAULT_PRODUCTION_HOSTS = frozenset({"10.3.34.11"})
NON_PRODUCTION_ENVIRONMENTS = frozenset({"test", "staging", "development"})
MINIMUM_PRODUCTION_LOAD_IDENTITIES = 12
PRODUCTION_TEARDOWN_RESERVE_SECONDS = 300
PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS = 540
RELEASE_IMAGE_COMPONENTS = (
    "api",
    "scheduler",
    "asset_api",
    "web",
    "worker",
)
LOAD_RELEASE_EVIDENCE_COMPONENTS = {
    "api": ("api", "gpu-control-api", "GPU Control API"),
    "scheduler": ("scheduler", "gpu-control-scheduler", "GPU Control Scheduler"),
    "asset_api": (
        "asset-api",
        "unified-scheduler-asset-api",
        "GPU Control Asset API",
    ),
    "web": ("web", "gpu-control-web", "GPU Control Web"),
    "worker": ("blender-worker", "li3d/blender-worker", "GPU Control Blender Worker"),
}
LOAD_RELEASE_ORIGIN_URLS = frozenset(
    {
        "https://github.com/kiana0512/gpu_control.git",
        "https://github.com/kiana0512/gpu_control",
        "git@github.com:kiana0512/gpu_control.git",
    }
)
LOAD_RELEASE_REMOTE_HEAD = "refs/heads/main"
LOAD_RELEASE_OCI_SOURCE = "https://github.com/kiana0512/gpu_control.git"
GIT_EXECUTABLE = "/usr/bin/git"
DOCKER_EXECUTABLE = "/usr/bin/docker"
SSH_EXECUTABLE = "/usr/bin/ssh"
MAX_LOAD_RELEASE_EVIDENCE_BYTES = 5 * 1024 * 1024
LOAD_DEPLOYMENT_HOSTS = {
    "control-api": "control-4090",
    "control-scheduler": "control-4090",
    "control-asset-api": "control-4090",
    "control-web": "control-4090",
    "control-worker": "control-4090",
    "worker-3090-a": "10.3.34.12",
    "worker-3090-b": "10.3.34.14:2222",
}
LOAD_LIVE_DEPLOYMENT_TARGETS: tuple[
    tuple[str, str, str, tuple[str, ...]], ...
] = (
    (
        "control-api",
        "api",
        "gpu-control-api-1",
        (DOCKER_EXECUTABLE, "inspect", "--type", "container", "--format={{.Image}}"),
    ),
    (
        "control-scheduler",
        "scheduler",
        "gpu-control-scheduler-1",
        (DOCKER_EXECUTABLE, "inspect", "--type", "container", "--format={{.Image}}"),
    ),
    (
        "control-asset-api",
        "asset_api",
        "gpu-control-asset-api-1",
        (DOCKER_EXECUTABLE, "inspect", "--type", "container", "--format={{.Image}}"),
    ),
    (
        "control-web",
        "web",
        "gpu-control-web-1",
        (DOCKER_EXECUTABLE, "inspect", "--type", "container", "--format={{.Image}}"),
    ),
    (
        "control-worker",
        "worker",
        "gpu-control-asset-worker-control-1",
        (DOCKER_EXECUTABLE, "inspect", "--type", "container", "--format={{.Image}}"),
    ),
    (
        "worker-3090-a",
        "worker",
        "gpu-control-node-blender-worker-1",
        (
            SSH_EXECUTABLE,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            "-p",
            "22",
            "--",
            "lilithgames@10.3.34.12",
            DOCKER_EXECUTABLE,
            "inspect",
            "--type",
            "container",
            "--format={{.Image}}",
        ),
    ),
    (
        "worker-3090-b",
        "worker",
        "gpu-control-node-blender-worker-1",
        (
            SSH_EXECUTABLE,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            "-p",
            "2222",
            "--",
            "gpucontrol@10.3.34.14",
            DOCKER_EXECUTABLE,
            "inspect",
            "--type",
            "container",
            "--format={{.Image}}",
        ),
    ),
)
FORBIDDEN_EVIDENCE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "x-api-key",
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "secret",
        "access_token",
        "refresh_token",
    }
)
TRANSIENT_LOAD_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
SUBSTANCE_LOAD_WORKER_MARKER = "3090-b-windows"
REQUIRED_FULL_BACKUP_PAYLOADS = frozenset(
    {
        "BACKUP_MANIFEST",
        "database.dump",
        "postgres-globals.sql",
        "repository-config.tar.gz",
        "repository.bundle",
        "git-status.txt",
        "git-worktree.patch",
        "git-index.patch",
        "git-head.txt",
        "git-remotes.txt",
        "git-lfs-files.txt",
        "docker-containers.txt",
        "docker-images.txt",
        "docker-volumes.txt",
        "repository-worktree-files.list",
        "repository-worktree.tar",
        "sensitive-config.tar.gz",
        "host-data.tar",
        "quiesce-gate-pre.txt",
        "quiesce-gate-post.txt",
    }
)
NONEMPTY_FULL_BACKUP_PAYLOADS = REQUIRED_FULL_BACKUP_PAYLOADS - {
    "git-status.txt",
    "git-worktree.patch",
    "git-index.patch",
    "git-lfs-files.txt",
}


class LoadTestConfigurationError(ValueError):
    """Raised when a plan, fixture manifest, or safety gate is invalid."""


class LoadTestPreempted(RuntimeError):
    """Raised when the production watchdog has fenced further load traffic."""

    def __init__(self, *, reason: str, operation: str) -> None:
        self.reason = reason
        self.operation = operation
        super().__init__(f"load operation {operation} preempted: {reason}")


def copy_load_evidence_json(value: Any) -> Any:
    """Deep-copy JSON evidence while refusing credential-shaped response fields."""

    def reject_credentials(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in FORBIDDEN_EVIDENCE_FIELD_NAMES:
                    raise LoadTestConfigurationError(
                        "load evidence response contains a forbidden credential field"
                    )
                if (
                    normalized_key == "url" or normalized_key.endswith("_url")
                ) and isinstance(nested, str):
                    parsed_url = urlsplit(nested)
                    if parsed_url.query or parsed_url.fragment:
                        raise LoadTestConfigurationError(
                            "load evidence response contains a query/fragment-bearing URL"
                        )
                reject_credentials(nested)
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes):
            for nested in item:
                reject_credentials(nested)

    reject_credentials(value)
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise LoadTestConfigurationError("load evidence response is not JSON-compatible") from exc


def capture_load_evidence_json(value: Any) -> tuple[Any | None, str | None]:
    """Capture response evidence without allowing validation errors to escape a VU."""

    try:
        return copy_load_evidence_json(value), None
    except LoadTestConfigurationError as exc:
        return None, str(exc)


def validate_load_service_provenance(
    control_api: Mapping[str, Any],
    asset_api: Mapping[str, Any],
    *,
    expected_revision: str,
) -> dict[str, dict[str, Any]]:
    """Verify the two HTTP-addressable services run the planned source revision."""

    if not COMMIT_PATTERN.fullmatch(expected_revision):
        raise LoadTestConfigurationError("planned source revision is not a full Git commit")
    verified: dict[str, dict[str, Any]] = {}
    for component, payload in (("api", control_api), ("asset-api", asset_api)):
        if payload.get("component") != component:
            raise LoadTestConfigurationError(
                f"{component} version endpoint returned the wrong component identity"
            )
        if payload.get("source_revision") != expected_revision:
            raise LoadTestConfigurationError(
                f"{component} live source revision does not match the load-test plan"
            )
        if payload.get("version_aligned") is not True or payload.get("provenance_complete") is not True:
            raise LoadTestConfigurationError(
                f"{component} live build provenance is incomplete or version-misaligned"
            )
        package_version = payload.get("package_version")
        build_version = payload.get("build_version")
        if not isinstance(package_version, str) or not package_version or build_version != package_version:
            raise LoadTestConfigurationError(
                f"{component} package/build version evidence is invalid"
            )
        verified[component] = {
            "component": component,
            "package_version": package_version,
            "build_version": build_version,
            "source_revision": expected_revision,
            "version_aligned": True,
            "provenance_complete": True,
        }
    return verified


def _load_evidence_git(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed Git argv for externally anchored release evidence."""

    try:
        completed = subprocess.run(  # noqa: S603 -- executable and operations are fixed argv
            [GIT_EXECUTABLE, "-C", str(repository_root.resolve()), *arguments],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LoadTestConfigurationError(
            "release evidence Git verification could not complete"
        ) from exc
    if check and completed.returncode != 0:
        operation = " ".join(arguments[:2])
        raise LoadTestConfigurationError(
            f"release evidence Git verification failed ({operation}, exit "
            f"{completed.returncode})"
        )
    return completed


def _required_release_evidence_string(
    source: Mapping[str, Any],
    field: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise LoadTestConfigurationError(f"release evidence field {field} is missing")
    if pattern is not None and not pattern.fullmatch(value):
        raise LoadTestConfigurationError(f"release evidence field {field} is invalid")
    return value


def verify_remote_load_release_evidence(
    repository_root: Path,
    runtime: RuntimeSettings,
) -> dict[str, Any]:
    """Verify the production release identity from an origin/main-anchored JSON blob.

    The environment is only an equality assertion.  Authority comes from the
    exact evidence blob stored at the current remote ``main`` commit.  The
    release/source commit may precede the evidence-only commit, but it must be
    its ancestor.
    """

    evidence_commit = runtime.release_evidence_commit
    evidence_path = runtime.release_evidence_path
    evidence_sha256 = runtime.release_evidence_sha256
    if not COMMIT_PATTERN.fullmatch(evidence_commit):
        raise LoadTestConfigurationError(
            "LOAD_TEST_RELEASE_EVIDENCE_COMMIT must be a full lowercase Git commit"
        )
    if not LOAD_RELEASE_EVIDENCE_PATH_PATTERN.fullmatch(evidence_path):
        raise LoadTestConfigurationError(
            "LOAD_TEST_RELEASE_EVIDENCE_PATH must name a packaged live deployment receipt"
        )
    if not HASH_PATTERN.fullmatch(evidence_sha256):
        raise LoadTestConfigurationError(
            "LOAD_TEST_RELEASE_EVIDENCE_SHA256 must be a lowercase SHA-256"
        )

    try:
        origin_url = _load_evidence_git(
            repository_root,
            "remote",
            "get-url",
            "origin",
        ).stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise LoadTestConfigurationError("release evidence origin is not valid UTF-8") from exc
    if origin_url not in LOAD_RELEASE_ORIGIN_URLS:
        raise LoadTestConfigurationError(
            f"release evidence origin is not the approved GitHub repository: {origin_url}"
        )
    try:
        remote_output = _load_evidence_git(
            repository_root,
            "ls-remote",
            "--exit-code",
            "--refs",
            "origin",
            LOAD_RELEASE_REMOTE_HEAD,
        ).stdout.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise LoadTestConfigurationError(
            "release evidence origin/main response is not ASCII"
        ) from exc
    remote_rows = [line.split() for line in remote_output.splitlines() if line.strip()]
    remote_commits = [
        row[0]
        for row in remote_rows
        if len(row) == 2 and row[1] == LOAD_RELEASE_REMOTE_HEAD
    ]
    if remote_commits != [evidence_commit]:
        raise LoadTestConfigurationError(
            "LOAD_TEST_RELEASE_EVIDENCE_COMMIT is not the current origin/main commit"
        )

    try:
        checkout_head = _load_evidence_git(
            repository_root,
            "rev-parse",
            "--verify",
            "HEAD",
        ).stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise LoadTestConfigurationError("local harness HEAD is not ASCII") from exc
    if checkout_head != evidence_commit:
        raise LoadTestConfigurationError(
            "production load harness HEAD must equal the origin/main evidence commit"
        )
    tracked_status = _load_evidence_git(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    ).stdout
    if tracked_status.strip():
        raise LoadTestConfigurationError(
            "production load harness has modified tracked files"
        )

    evidence_blob = _load_evidence_git(
        repository_root,
        "show",
        "--no-ext-diff",
        "--no-textconv",
        f"{evidence_commit}:{evidence_path}",
    ).stdout
    if not evidence_blob or len(evidence_blob) > MAX_LOAD_RELEASE_EVIDENCE_BYTES:
        raise LoadTestConfigurationError("remote release evidence has an invalid size")
    actual_evidence_sha256 = hashlib.sha256(evidence_blob).hexdigest()
    if actual_evidence_sha256 != evidence_sha256:
        raise LoadTestConfigurationError(
            "remote release evidence SHA-256 does not match the approved anchor"
        )
    try:
        payload = json.loads(evidence_blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadTestConfigurationError("remote release evidence is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LoadTestConfigurationError("remote release evidence must be a JSON object")
    if (
        payload.get("schema_version") != "gpu-control-live-deployment.v1"
        or payload.get("deployment_status") != "DEPLOYED_NOT_ACCEPTED"
        or payload.get("deployed") is not True
        or payload.get("production_accepted") is not False
    ):
        raise LoadTestConfigurationError(
            "remote live deployment receipt status or schema is not approved"
        )

    source_revision = _required_release_evidence_string(
        payload,
        "source_revision",
        pattern=COMMIT_PATTERN,
    )
    if source_revision != runtime.source_revision:
        raise LoadTestConfigurationError(
            "environment source revision does not match remote release evidence"
        )
    ancestry = _load_evidence_git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        source_revision,
        evidence_commit,
        check=False,
    )
    if ancestry.returncode != 0:
        raise LoadTestConfigurationError(
            "release source revision is not an ancestor of the evidence commit"
        )

    receipt_source = payload.get("source")
    if not isinstance(receipt_source, Mapping) or receipt_source != {
        "repository": LOAD_RELEASE_OCI_SOURCE,
        "revision": source_revision,
    }:
        raise LoadTestConfigurationError("live deployment receipt source identity is invalid")
    candidate_anchor = payload.get("candidate_evidence")
    if not isinstance(candidate_anchor, Mapping):
        raise LoadTestConfigurationError(
            "live deployment receipt omitted candidate evidence anchor"
        )
    candidate_path = _required_release_evidence_string(candidate_anchor, "path")
    candidate_sha256 = _required_release_evidence_string(
        candidate_anchor,
        "sha256",
        pattern=HASH_PATTERN,
    )
    if not LOAD_CANDIDATE_EVIDENCE_PATH_PATTERN.fullmatch(candidate_path):
        raise LoadTestConfigurationError(
            "live deployment receipt candidate evidence path is invalid"
        )
    candidate_blob = _load_evidence_git(
        repository_root,
        "show",
        "--no-ext-diff",
        "--no-textconv",
        f"{evidence_commit}:{candidate_path}",
    ).stdout
    if (
        not candidate_blob
        or len(candidate_blob) > MAX_LOAD_RELEASE_EVIDENCE_BYTES
        or hashlib.sha256(candidate_blob).hexdigest() != candidate_sha256
    ):
        raise LoadTestConfigurationError(
            "live deployment receipt candidate evidence blob is invalid"
        )
    try:
        candidate_payload = json.loads(candidate_blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadTestConfigurationError("candidate release evidence is not valid JSON") from exc
    if (
        not isinstance(candidate_payload, dict)
        or candidate_payload.get("schema_version") != "gpu-control-release-candidate.v2"
        or candidate_payload.get("release_status") != "CANDIDATE_ARCHIVE_ONLY"
        or candidate_payload.get("deployed") is not False
        or candidate_payload.get("production_accepted") is not False
        or candidate_payload.get("revision") != source_revision
    ):
        raise LoadTestConfigurationError(
            "live deployment receipt candidate evidence identity is invalid"
        )

    substance_script_blob = _load_evidence_git(
        repository_root,
        "show",
        "--no-ext-diff",
        "--no-textconv",
        f"{evidence_commit}:apps/substance_baker_agent/Invoke-GPUControlSubstanceAgent.ps1",
    ).stdout
    substance_script_sha256 = hashlib.sha256(substance_script_blob).hexdigest()
    expected_substance = runtime.target_release_identity["substance_agent"]
    expected_receipt_substance = {
        **expected_substance,
        "repository_script_sha256": substance_script_sha256,
    }
    receipt_substance = payload.get("substance_agent")
    if (
        not substance_script_blob
        or not isinstance(receipt_substance, Mapping)
        or dict(receipt_substance) != expected_receipt_substance
    ):
        raise LoadTestConfigurationError(
            "live deployment receipt Windows Substance Agent identity is invalid"
        )

    receipt_payload = payload
    payload = candidate_payload

    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise LoadTestConfigurationError("remote release evidence omitted source identity")
    if (
        source.get("repository") != LOAD_RELEASE_OCI_SOURCE
        or source.get("remote_ref") != "origin/main"
        or source.get("remote_sha") != source_revision
    ):
        raise LoadTestConfigurationError("remote release evidence source identity is invalid")
    version = _required_release_evidence_string(payload, "version")
    worker_version = _required_release_evidence_string(payload, "worker_version")
    images = payload.get("images")
    offline_exports = payload.get("offline_oci_exports")
    expected_image_keys = {
        evidence_key for evidence_key, _, _ in LOAD_RELEASE_EVIDENCE_COMPONENTS.values()
    }
    if not isinstance(images, Mapping) or set(images) != expected_image_keys:
        raise LoadTestConfigurationError(
            "remote release evidence must contain exactly five first-party images"
        )
    if not isinstance(offline_exports, Mapping) or set(offline_exports) != expected_image_keys:
        raise LoadTestConfigurationError(
            "remote release evidence must contain exactly five offline OCI identities"
        )
    attestations = payload.get("attestations")
    if not isinstance(attestations, Mapping) or attestations.get(
        "provenance_status"
    ) != "VERIFIED_OFFLINE_OCI":
        raise LoadTestConfigurationError("remote release evidence has no verified OCI provenance")

    declared_digests = runtime.target_release_identity["image_digests"]
    verified_images: dict[str, dict[str, str]] = {}
    for runtime_key, (
        evidence_key,
        repository,
        title,
    ) in LOAD_RELEASE_EVIDENCE_COMPONENTS.items():
        image = images[evidence_key]
        offline = offline_exports[evidence_key]
        if not isinstance(image, Mapping) or not isinstance(offline, Mapping):
            raise LoadTestConfigurationError(
                f"remote release evidence image {evidence_key} has an invalid shape"
            )
        component_version = worker_version if runtime_key == "worker" else version
        expected_reference = f"{repository}:{component_version}"
        local_image_id = _required_release_evidence_string(
            image,
            "local_image_id",
            pattern=IMAGE_DIGEST_PATTERN,
        )
        manifest_digest = _required_release_evidence_string(
            image,
            "oci_image_manifest_digest",
            pattern=IMAGE_DIGEST_PATTERN,
        )
        config_digest = _required_release_evidence_string(
            image,
            "oci_config_digest",
            pattern=IMAGE_DIGEST_PATTERN,
        )
        docker_config_digest = _required_release_evidence_string(
            image,
            "docker_archive_config_digest",
            pattern=IMAGE_DIGEST_PATTERN,
        )
        if (
            image.get("reference") != expected_reference
            or image.get("local_image_id_semantics")
            != "ENGINE_LOCAL_CONTENT_ID_NOT_ASSUMED_CONFIG_DIGEST"
            or config_digest != docker_config_digest
            or image.get("docker_oci_config_match") is not True
            or offline.get("oci_image_manifest_digest") != manifest_digest
            or offline.get("oci_config_digest") != config_digest
            or offline.get("docker_archive_config_digest") != config_digest
            or offline.get("docker_oci_config_match") is not True
        ):
            raise LoadTestConfigurationError(
                f"remote release evidence OCI identity for {evidence_key} is inconsistent"
            )
        labels = image.get("oci_labels")
        if not isinstance(labels, Mapping) or labels != {
            "org.opencontainers.image.title": title,
            "org.opencontainers.image.version": component_version,
            "org.opencontainers.image.revision": source_revision,
            "org.opencontainers.image.source": LOAD_RELEASE_OCI_SOURCE,
        }:
            raise LoadTestConfigurationError(
                f"remote release evidence OCI labels for {evidence_key} are invalid"
            )
        declared_digest = declared_digests.get(runtime_key)
        if declared_digest != local_image_id:
            raise LoadTestConfigurationError(
                f"environment image digest for {runtime_key} does not match remote evidence"
            )
        verified_images[runtime_key] = {
            "evidence_component": evidence_key,
            "reference": expected_reference,
            "identity_type": "docker_local_image_id+offline_oci_manifest_and_config",
            "local_image_id": local_image_id,
            "oci_image_manifest_digest": manifest_digest,
            "oci_config_digest": config_digest,
        }

    expected_deployment_inventory = runtime.target_release_identity[
        "deployment_inventory"
    ]
    if receipt_payload.get("components") != verified_images:
        raise LoadTestConfigurationError(
            "live deployment receipt components do not match candidate OCI evidence"
        )
    if receipt_payload.get("inventory") != expected_deployment_inventory:
        raise LoadTestConfigurationError(
            "live deployment receipt inventory does not match the declared live topology"
        )

    return {
        "schema_version": "gpu-control-load-release-evidence-verification.v1",
        "verified": True,
        "origin_url": origin_url,
        "remote_ref": LOAD_RELEASE_REMOTE_HEAD,
        "evidence_commit": evidence_commit,
        "evidence_path": evidence_path,
        "evidence_sha256": actual_evidence_sha256,
        "source_revision": source_revision,
        "candidate_evidence": {
            "path": candidate_path,
            "sha256": candidate_sha256,
        },
        "images": verified_images,
        "deployment_inventory": expected_deployment_inventory,
        "substance_agent": expected_receipt_substance,
    }


def verify_live_load_deployment(
    runtime: RuntimeSettings,
    release_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind every live first-party container to the remote release evidence."""

    evidence_images = release_evidence.get("images")
    if (
        release_evidence.get("verified") is not True
        or not isinstance(evidence_images, Mapping)
        or release_evidence.get("deployment_inventory")
        != runtime.target_release_identity["deployment_inventory"]
    ):
        raise LoadTestConfigurationError(
            "live deployment verification requires verified remote release evidence"
        )
    declared_digests = runtime.target_release_identity["image_digests"]
    inventory: dict[str, dict[str, str]] = {}
    for target, component, container_name, command_prefix in LOAD_LIVE_DEPLOYMENT_TARGETS:
        evidence_image = evidence_images.get(component)
        if not isinstance(evidence_image, Mapping):
            raise LoadTestConfigurationError(
                f"remote release evidence omitted live component {component}"
            )
        expected_image_id = evidence_image.get("local_image_id")
        if (
            not isinstance(expected_image_id, str)
            or not IMAGE_DIGEST_PATTERN.fullmatch(expected_image_id)
            or expected_image_id != declared_digests.get(component)
        ):
            raise LoadTestConfigurationError(
                f"remote release evidence does not authorize live component {component}"
            )
        command = [*command_prefix, container_name]
        try:
            completed = subprocess.run(  # noqa: S603 -- fully fixed executable and argv
                command,
                check=False,
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LoadTestConfigurationError(
                f"live deployment inspection could not complete for {target}"
            ) from exc
        if completed.returncode != 0:
            raise LoadTestConfigurationError(
                f"live deployment inspection failed for {target} "
                f"(exit {completed.returncode})"
            )
        try:
            output_lines = completed.stdout.decode("ascii", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise LoadTestConfigurationError(
                f"live deployment inspection returned invalid output for {target}"
            ) from exc
        image_ids = [line.strip() for line in output_lines if line.strip()]
        if image_ids != [expected_image_id]:
            raise LoadTestConfigurationError(
                f"live deployment image for {target} does not match remote release evidence"
            )
        inventory[target] = {
            "component": component,
            "host": LOAD_DEPLOYMENT_HOSTS[target],
            "container_name": container_name,
            "image_identity_type": "docker_container_config_image_id",
            "image_id": expected_image_id,
        }
    worker_image_ids = {
        item["image_id"]
        for item in inventory.values()
        if item["component"] == "worker"
    }
    if worker_image_ids != {runtime.worker_image_digest}:
        raise LoadTestConfigurationError(
            "all three live Blender Workers must use one release image ID"
        )
    substance_command = [
        SSH_EXECUTABLE,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        "-p",
        "2222",
        "--",
        "gpucontrol@10.3.34.14",
        "/usr/bin/sha256sum",
        "/mnt/d/GPUControl/agent/Invoke-GPUControlSubstanceAgent.ps1",
    ]
    try:
        substance_completed = subprocess.run(  # noqa: S603 -- fully fixed argv
            substance_command,
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LoadTestConfigurationError(
            "live Windows Substance Agent script inspection could not complete"
        ) from exc
    try:
        substance_output = substance_completed.stdout.decode(
            "ascii", errors="strict"
        ).split()
    except UnicodeDecodeError as exc:
        raise LoadTestConfigurationError(
            "live Windows Substance Agent script inspection returned invalid output"
        ) from exc
    if (
        substance_completed.returncode != 0
        or len(substance_output) != 2
        or substance_output[0] != runtime.substance_agent_sha256
        or substance_output[1]
        != "/mnt/d/GPUControl/agent/Invoke-GPUControlSubstanceAgent.ps1"
    ):
        raise LoadTestConfigurationError(
            "live Windows Substance Agent script does not match the remote receipt"
        )
    return {
        "schema_version": "gpu-control-load-live-deployment-verification.v1",
        "verified": True,
        "release_evidence_commit": release_evidence.get("evidence_commit"),
        "source_revision": runtime.source_revision,
        "inventory": inventory,
        "substance_agent": runtime.target_release_identity["substance_agent"],
    }


def expected_load_artifact_kinds(
    api_name: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return the exact final artifact kinds for one submitted load fixture."""

    fixed = FIXED_LOAD_ARTIFACT_KINDS.get(api_name)
    if fixed is not None:
        return fixed
    payload = metadata if isinstance(metadata, Mapping) else {}
    if api_name == "retopology_process":
        return RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS
    if api_name == "substance_bake":
        options = payload.get("options")
        profile = str(options.get("profile") or "") if isinstance(options, Mapping) else ""
        expected = SUBSTANCE_LOAD_ARTIFACT_KINDS.get(profile)
        if expected is None:
            raise LoadTestConfigurationError(
                f"unsupported Substance load artifact profile: {profile or '<missing>'}"
            )
        return expected
    raise LoadTestConfigurationError(f"unknown load API artifact contract: {api_name}")


def validate_load_artifact_manifest(
    api_name: str,
    artifacts: object,
    *,
    expected_kinds: Sequence[str] | frozenset[str],
) -> list[dict[str, Any]]:
    """Validate exact cardinality/kinds and immutable download metadata."""

    expected = frozenset(str(kind) for kind in expected_kinds)
    if not expected:
        raise LoadTestConfigurationError(f"{api_name} artifact contract is empty")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, str | bytes):
        raise LoadTestConfigurationError(f"{api_name} artifacts must be a list")
    normalized: list[dict[str, Any]] = []
    observed: list[str] = []
    for index, raw in enumerate(artifacts):
        if not isinstance(raw, Mapping):
            raise LoadTestConfigurationError(f"{api_name} artifact index {index} is not an object")
        artifact = dict(raw)
        kind = str(artifact.get("kind") or "")
        identifier = str(artifact.get("id") or "")
        filename = str(artifact.get("filename") or "")
        download_url = str(artifact.get("download_url") or "")
        size_bytes = artifact.get("size_bytes")
        sha256 = str(artifact.get("sha256") or "")
        if not kind or not identifier or (api_name != "modelview_roughness" and not filename):
            raise LoadTestConfigurationError(
                f"{api_name} artifact index {index} omitted identity metadata"
            )
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
            raise LoadTestConfigurationError(f"{api_name} artifact {kind} has a non-positive size")
        if not HASH_PATTERN.fullmatch(sha256):
            raise LoadTestConfigurationError(f"{api_name} artifact {kind} has an invalid SHA-256")
        parsed_download_url = urlsplit(download_url)
        if (
            not download_url.startswith("/")
            or download_url.startswith("//")
            or parsed_download_url.scheme
            or parsed_download_url.netloc
            or parsed_download_url.query
            or parsed_download_url.fragment
        ):
            raise LoadTestConfigurationError(
                f"{api_name} artifact {kind} has a non-local download URL"
            )
        observed.append(kind)
        normalized.append(artifact)
    if len(set(observed)) != len(observed):
        raise LoadTestConfigurationError(f"{api_name} artifact kinds are not unique")
    if frozenset(observed) != expected or len(observed) != len(expected):
        raise LoadTestConfigurationError(
            f"{api_name} artifact kinds/cardinality drifted: "
            f"expected={sorted(expected)} observed={sorted(observed)}"
        )
    return normalized


def validate_downloaded_load_artifact(
    api_name: str,
    artifact: Mapping[str, Any],
    content: bytes,
    *,
    header_sha256: str | None,
) -> str:
    """Verify metadata size/SHA, response SHA header, and downloaded bytes."""

    kind = str(artifact.get("kind") or "<unknown>")
    size_bytes = artifact.get("size_bytes")
    expected_sha256 = str(artifact.get("sha256") or "")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise LoadTestConfigurationError(
            f"{api_name} artifact {kind} has a non-positive metadata size"
        )
    if not HASH_PATTERN.fullmatch(expected_sha256):
        raise LoadTestConfigurationError(
            f"{api_name} artifact {kind} has an invalid metadata SHA-256"
        )
    if not content or len(content) != size_bytes:
        raise LoadTestConfigurationError(
            f"{api_name} artifact {kind} download size does not match metadata"
        )
    response_sha256 = str(header_sha256 or "")
    if response_sha256 != expected_sha256:
        raise LoadTestConfigurationError(
            f"{api_name} artifact {kind} response SHA-256 header does not match metadata"
        )
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise LoadTestConfigurationError(
            f"{api_name} artifact {kind} body SHA-256 does not match metadata"
        )
    return actual_sha256


def build_load_artifact_evidence(
    api_name: str,
    artifact: Mapping[str, Any],
    *,
    header_sha256: str,
    body_sha256: str,
    body_size_bytes: int,
) -> dict[str, Any]:
    """Build one secret-free artifact proof after three-way SHA validation."""

    kind = str(artifact.get("kind") or "")
    identifier = str(artifact.get("id") or "")
    filename = str(artifact.get("filename") or "")
    metadata_size_bytes = artifact.get("size_bytes")
    metadata_sha256 = str(artifact.get("sha256") or "")
    if not kind or not identifier or (api_name != "modelview_roughness" and not filename):
        raise LoadTestConfigurationError(f"{api_name} artifact evidence omitted identity metadata")
    if (
        isinstance(metadata_size_bytes, bool)
        or not isinstance(metadata_size_bytes, int)
        or metadata_size_bytes <= 0
        or isinstance(body_size_bytes, bool)
        or not isinstance(body_size_bytes, int)
        or body_size_bytes <= 0
        or metadata_size_bytes != body_size_bytes
    ):
        raise LoadTestConfigurationError(f"{api_name} artifact evidence size values do not match")
    if (
        not all(
            HASH_PATTERN.fullmatch(value) for value in (metadata_sha256, header_sha256, body_sha256)
        )
        or len({metadata_sha256, header_sha256, body_sha256}) != 1
    ):
        raise LoadTestConfigurationError(
            f"{api_name} artifact evidence SHA-256 values do not match"
        )
    return {
        "kind": kind,
        "id": identifier,
        "filename": filename,
        "metadata_size_bytes": metadata_size_bytes,
        "metadata_sha256": metadata_sha256,
        "x_artifact_sha256": header_sha256,
        "body_size_bytes": body_size_bytes,
        "body_sha256": body_sha256,
    }


def find_load_session_identity_collisions(
    gpu_jobs: Sequence[Mapping[str, Any]],
    asset_jobs: Sequence[Mapping[str, Any]],
    *,
    tenant_ids: Sequence[str],
    session_id: str,
) -> list[dict[str, str]]:
    """Find historical rows that reuse this run's exact identity namespace."""

    if not SESSION_PATTERN.fullmatch(session_id):
        raise LoadTestConfigurationError("session collision scan requires a valid session id")
    tenants = {str(value).strip() for value in tenant_ids if str(value).strip()}
    if not tenants or len(tenants) != len(tenant_ids):
        raise LoadTestConfigurationError(
            "session collision scan requires unique non-empty tenant ids"
        )
    escaped_session = re.escape(session_id)
    gpu_batch_pattern = re.compile(rf"^loadtest:{escaped_session}:imageclip_batch:[0-9]{{8}}$")
    roughness_pattern = re.compile(rf"^lt:{escaped_session}:mvr:[0-9]{{8}}$")
    asset_pattern = re.compile(
        rf"^loadtest:{escaped_session}:"
        r"(?:uv_process|retopology_audit|retopology_process|substance_bake):[0-9]{8}$"
    )
    collisions: list[dict[str, str]] = []
    for plane, rows in (("gpu", gpu_jobs), ("asset", asset_jobs)):
        for row in rows:
            if not isinstance(row, Mapping):
                raise LoadTestConfigurationError(
                    f"{plane} session collision scan received a non-object row"
                )
            owner_field = "tenant_id" if plane == "gpu" else "client_id"
            if str(row.get(owner_field) or "") not in tenants:
                continue
            identity = ""
            if plane == "asset":
                identity = str(row.get("external_asset_id") or "")
                matched = asset_pattern.fullmatch(identity) is not None
            elif row.get("kind") == "batch":
                identity = str(row.get("external_batch_id") or "")
                matched = gpu_batch_pattern.fullmatch(identity) is not None
            else:
                identity = str(row.get("request_id") or "")
                matched = roughness_pattern.fullmatch(identity) is not None
            if not matched:
                continue
            identifier = str(row.get("job_id") or row.get("batch_id") or "")
            if not identifier:
                raise LoadTestConfigurationError(
                    f"{plane} session collision row omitted its task id"
                )
            collisions.append(
                {
                    "plane": plane,
                    "task_id": identifier,
                    "owner_id": str(row.get(owner_field)),
                    "identity": identity,
                    "status": str(row.get("status") or "UNKNOWN"),
                }
            )
    return collisions


class LoadShapeStopSignal:
    """Thread-safe, idempotent fence for the Locust Shape and virtual users."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reason: str | None = None

    def request(self, reason: str) -> bool:
        normalized = reason.strip()
        if not normalized:
            raise LoadTestConfigurationError("load Shape stop reason cannot be empty")
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = normalized
            return True

    def reset(self) -> None:
        with self._lock:
            self._reason = None

    def raise_if_requested(self, operation: str) -> None:
        """Prevent a virtual user from starting another request after a safety stop."""

        normalized = operation.strip()
        if not normalized:
            raise LoadTestConfigurationError("load preemption operation cannot be empty")
        with self._lock:
            reason = self._reason
        if reason is not None:
            raise LoadTestPreempted(reason=reason, operation=normalized)

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._reason is not None

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason


def execute_bounded_teardown_cancel(
    send_status: Callable[[], int],
    sleep: Callable[[float], None],
    *,
    max_attempts: int = 3,
    initial_backoff_seconds: float = 0.25,
    maximum_backoff_seconds: float = 1.0,
    deadline_monotonic: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, int]:
    """Cancel once, retrying only 429/5xx responses with bounded backoff."""

    if max_attempts < 1:
        raise LoadTestConfigurationError("teardown cancel max_attempts must be positive")
    if initial_backoff_seconds < 0 or maximum_backoff_seconds < 0:
        raise LoadTestConfigurationError("teardown cancel backoff cannot be negative")
    for attempt in range(1, max_attempts + 1):
        if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
            raise TimeoutError("teardown deadline expired before cancel attempt")
        status_code = send_status()
        retryable = status_code == 429 or 500 <= status_code <= 599
        if not retryable or attempt == max_attempts:
            return status_code, attempt
        delay = min(
            maximum_backoff_seconds,
            initial_backoff_seconds * (2 ** (attempt - 1)),
        )
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - monotonic()
            if remaining <= 0:
                raise TimeoutError("teardown deadline expired before cancel retry")
            delay = min(delay, remaining)
        sleep(delay)
    raise AssertionError("bounded teardown cancel exhausted without returning")


def approved_load_tls_verify(ca_file: Path | None) -> bool | str:
    """Return requests/httpx TLS verification using only the approved CA."""

    if ca_file is None:
        return True
    try:
        if not ca_file.is_file():
            raise LoadTestConfigurationError("approved load-test CA is not a file")
        with ca_file.open("rb"):
            pass
    except OSError as exc:
        raise LoadTestConfigurationError("approved load-test CA is not readable") from exc
    return str(ca_file)


def configure_locust_client_tls(client: Any, ca_file: Path | None) -> bool | str:
    """Bind a Locust HttpSession to the approved CA before its first request."""

    if not hasattr(client, "verify") or not hasattr(client, "trust_env"):
        raise LoadTestConfigurationError("Locust client does not expose TLS controls")
    verify = approved_load_tls_verify(ca_file)
    client.verify = verify
    # requests can otherwise let REQUESTS_CA_BUNDLE replace Session.verify.
    # Disable environment merging so every VU stays bound to the reviewed CA.
    client.trust_env = False
    if getattr(client, "verify", None) != verify or getattr(client, "trust_env", True):
        raise LoadTestConfigurationError("Locust client rejected approved TLS controls")
    return verify


def normalize_scheduler_capacity_v1(payload: object) -> dict[str, Any]:
    """Canonicalize only the two documented scheduler-capacity v1 aliases.

    Older v1.0 deployments expose ``accepting`` and
    ``cluster.queued_jobs``; newer deployments also expose
    ``accepting_batches`` and top-level ``queue_depth``. Both spellings are
    populated in the result so preflight and telemetry share one contract.
    Unknown shapes and conflicting aliases fail closed.
    """

    if not isinstance(payload, Mapping):
        raise LoadTestConfigurationError("scheduler capacity must be an object")
    if payload.get("schema_version") != "1.0":
        raise LoadTestConfigurationError("scheduler capacity schema_version must be 1.0")
    raw_cluster = payload.get("cluster")
    if not isinstance(raw_cluster, Mapping):
        raise LoadTestConfigurationError("scheduler capacity cluster must be an object")

    accepting_values = [
        payload[key] for key in ("accepting_batches", "accepting") if key in payload
    ]
    if not accepting_values or any(not isinstance(value, bool) for value in accepting_values):
        raise LoadTestConfigurationError("scheduler capacity accepting flag must be boolean")
    if len(set(accepting_values)) != 1:
        raise LoadTestConfigurationError("scheduler capacity accepting aliases conflict")

    queue_values = [
        value
        for value in (payload.get("queue_depth"), raw_cluster.get("queued_jobs"))
        if value is not None
    ]
    if not queue_values or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in queue_values
    ):
        raise LoadTestConfigurationError(
            "scheduler capacity queue depth must be a non-negative integer"
        )
    if len(set(queue_values)) != 1:
        raise LoadTestConfigurationError("scheduler capacity queue aliases conflict")

    accepting = accepting_values[0]
    queue_depth = queue_values[0]
    normalized = dict(payload)
    normalized_cluster = dict(raw_cluster)
    normalized["accepting"] = accepting
    normalized["accepting_batches"] = accepting
    normalized["queue_depth"] = queue_depth
    normalized_cluster["queued_jobs"] = queue_depth
    normalized["cluster"] = normalized_cluster
    return normalized


def load_response_is_retryable(
    status_code: int | None, *, has_transport_error: bool = False
) -> bool:
    """Return whether a load-harness HTTP response is safe to retry.

    Locust represents connection failures and request timeouts as synthetic
    responses with status ``0``.  Treating those responses as ordinary
    ``<400`` successes would both suppress the failure and strand an
    idempotently-created server task, so they are always retryable.
    """

    return (
        has_transport_error
        or status_code is None
        or status_code <= 0
        or status_code in TRANSIENT_LOAD_HTTP_STATUSES
    )


def load_queue_start(payload: Mapping[str, Any]) -> object:
    """Use the authoritative queued timestamp, with legacy create fallback."""

    return payload.get("queued_at") or payload.get("created_at")


def validate_test_client_capacities(
    capacities: Sequence[Mapping[str, Any]],
    *,
    expected_tenant_ids: Sequence[str],
    asset_capacities: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate ordered load identities on both API planes without retaining secrets."""

    expected_tenants = tuple(str(value).strip() for value in expected_tenant_ids)
    if (
        not expected_tenants
        or any(not value for value in expected_tenants)
        or len(set(expected_tenants)) != len(expected_tenants)
        or len(capacities) != len(expected_tenants)
    ):
        raise LoadTestConfigurationError(
            "every LOAD_TEST_API_KEYS identity must have one ordered tenant capacity preflight"
        )
    if asset_capacities is not None and len(asset_capacities) != len(expected_tenants):
        raise LoadTestConfigurationError(
            "every LOAD_TEST_API_KEYS identity must have one Asset capacity preflight"
        )
    checks: list[dict[str, Any]] = []
    for index, (capacity, expected_tenant_id) in enumerate(
        zip(capacities, expected_tenants, strict=True)
    ):
        client = capacity.get("client")
        if (
            not isinstance(client, Mapping)
            or client.get("kind") != "test"
            or client.get("id") != expected_tenant_id
        ):
            raise LoadTestConfigurationError(
                f"load API key index {index} does not match test tenant {expected_tenant_id}"
            )
        if capacity.get("accepting_batches") is not True:
            raise LoadTestConfigurationError(
                f"load API key index {index} is not accepting GPU batches"
            )
        asset_identity_verified = asset_capacities is not None
        if asset_capacities is not None:
            asset_client = asset_capacities[index].get("client")
            if (
                not isinstance(asset_client, Mapping)
                or asset_client.get("kind") != "test"
                or asset_client.get("id") != expected_tenant_id
            ):
                raise LoadTestConfigurationError(
                    f"load API key index {index} does not match Asset test tenant "
                    f"{expected_tenant_id}"
                )
        checks.append(
            {
                "api_key_index": index,
                "tenant_id": expected_tenant_id,
                "client_kind": "test",
                "accepting_batches": True,
                "asset_identity_verified": asset_identity_verified,
            }
        )
    return checks


def validate_asset_worker_roles(
    workers: Sequence[Mapping[str, Any]],
    *,
    minimum_cpu_workers: int,
    minimum_cpu_slots: int,
    minimum_substance_slots: int,
) -> dict[str, Any]:
    """Require independent CPU and fenced-Substance capacity."""

    online: list[Mapping[str, Any]] = [
        worker for worker in workers if worker.get("status") == "ONLINE"
    ]
    substance = [
        worker
        for worker in online
        if SUBSTANCE_LOAD_WORKER_MARKER in str(worker.get("id", "")).lower()
    ]
    cpu = [worker for worker in online if worker not in substance]

    def available_slots(rows: Sequence[Mapping[str, Any]]) -> int:
        total = 0
        for worker in rows:
            maximum = worker.get("max_concurrency")
            current = worker.get("current_jobs")
            if (
                isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or maximum < 1
                or isinstance(current, bool)
                or not isinstance(current, int)
                or current < 0
                or current > maximum
            ):
                raise LoadTestConfigurationError(
                    "asset worker returned invalid current/max concurrency"
                )
            total += maximum - current
        return total

    cpu_slots = available_slots(cpu)
    substance_slots = available_slots(substance)
    if len(cpu) < minimum_cpu_workers:
        raise LoadTestConfigurationError("not enough online CPU asset workers")
    if cpu_slots < minimum_cpu_slots:
        raise LoadTestConfigurationError("no approved CPU asset worker capacity")
    if substance_slots < minimum_substance_slots:
        raise LoadTestConfigurationError("no approved Substance worker capacity")
    return {
        "online_workers": online,
        "cpu_workers": cpu,
        "substance_workers": substance,
        "cpu_available_slots": cpu_slots,
        "substance_available_slots": substance_slots,
    }


def identify_foreign_active_work(
    gpu_jobs: Sequence[Mapping[str, Any]],
    asset_jobs: Sequence[Mapping[str, Any]],
    *,
    test_tenant_ids: Sequence[str],
    session_id: str,
    roughness_request_key_indices: Mapping[str, int],
    roughness_idempotency_key_indices: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return minimal evidence for active work outside this load session.

    Asset rows use their session-prefixed external ID. ImageClip uses its
    external batch ID. Synchronous Roughness prefers the server-side
    idempotency key because a gateway may replace ``request_id``; older admin
    payloads fall back to the request/key binding registered by this harness.
    GPU rows also require ``client_kind=test`` when the field is present.
    Missing, cross-session, or unknown ownership fails closed. Business
    payloads are never returned.
    """

    approved_tenant_order = tuple(str(value) for value in test_tenant_ids if str(value))
    approved_tenants = set(approved_tenant_order)
    if not approved_tenants or len(approved_tenants) != len(test_tenant_ids):
        raise LoadTestConfigurationError("production watchdog requires unique LOAD_TEST_TENANT_IDS")
    if not SESSION_PATTERN.fullmatch(session_id):
        raise LoadTestConfigurationError("production watchdog requires a valid session id")
    tenant_key_indices = {tenant_id: index for index, tenant_id in enumerate(approved_tenant_order)}
    escaped_session = re.escape(session_id)
    roughness_pattern = re.compile(rf"^lt:{escaped_session}:mvr:[0-9]{{8}}$")
    roughness_idempotency_pattern = re.compile(
        rf"^load:{escaped_session}:mvr:[0-9]{{8}}$"
    )
    roughness_idempotency_bindings = dict(roughness_idempotency_key_indices or {})
    imageclip_pattern = re.compile(rf"^loadtest:{escaped_session}:imageclip_batch:[0-9]{{8}}$")
    asset_patterns = {
        api_name: re.compile(rf"^loadtest:{escaped_session}:{re.escape(api_name)}:[0-9]{{8}}$")
        for api_name in ASSET_JOB_TYPE_TO_API.values()
    }
    conflicts: list[dict[str, str]] = []
    for plane, rows in (("gpu", gpu_jobs), ("asset", asset_jobs)):
        for row in rows:
            if not isinstance(row, Mapping):
                raise LoadTestConfigurationError(
                    f"{plane} production watchdog received a non-object job"
                )
            status = str(row.get("status") or "")
            if status not in LOAD_ACTIVE_STATUSES:
                continue
            owner_field = "tenant_id" if plane == "gpu" else "client_id"
            owner = str(row.get(owner_field) or "")
            raw_client_kind = row.get("client_kind")
            client_kind = str(raw_client_kind) if raw_client_kind else "production"
            owner_is_load_tenant = owner in approved_tenants
            if plane == "gpu" and raw_client_kind is not None:
                belongs_to_load_tenant = client_kind == "test" and owner_is_load_tenant
            else:
                belongs_to_load_tenant = owner_is_load_tenant
            belongs_to_session = False
            if belongs_to_load_tenant and plane == "asset":
                api_name = ASSET_JOB_TYPE_TO_API.get(str(row.get("job_type") or ""))
                pattern = asset_patterns.get(api_name or "")
                belongs_to_session = (
                    pattern is not None
                    and pattern.fullmatch(str(row.get("external_asset_id") or "")) is not None
                )
            elif belongs_to_load_tenant and row.get("kind") == "batch":
                belongs_to_session = (
                    imageclip_pattern.fullmatch(str(row.get("external_batch_id") or "")) is not None
                )
            elif (
                belongs_to_load_tenant
                and row.get("kind") == "job"
                and row.get("workflow_key") == "modelview-roughness"
            ):
                request_id = str(row.get("request_id") or "")
                idempotency_key = str(row.get("idempotency_key") or "")
                if idempotency_key:
                    belongs_to_session = (
                        roughness_idempotency_pattern.fullmatch(idempotency_key) is not None
                        and roughness_idempotency_bindings.get(idempotency_key)
                        == tenant_key_indices.get(owner)
                    )
                else:
                    belongs_to_session = (
                        roughness_pattern.fullmatch(request_id) is not None
                        and roughness_request_key_indices.get(request_id)
                        == tenant_key_indices.get(owner)
                    )
            if belongs_to_session:
                continue
            identifier = row.get("job_id") or row.get("batch_id") or "unknown"
            conflict = {
                "plane": plane,
                "job_id": str(identifier),
                "status": status,
                "client_kind": client_kind,
            }
            if owner:
                conflict["owner_id"] = owner
            conflicts.append(conflict)
    return {
        "detected": bool(conflicts),
        "count": len(conflicts),
        "jobs": conflicts,
    }


def discover_scoped_teardown_tasks(
    gpu_jobs: Sequence[Mapping[str, Any]],
    asset_jobs: Sequence[Mapping[str, Any]],
    *,
    tenant_key_indices: Mapping[str, int],
    roughness_request_key_indices: Mapping[str, int],
    session_id: str,
    started_at: str,
    roughness_idempotency_key_indices: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Recover active run-owned tasks without widening cancellation scope.

    The configured load tenants are dedicated to one run and map one-to-one to
    API keys. Asset jobs and ImageClip batches must additionally carry the
    harness session prefix. The synchronous roughness contract has no external
    business ID, so its server-side idempotency key must exactly match a
    key/tenant binding registered by this harness. Older admin payloads without
    that field fall back to the registered request ID. ``created_at`` remains a
    secondary lower bound for every row. Any ambiguous active row owned by a
    load tenant aborts discovery instead of being guessed or cancelled.
    """

    if not SESSION_PATTERN.fullmatch(session_id):
        raise LoadTestConfigurationError("teardown scan received an invalid session id")
    normalized_indices: dict[str, int] = {}
    for tenant_id, raw_index in tenant_key_indices.items():
        tenant = str(tenant_id).strip()
        if (
            not tenant
            or isinstance(raw_index, bool)
            or not isinstance(raw_index, int)
            or raw_index < 0
        ):
            raise LoadTestConfigurationError(
                "teardown scan requires non-empty tenants and non-negative key indices"
            )
        normalized_indices[tenant] = raw_index
    if not normalized_indices or len(set(normalized_indices.values())) != len(normalized_indices):
        raise LoadTestConfigurationError(
            "teardown scan requires a unique API key index for every tenant"
        )
    roughness_request_pattern = re.compile(rf"^lt:{re.escape(session_id)}:mvr:[0-9]{{8}}$")
    roughness_idempotency_pattern = re.compile(
        rf"^load:{re.escape(session_id)}:mvr:[0-9]{{8}}$"
    )
    normalized_roughness_requests: dict[str, int] = {}
    for request_id, raw_index in roughness_request_key_indices.items():
        normalized_request_id = str(request_id).strip()
        if (
            not roughness_request_pattern.fullmatch(normalized_request_id)
            or isinstance(raw_index, bool)
            or not isinstance(raw_index, int)
            or raw_index < 0
            or raw_index not in normalized_indices.values()
        ):
            raise LoadTestConfigurationError(
                "teardown scan received an invalid roughness request binding"
            )
        normalized_roughness_requests[normalized_request_id] = raw_index
    normalized_roughness_idempotency_keys: dict[str, int] = {}
    for idempotency_key, raw_index in (roughness_idempotency_key_indices or {}).items():
        normalized_idempotency_key = str(idempotency_key).strip()
        if (
            not roughness_idempotency_pattern.fullmatch(normalized_idempotency_key)
            or isinstance(raw_index, bool)
            or not isinstance(raw_index, int)
            or raw_index < 0
            or raw_index not in normalized_indices.values()
        ):
            raise LoadTestConfigurationError(
                "teardown scan received an invalid roughness idempotency binding"
            )
        normalized_roughness_idempotency_keys[normalized_idempotency_key] = raw_index
    run_started_at = _parse_window_timestamp(started_at)
    if run_started_at is None:
        raise LoadTestConfigurationError("teardown scan requires an aware RFC3339 run start")

    escaped_session = re.escape(session_id)
    imageclip_pattern = re.compile(rf"^loadtest:{escaped_session}:imageclip_batch:[0-9]{{8}}$")
    asset_patterns = {
        api_name: re.compile(rf"^loadtest:{escaped_session}:{re.escape(api_name)}:[0-9]{{8}}$")
        for api_name in ASSET_JOB_TYPE_TO_API.values()
    }
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def common_fields(row: Mapping[str, Any], *, plane: str) -> tuple[str, str, int] | None:
        status = str(row.get("status") or "")
        if status not in LOAD_ACTIVE_STATUSES:
            return None
        owner_field = "tenant_id" if plane == "gpu" else "client_id"
        owner = str(row.get(owner_field) or "")
        if owner not in normalized_indices:
            return None
        if plane == "gpu" and row.get("client_kind") != "test":
            raise LoadTestConfigurationError(
                "teardown scan found a load-tenant GPU row without client_kind=test"
            )
        created_at = _parse_window_timestamp(str(row.get("created_at") or ""))
        if created_at is None:
            raise LoadTestConfigurationError(
                "teardown scan cannot scope an active load-tenant row without created_at"
            )
        if created_at < run_started_at:
            raise LoadTestConfigurationError(
                "teardown scan found pre-run active work in a load tenant"
            )
        identifier = str(row.get("job_id") or row.get("batch_id") or "")
        if not identifier or identifier in seen:
            raise LoadTestConfigurationError(
                "teardown scan found a missing or duplicate active task id"
            )
        seen.add(identifier)
        return identifier, status, normalized_indices[owner]

    for row in gpu_jobs:
        if not isinstance(row, Mapping):
            raise LoadTestConfigurationError("teardown scan received a non-object GPU job")
        common = common_fields(row, plane="gpu")
        if common is None:
            continue
        identifier, status, key_index = common
        kind = str(row.get("kind") or "")
        if kind == "batch":
            external_id = str(row.get("external_batch_id") or "")
            if imageclip_pattern.fullmatch(external_id) is None:
                raise LoadTestConfigurationError(
                    "teardown scan found an ambiguous load-tenant GPU batch"
                )
            discovered.append(
                {
                    "id": identifier,
                    "api": "imageclip_batch",
                    "kind": "batch",
                    "status_url": f"/api/v1/batches/{identifier}",
                    "cancel_url": f"/api/v1/batches/{identifier}/cancel",
                    "external_id": external_id,
                    "api_key_index": key_index,
                    "last_status": status,
                    "recovery_source": "admin_scope_scan",
                    "scope_basis": "tenant+created_at+exact_external_batch_id",
                }
            )
            continue
        roughness_request_id = str(row.get("request_id") or "")
        roughness_idempotency_key = str(row.get("idempotency_key") or "")
        if roughness_idempotency_key:
            roughness_identity_matches = (
                roughness_idempotency_pattern.fullmatch(roughness_idempotency_key) is not None
                and normalized_roughness_idempotency_keys.get(roughness_idempotency_key)
                == key_index
            )
            scope_basis = "tenant+created_at+workflow_key+idempotency_key"
        else:
            roughness_identity_matches = (
                roughness_request_pattern.fullmatch(roughness_request_id) is not None
                and normalized_roughness_requests.get(roughness_request_id) == key_index
            )
            scope_basis = "tenant+created_at+workflow_key+request_id"
        if (
            kind != "job"
            or row.get("workflow_key") != "modelview-roughness"
            or not roughness_identity_matches
        ):
            raise LoadTestConfigurationError("teardown scan found an ambiguous load-tenant GPU job")
        discovered.append(
            {
                "id": identifier,
                "api": "modelview_roughness",
                "kind": "job",
                "status_url": f"/api/v1/jobs/{identifier}",
                "cancel_url": f"/api/v1/jobs/{identifier}/cancel",
                "external_id": None,
                "api_key_index": key_index,
                "last_status": status,
                "recovery_source": "admin_scope_scan",
                "request_id": roughness_request_id,
                "idempotency_key": roughness_idempotency_key or None,
                "scope_basis": scope_basis,
            }
        )

    for row in asset_jobs:
        if not isinstance(row, Mapping):
            raise LoadTestConfigurationError("teardown scan received a non-object asset job")
        common = common_fields(row, plane="asset")
        if common is None:
            continue
        identifier, status, key_index = common
        external_id = str(row.get("external_asset_id") or "")
        api_name = ASSET_JOB_TYPE_TO_API.get(str(row.get("job_type") or ""))
        pattern = asset_patterns.get(api_name or "")
        if api_name is None or pattern is None or pattern.fullmatch(external_id) is None:
            raise LoadTestConfigurationError(
                "teardown scan found an ambiguous load-tenant asset job"
            )
        discovered.append(
            {
                "id": identifier,
                "api": api_name,
                "kind": "asset",
                "status_url": f"/api/v1/assets/jobs/{identifier}",
                "cancel_url": f"/api/v1/assets/jobs/{identifier}/cancel",
                "external_id": external_id,
                "api_key_index": key_index,
                "last_status": status,
                "recovery_source": "admin_scope_scan",
                "scope_basis": "tenant+created_at+job_type+exact_external_asset_id",
            }
        )

    return sorted(discovered, key=lambda item: (str(item["api"]), str(item["id"])))


def evaluate_load_lifecycle(
    records: Sequence[Mapping[str, Any]],
    teardown: Sequence[Mapping[str, Any]],
    *,
    mode: str = "all_complete",
    recovery_scan_passed: bool = True,
) -> dict[str, Any]:
    """Evaluate acceptance or bounded-stress lifecycle semantics."""

    if mode not in LOAD_LIFECYCLE_MODES:
        raise LoadTestConfigurationError(f"unsupported load lifecycle mode: {mode}")

    incomplete = [str(record.get("id")) for record in records if not record.get("terminal_status")]
    unsuccessful = [
        {
            "id": str(record.get("id")),
            "status": str(record.get("terminal_status")),
        }
        for record in records
        if record.get("terminal_status")
        and record.get("terminal_status") not in LOAD_SUCCESS_STATUSES
    ]
    missing_artifacts = [
        str(record.get("id"))
        for record in records
        if record.get("terminal_status") in LOAD_SUCCESS_STATUSES
        and int(record.get("artifact_count") or 0) < 1
    ]
    artifact_contract_failures = [
        str(record.get("id"))
        for record in records
        if record.get("artifact_contract_failed") is True
    ]
    artifact_contract_unverified = [
        str(record.get("id"))
        for record in records
        if record.get("terminal_status") in LOAD_SUCCESS_STATUSES
        and record.get("artifact_contract_verified") is not True
    ]
    poll_timeouts = [
        str(record.get("id")) for record in records if record.get("poll_timed_out") is True
    ]
    teardown_failed = [
        str(outcome.get("task_id"))
        for outcome in teardown
        if not (
            outcome.get("settled") is True
            and str(outcome.get("final_status") or "") in LOAD_ACCEPTABLE_TEARDOWN_STATUSES
        )
    ]
    verified_successes = [
        str(record.get("id"))
        for record in records
        if record.get("terminal_status") in LOAD_SUCCESS_STATUSES
        and int(record.get("artifact_count") or 0) >= 1
        and record.get("artifact_contract_failed") is not True
        and record.get("artifact_contract_verified") is True
    ]
    verified_successful_by_api = Counter(
        str(record.get("api") or "")
        for record in records
        if record.get("terminal_status") in LOAD_SUCCESS_STATUSES
        and int(record.get("artifact_count") or 0) >= 1
        and record.get("artifact_contract_failed") is not True
        and record.get("artifact_contract_verified") is True
    )
    missing_successful_apis = [
        api_name for api_name in API_NAMES if verified_successful_by_api[api_name] < 1
    ]
    safely_settled_ids = {
        str(outcome.get("task_id"))
        for outcome in teardown
        if str(outcome.get("task_id") or "")
        and outcome.get("settled") is True
        and str(outcome.get("final_status") or "") in LOAD_ACCEPTABLE_TEARDOWN_STATUSES
    }
    unresolved_incomplete = [
        identifier for identifier in incomplete if identifier not in safely_settled_ids
    ]
    bounded_unsuccessful = [
        item
        for item in unsuccessful
        if not (item["status"] == "CANCELLED" and item["id"] in safely_settled_ids)
    ]
    if mode == "all_complete":
        passed = bool(records) and not any(
            (
                incomplete,
                unsuccessful,
                missing_artifacts,
                artifact_contract_failures,
                artifact_contract_unverified,
                poll_timeouts,
                teardown,
                missing_successful_apis,
            )
        )
        policy = (
            "all six APIs require a successful terminal task with a verified artifact; "
            "all registered tasks must succeed and teardown means the run is incomplete"
        )
    else:
        passed = (
            bool(records)
            and not missing_successful_apis
            and recovery_scan_passed
            and not any(
                (
                    unresolved_incomplete,
                    bounded_unsuccessful,
                    missing_artifacts,
                    artifact_contract_failures,
                    artifact_contract_unverified,
                    poll_timeouts,
                    teardown_failed,
                )
            )
        )
        policy = (
            "bounded stress requires a verified successful artifact from every API and "
            "every residual task to be scope-recovered, cancelled or terminal, and "
            "observed settled"
        )
    return {
        "passed": passed,
        "mode": mode,
        "registered": len(records),
        "successful": sum(
            record.get("terminal_status") in LOAD_SUCCESS_STATUSES for record in records
        ),
        "verified_successful": len(verified_successes),
        "verified_successful_by_api": {
            api_name: verified_successful_by_api[api_name] for api_name in API_NAMES
        },
        "missing_successful_apis": missing_successful_apis,
        "incomplete_task_ids": incomplete,
        "unresolved_incomplete_task_ids": unresolved_incomplete,
        "unsuccessful_tasks": unsuccessful,
        "bounded_unsuccessful_tasks": bounded_unsuccessful,
        "missing_artifact_task_ids": missing_artifacts,
        "artifact_contract_failure_task_ids": artifact_contract_failures,
        "artifact_contract_unverified_task_ids": artifact_contract_unverified,
        "poll_timeout_task_ids": poll_timeouts,
        "teardown_attempted": len(teardown),
        "teardown_failed_task_ids": teardown_failed,
        "teardown_safely_settled_task_ids": sorted(safely_settled_ids),
        "recovery_scan_passed": recovery_scan_passed,
        "policy": policy,
    }


@dataclass(frozen=True)
class LoadStage:
    users: int
    duration_seconds: int
    spawn_rate: float


def select_load_shape_stage(
    stages: Sequence[LoadStage],
    run_time: float,
    *,
    stop_requested: bool,
) -> tuple[int, float] | None:
    """Return the active stage, or let the Shape end after a safety request."""

    if stop_requested:
        return None
    elapsed = 0
    for stage in stages:
        elapsed += stage.duration_seconds
        if run_time < elapsed:
            return stage.users, stage.spawn_rate
    return None


@dataclass(frozen=True)
class LoadScenario:
    source: Path
    weights: dict[str, int]
    weights_confirmed: bool
    stages: tuple[LoadStage, ...]
    poll_interval_seconds: float
    operation_timeout_seconds: dict[str, int]
    max_retries: int
    max_backup_age_hours: float
    lifecycle_mode: str
    preflight: dict[str, int]
    thresholds: dict[str, float]
    approved_workflows: dict[str, dict[str, str]]

    @property
    def maximum_users(self) -> int:
        return max(stage.users for stage in self.stages)

    @property
    def total_duration_seconds(self) -> int:
        return sum(stage.duration_seconds for stage in self.stages)

    def normalized_weights(self) -> dict[str, float]:
        total = sum(self.weights.values())
        return {name: round(self.weights[name] / total, 6) for name in API_NAMES}

    def resource_mix(self) -> dict[str, float]:
        total = sum(self.weights.values())
        gpu_weight = sum(
            self.weights[name]
            for name in API_NAMES
            if API_CONTRACTS[name]["resource"] in {"GPU", "GPU_FENCED_ASSET"}
        )
        cpu_weight = sum(
            self.weights[name] for name in API_NAMES if API_CONTRACTS[name]["resource"] == "CPU"
        )
        return {
            "gpu_consuming": round(gpu_weight / total, 6),
            "cpu": round(cpu_weight / total, 6),
        }


@dataclass(frozen=True)
class FixtureManifest:
    source: Path
    entries: dict[str, dict[str, Path | tuple[Path, ...]]]

    def paths_for(self, api_name: str) -> dict[str, Path | tuple[Path, ...]]:
        return self.entries[api_name]

    def all_paths(self) -> tuple[Path, ...]:
        result: list[Path] = []
        for entry in self.entries.values():
            for value in entry.values():
                result.extend(value if isinstance(value, tuple) else (value,))
        return tuple(result)


@dataclass(frozen=True)
class RuntimeSettings:
    target: str
    session_id: str
    environment: str
    allow_load_test: bool
    allow_production_load_test: bool
    target_allowlist: tuple[str, ...]
    production_targets: tuple[str, ...]
    confirmation_token: str
    change_id: str
    window_start: str
    window_end: str
    backup_dir: Path | None
    api_keys: tuple[str, ...]
    tenant_ids: tuple[str, ...]
    admin_bearer_token: str
    ca_file: Path | None
    result_dir: Path | None
    source_revision: str
    api_image_digest: str
    scheduler_image_digest: str
    asset_api_image_digest: str
    web_image_digest: str
    worker_image_digest: str
    release_evidence_commit: str
    release_evidence_path: str
    release_evidence_sha256: str
    substance_agent_sha256: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> RuntimeSettings:
        source = dict(os.environ if environment is None else environment)
        target = normalize_target(
            source.get("LOAD_TEST_TARGET")
            or source.get("LOAD_TEST_HOST")
            or "http://127.0.0.1:8000"
        )
        session_id = source.get("LOAD_TEST_SESSION_ID", "plan-only")
        if not SESSION_PATTERN.fullmatch(session_id):
            raise LoadTestConfigurationError(
                "LOAD_TEST_SESSION_ID must contain 1..48 safe ASCII characters"
            )
        api_keys = _split_nonempty(source.get("LOAD_TEST_API_KEYS", ""))
        if not api_keys and source.get("LOAD_TEST_API_KEY", "").strip():
            api_keys = (source["LOAD_TEST_API_KEY"].strip(),)
        tenant_ids = _split_nonempty(source.get("LOAD_TEST_TENANT_IDS", ""))
        if tenant_ids and (
            len(tenant_ids) != len(api_keys) or len(set(tenant_ids)) != len(tenant_ids)
        ):
            raise LoadTestConfigurationError(
                "LOAD_TEST_TENANT_IDS must uniquely match LOAD_TEST_API_KEYS one-to-one"
            )
        ca_value = source.get("LOAD_TEST_CA_FILE", "").strip()
        result_value = source.get("LOAD_TEST_RESULT_DIR", "").strip()
        production_targets = tuple(
            sorted(
                DEFAULT_PRODUCTION_HOSTS
                | set(_split_nonempty(source.get("LOAD_TEST_PRODUCTION_TARGETS", "")))
            )
        )
        return cls(
            target=target,
            session_id=session_id,
            environment=source.get("LOAD_TEST_ENVIRONMENT", "plan").strip().lower(),
            allow_load_test=source.get("ALLOW_LOAD_TEST", "").strip().lower() == "true",
            allow_production_load_test=source.get("ALLOW_PRODUCTION_LOAD_TEST", "").strip().lower()
            == "true",
            target_allowlist=_split_nonempty(source.get("LOAD_TEST_TARGET_ALLOWLIST", "")),
            production_targets=production_targets,
            confirmation_token=source.get("LOAD_TEST_CONFIRMATION_TOKEN", "").strip(),
            change_id=source.get("LOAD_TEST_CHANGE_ID", "").strip(),
            window_start=source.get("LOAD_TEST_WINDOW_START", "").strip(),
            window_end=source.get("LOAD_TEST_WINDOW_END", "").strip(),
            backup_dir=(
                Path(source["LOAD_TEST_BACKUP_DIR"].strip()).expanduser().resolve()
                if source.get("LOAD_TEST_BACKUP_DIR", "").strip()
                else None
            ),
            api_keys=api_keys,
            tenant_ids=tenant_ids,
            admin_bearer_token=source.get("LOAD_TEST_ADMIN_BEARER_TOKEN", "").strip(),
            ca_file=Path(ca_value).expanduser() if ca_value else None,
            result_dir=Path(result_value).expanduser() if result_value else None,
            source_revision=source.get("LOAD_TEST_SOURCE_REVISION", "").strip(),
            api_image_digest=source.get("LOAD_TEST_API_IMAGE_DIGEST", "").strip(),
            scheduler_image_digest=source.get("LOAD_TEST_SCHEDULER_IMAGE_DIGEST", "").strip(),
            asset_api_image_digest=source.get("LOAD_TEST_ASSET_API_IMAGE_DIGEST", "").strip(),
            web_image_digest=source.get("LOAD_TEST_WEB_IMAGE_DIGEST", "").strip(),
            worker_image_digest=source.get("LOAD_TEST_WORKER_IMAGE_DIGEST", "").strip(),
            release_evidence_commit=source.get(
                "LOAD_TEST_RELEASE_EVIDENCE_COMMIT", ""
            ).strip(),
            release_evidence_path=source.get(
                "LOAD_TEST_RELEASE_EVIDENCE_PATH", ""
            ).strip(),
            release_evidence_sha256=source.get(
                "LOAD_TEST_RELEASE_EVIDENCE_SHA256", ""
            ).strip(),
            substance_agent_sha256=source.get(
                "LOAD_TEST_SUBSTANCE_AGENT_SHA256", ""
            ).strip(),
        )

    @property
    def target_release_identity(self) -> dict[str, Any]:
        """Return the non-secret immutable release identity bound to this run."""

        return {
            "source_revision": self.source_revision or None,
            "image_digests": {
                "api": self.api_image_digest or None,
                "scheduler": self.scheduler_image_digest or None,
                "asset_api": self.asset_api_image_digest or None,
                "web": self.web_image_digest or None,
                "worker": self.worker_image_digest or None,
            },
            "release_evidence": {
                "commit": self.release_evidence_commit or None,
                "path": self.release_evidence_path or None,
                "sha256": self.release_evidence_sha256 or None,
                "remote_ref": LOAD_RELEASE_REMOTE_HEAD,
            },
            "deployment_inventory": {
                target: {
                    "component": component,
                    "host": LOAD_DEPLOYMENT_HOSTS[target],
                    "container_name": container_name,
                    "image_identity_type": "docker_container_config_image_id",
                    "image_id": {
                        "api": self.api_image_digest,
                        "scheduler": self.scheduler_image_digest,
                        "asset_api": self.asset_api_image_digest,
                        "web": self.web_image_digest,
                        "worker": self.worker_image_digest,
                    }[component]
                    or None,
                }
                for target, component, container_name, _ in LOAD_LIVE_DEPLOYMENT_TARGETS
            },
            "substance_agent": {
                "skill_version": "substance-baker-2026.08.12-v7",
                "installed_path": (
                    "D:\\GPUControl\\agent\\Invoke-GPUControlSubstanceAgent.ps1"
                ),
                "installed_script_sha256": self.substance_agent_sha256 or None,
                "instance_count": 4,
                "worker_ids": [
                    f"asset-worker-3090-b-windows-{index:02d}" for index in range(1, 5)
                ],
            },
        }

    @property
    def required_production_window_seconds(self) -> int:
        """Reserved time is scenario-specific and is added in execution validation."""

        return PRODUCTION_TEARDOWN_RESERVE_SECONDS + PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS

    @property
    def expected_confirmation_token(self) -> str:
        tenant_binding = hashlib.sha256(",".join(self.tenant_ids).encode()).hexdigest()
        release_binding = hashlib.sha256(
            json.dumps(
                self.target_release_identity,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if self.is_production_target():
            material = (
                "gpu-control-six-api:production:"
                f"{self.change_id}:{self.window_start}:{self.window_end}:"
                f"{self.backup_dir or ''}:"
                f"{self.session_id}:{self.target}:{tenant_binding}:{release_binding}:execute"
            ).encode()
        else:
            material = (
                "gpu-control-six-api:nonproduction:"
                f"{self.session_id}:{self.target}:{tenant_binding}:{release_binding}:execute"
            ).encode()
        return hashlib.sha256(material).hexdigest()

    @property
    def hostname(self) -> str:
        return str(urlsplit(self.target).hostname or "").lower()

    def is_production_target(self) -> bool:
        if self.environment in {"prod", "production"}:
            return True
        hostname = self.hostname
        production_hosts = {
            str(urlsplit(value).hostname or value).lower() for value in self.production_targets
        }
        if hostname in production_hosts:
            return True
        return "prod" in hostname or "production" in hostname

    def target_is_allowlisted(self) -> bool:
        if not self.target_allowlist or "*" in self.target_allowlist:
            return False
        allowed: set[str] = set()
        for value in self.target_allowlist:
            try:
                allowed.add(normalize_target(value))
            except LoadTestConfigurationError:
                return False
        return self.target in allowed

    def execution_blockers(
        self,
        scenario: LoadScenario,
        fixtures: FixtureManifest,
        *,
        repository_root: Path,
        now: datetime | None = None,
        validate_backup: bool = True,
        verified_release_evidence: Mapping[str, Any] | None = None,
        verified_live_deployment: Mapping[str, Any] | None = None,
    ) -> list[str]:
        blockers: list[str] = []
        production = self.is_production_target()
        if not self.allow_load_test:
            blockers.append("ALLOW_LOAD_TEST must be exactly true")
        if production:
            if self.session_id == "plan-only":
                blockers.append(
                    "production requires an explicit unique UUIDv4 LOAD_TEST_SESSION_ID"
                )
            else:
                try:
                    parsed_session_id = UUID(self.session_id)
                except ValueError:
                    parsed_session_id = None
                if (
                    parsed_session_id is None
                    or parsed_session_id.version != 4
                    or str(parsed_session_id) != self.session_id
                ):
                    blockers.append("production LOAD_TEST_SESSION_ID must be a canonical UUIDv4")
            if self.environment != "production":
                blockers.append("known production targets require LOAD_TEST_ENVIRONMENT=production")
            if not self.allow_production_load_test:
                blockers.append("ALLOW_PRODUCTION_LOAD_TEST must be exactly true")
            if not self.change_id:
                blockers.append("LOAD_TEST_CHANGE_ID is required for production")
            elif not CHANGE_ID_PATTERN.fullmatch(self.change_id):
                blockers.append("LOAD_TEST_CHANGE_ID contains unsupported characters")
            start = _parse_window_timestamp(self.window_start)
            end = _parse_window_timestamp(self.window_end)
            if start is None or end is None or start >= end:
                blockers.append(
                    "production requires a valid LOAD_TEST_WINDOW_START/END RFC3339 window"
                )
            else:
                current = now or datetime.now(UTC)
                if current < start or current >= end:
                    blockers.append("current time is outside the approved production window")
                required_window_seconds = (
                    scenario.total_duration_seconds + self.required_production_window_seconds
                )
                if (end - start).total_seconds() < required_window_seconds:
                    blockers.append(
                        "production window must cover all load stages plus 300 seconds "
                        "teardown and 540 seconds preflight/evidence reserve"
                    )
                planned_end = current.timestamp() + required_window_seconds
                if planned_end > end.timestamp():
                    blockers.append(
                        "remaining production window must cover all load stages plus "
                        "300 seconds teardown and 540 seconds preflight/evidence reserve"
                    )
            if (
                scenario.preflight.get("maximum_preexisting_gpu_jobs") != 0
                or scenario.preflight.get("maximum_preexisting_asset_jobs") != 0
            ):
                blockers.append(
                    "production scenarios must require zero pre-existing GPU and asset jobs"
                )
            if (
                scenario.maximum_users < 100
                or scenario.maximum_users > 120
                or scenario.stages[-1].users != scenario.maximum_users
            ):
                blockers.append(
                    "production six-API scenario must peak between 100 and 120 users "
                    "and finish at that peak"
                )
            if len(self.api_keys) < MINIMUM_PRODUCTION_LOAD_IDENTITIES or len(
                set(self.api_keys)
            ) != len(self.api_keys):
                blockers.append("production requires at least 12 unique LOAD_TEST_API_KEYS")
            if (
                len(self.tenant_ids) < MINIMUM_PRODUCTION_LOAD_IDENTITIES
                or len(set(self.tenant_ids)) != len(self.tenant_ids)
                or len(self.tenant_ids) != len(self.api_keys)
            ):
                blockers.append(
                    "production requires at least 12 unique LOAD_TEST_TENANT_IDS mapped "
                    "one-to-one to unique API keys"
                )
            if not COMMIT_PATTERN.fullmatch(self.source_revision):
                blockers.append(
                    "LOAD_TEST_SOURCE_REVISION must be the target release's full 40-character "
                    "lowercase Git revision"
                )
            image_digests = self.target_release_identity["image_digests"]
            for component in RELEASE_IMAGE_COMPONENTS:
                digest = str(image_digests[component] or "")
                if not IMAGE_DIGEST_PATTERN.fullmatch(digest):
                    env_name = f"LOAD_TEST_{component.upper()}_IMAGE_DIGEST"
                    blockers.append(
                        f"{env_name} must be an immutable lowercase sha256:<64-hex> digest"
                    )
            if not COMMIT_PATTERN.fullmatch(self.release_evidence_commit):
                blockers.append(
                    "LOAD_TEST_RELEASE_EVIDENCE_COMMIT must be the full origin/main evidence "
                    "commit"
                )
            if not LOAD_RELEASE_EVIDENCE_PATH_PATTERN.fullmatch(
                self.release_evidence_path
            ):
                blockers.append(
                    "LOAD_TEST_RELEASE_EVIDENCE_PATH must name the packaged live deployment receipt"
                )
            if not HASH_PATTERN.fullmatch(self.release_evidence_sha256):
                blockers.append(
                    "LOAD_TEST_RELEASE_EVIDENCE_SHA256 must be the evidence blob SHA-256"
                )
            if not HASH_PATTERN.fullmatch(self.substance_agent_sha256):
                blockers.append(
                    "LOAD_TEST_SUBSTANCE_AGENT_SHA256 must bind the installed Windows v6 script"
                )
            expected_anchor = self.target_release_identity["release_evidence"]
            verified_images = (
                verified_release_evidence.get("images")
                if isinstance(verified_release_evidence, Mapping)
                else None
            )
            verified_image_ids_match = isinstance(verified_images, Mapping) and set(
                verified_images
            ) == set(RELEASE_IMAGE_COMPONENTS) and all(
                isinstance(verified_images.get(component), Mapping)
                and verified_images[component].get("local_image_id")
                == image_digests[component]
                for component in RELEASE_IMAGE_COMPONENTS
            )
            if (
                not isinstance(verified_release_evidence, Mapping)
                or verified_release_evidence.get("schema_version")
                != "gpu-control-load-release-evidence-verification.v1"
                or verified_release_evidence.get("verified") is not True
                or verified_release_evidence.get("origin_url")
                not in LOAD_RELEASE_ORIGIN_URLS
                or verified_release_evidence.get("remote_ref")
                != LOAD_RELEASE_REMOTE_HEAD
                or verified_release_evidence.get("evidence_commit")
                != expected_anchor["commit"]
                or verified_release_evidence.get("evidence_path")
                != expected_anchor["path"]
                or verified_release_evidence.get("evidence_sha256")
                != expected_anchor["sha256"]
                or verified_release_evidence.get("source_revision")
                != self.source_revision
                or not verified_image_ids_match
            ):
                blockers.append(
                    "production requires release evidence verified from the current "
                    "GitHub origin/main"
                )
            if (
                not isinstance(verified_live_deployment, Mapping)
                or verified_live_deployment.get("schema_version")
                != "gpu-control-load-live-deployment-verification.v1"
                or verified_live_deployment.get("verified") is not True
                or verified_live_deployment.get("release_evidence_commit")
                != self.release_evidence_commit
                or verified_live_deployment.get("source_revision")
                != self.source_revision
                or verified_live_deployment.get("inventory")
                != self.target_release_identity["deployment_inventory"]
                or verified_live_deployment.get("substance_agent")
                != self.target_release_identity["substance_agent"]
            ):
                blockers.append(
                    "production requires all seven live containers to match the remote "
                    "release evidence"
                )
            if self.backup_dir is None:
                blockers.append("LOAD_TEST_BACKUP_DIR is required for production")
            elif start is not None and validate_backup:
                try:
                    validate_production_backup(
                        self.backup_dir,
                        approved_window_start=start,
                        max_age_hours=scenario.max_backup_age_hours,
                    )
                except LoadTestConfigurationError as exc:
                    blockers.append(str(exc))
        elif self.environment not in NON_PRODUCTION_ENVIRONMENTS:
            blockers.append("LOAD_TEST_ENVIRONMENT must name a non-production environment")
        if not self.target_is_allowlisted():
            blockers.append(
                "target must exactly match an HTTP(S) origin in LOAD_TEST_TARGET_ALLOWLIST"
            )
        if self.confirmation_token != self.expected_confirmation_token:
            blockers.append("LOAD_TEST_CONFIRMATION_TOKEN does not match this session and target")
        if not self.api_keys:
            blockers.append("LOAD_TEST_API_KEYS must be supplied from the environment")
        if not self.tenant_ids or len(self.tenant_ids) != len(self.api_keys):
            blockers.append(
                "LOAD_TEST_TENANT_IDS must uniquely match LOAD_TEST_API_KEYS one-to-one"
            )
        if not self.admin_bearer_token:
            blockers.append("LOAD_TEST_ADMIN_BEARER_TOKEN is required for read-only preflight")
        if not scenario.weights_confirmed:
            blockers.append("scenario weights_confirmed must be true after real-traffic review")
        if scenario.maximum_users < 100:
            blockers.append("scenario must include a stage with at least 100 users")
        if scenario.maximum_users > 120:
            blockers.append("scenario cannot exceed the approved safety cap of 120 users")
        if self.result_dir is None:
            blockers.append("LOAD_TEST_RESULT_DIR must be an explicit new result directory")
        if urlsplit(self.target).scheme == "https":
            if self.ca_file is None or not self.ca_file.is_file():
                blockers.append("HTTPS targets require a readable LOAD_TEST_CA_FILE")
        try:
            validate_fixture_files(fixtures, repository_root=repository_root)
        except LoadTestConfigurationError as exc:
            blockers.append(str(exc))
        return blockers

    def assert_execution_allowed(
        self,
        scenario: LoadScenario,
        fixtures: FixtureManifest,
        *,
        repository_root: Path,
        now: datetime | None = None,
        verified_release_evidence: Mapping[str, Any] | None = None,
        verified_live_deployment: Mapping[str, Any] | None = None,
    ) -> None:
        blockers = self.execution_blockers(
            scenario,
            fixtures,
            repository_root=repository_root,
            now=now,
            verified_release_evidence=verified_release_evidence,
            verified_live_deployment=verified_live_deployment,
        )
        if blockers:
            raise LoadTestConfigurationError("; ".join(blockers))


def _split_nonempty(raw: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _parse_window_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LoadTestConfigurationError(f"{label} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LoadTestConfigurationError(f"cannot read YAML {path}: {exc}") from exc
    return _mapping(payload, str(path))


def normalize_target(raw: str) -> str:
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LoadTestConfigurationError("load-test target must be an HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LoadTestConfigurationError(
            "load-test target cannot contain credentials/query/fragment"
        )
    if parsed.path not in {"", "/"}:
        raise LoadTestConfigurationError("load-test target must not contain a path")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
    return f"{parsed.scheme}://{host}{port}"


def load_scenario(path: Path) -> LoadScenario:
    source = path.expanduser().resolve()
    payload = _load_yaml(source)
    if str(payload.get("schema_version")) != "1.0":
        raise LoadTestConfigurationError("scenario schema_version must be 1.0")
    raw_weights = _mapping(payload.get("weights"), "weights")
    if set(raw_weights) != set(API_NAMES):
        missing = sorted(set(API_NAMES) - set(raw_weights))
        extra = sorted(set(raw_weights) - set(API_NAMES))
        raise LoadTestConfigurationError(
            f"weights must contain exactly six APIs; missing={missing}, extra={extra}"
        )
    weights: dict[str, int] = {}
    for name in API_NAMES:
        value = raw_weights[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise LoadTestConfigurationError(f"weight for {name} must be a positive integer")
        weights[name] = value

    raw_stages = payload.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise LoadTestConfigurationError("stages must be a non-empty list")
    stages: list[LoadStage] = []
    previous_users = 0
    for index, raw_stage in enumerate(raw_stages):
        stage = _mapping(raw_stage, f"stages[{index}]")
        try:
            users = int(stage.get("users", 0))
            duration = int(stage.get("duration_seconds", 0))
            spawn_rate = float(stage.get("spawn_rate", 0))
        except (TypeError, ValueError) as exc:
            raise LoadTestConfigurationError(
                f"stages[{index}] users/duration/spawn_rate must be numeric"
            ) from exc
        if users < 1 or duration < 1 or spawn_rate <= 0:
            raise LoadTestConfigurationError(
                f"stages[{index}] users/duration/spawn_rate must be positive"
            )
        if users < previous_users:
            raise LoadTestConfigurationError("stages must not reduce users")
        stages.append(LoadStage(users, duration, spawn_rate))
        previous_users = users
    if max(stage.users for stage in stages) < 100:
        raise LoadTestConfigurationError("a 100+ virtual-user stage is required")

    raw_timeouts = _mapping(payload.get("operation_timeout_seconds"), "operation_timeout_seconds")
    if set(raw_timeouts) != set(API_NAMES):
        raise LoadTestConfigurationError(
            "operation_timeout_seconds must contain exactly the six API names"
        )
    try:
        operation_timeouts = {name: int(raw_timeouts[name]) for name in API_NAMES}
    except (TypeError, ValueError) as exc:
        raise LoadTestConfigurationError("operation timeouts must be integers") from exc
    if any(value < 1 for value in operation_timeouts.values()):
        raise LoadTestConfigurationError("operation timeouts must be positive")

    approved = _mapping(payload.get("approved_workflows"), "approved_workflows")
    for workflow_key in ("imageclip-rgba", "modelview-roughness"):
        approval = _mapping(approved.get(workflow_key), f"approved_workflows.{workflow_key}")
        if not approval.get("version") or not HASH_PATTERN.fullmatch(
            str(approval.get("template_sha256", ""))
        ):
            raise LoadTestConfigurationError(
                f"{workflow_key} approval requires version and 64-hex template_sha256"
            )
        approved[workflow_key] = {str(key): str(value) for key, value in approval.items()}
    imageclip = approved["imageclip-rgba"]
    if not COMMIT_PATTERN.fullmatch(imageclip.get("pipeline_commit", "")):
        raise LoadTestConfigurationError("ImageClip approval requires 40-hex pipeline_commit")
    if not HASH_PATTERN.fullmatch(imageclip.get("pipeline_sha256", "")):
        raise LoadTestConfigurationError("ImageClip approval requires 64-hex pipeline_sha256")
    if not imageclip.get("output_node"):
        raise LoadTestConfigurationError("ImageClip approval requires output_node")

    preflight_defaults = {
        "maximum_preexisting_gpu_jobs": 0,
        "maximum_preexisting_asset_jobs": 0,
        "minimum_healthy_gpu_nodes": 1,
        "minimum_online_asset_workers": 1,
        "minimum_cpu_slots": 1,
        "minimum_substance_slots": 1,
    }
    raw_preflight = _mapping(payload.get("preflight", {}), "preflight")
    try:
        preflight = {
            key: int(raw_preflight.get(key, default)) for key, default in preflight_defaults.items()
        }
    except (TypeError, ValueError) as exc:
        raise LoadTestConfigurationError("preflight thresholds must be integers") from exc
    if any(value < 0 for value in preflight.values()):
        raise LoadTestConfigurationError("preflight thresholds cannot be negative")
    if preflight["minimum_healthy_gpu_nodes"] < 3:
        raise LoadTestConfigurationError(
            "six-API cluster load requires at least three healthy GPU nodes"
        )
    if preflight["minimum_online_asset_workers"] < 3:
        raise LoadTestConfigurationError(
            "six-API cluster load requires at least three online CPU asset workers"
        )
    if preflight["minimum_cpu_slots"] < 1:
        raise LoadTestConfigurationError(
            "six-API cluster load requires at least one CPU asset slot"
        )
    if preflight["minimum_substance_slots"] < 1:
        raise LoadTestConfigurationError(
            "six-API cluster load requires at least one Substance slot"
        )
    raw_thresholds = _mapping(payload.get("thresholds", {}), "thresholds")
    try:
        thresholds = {str(key): float(value) for key, value in raw_thresholds.items()}
        poll_interval_seconds = float(payload.get("poll_interval_seconds", 2.0))
        max_retries = int(payload.get("max_retries", 4))
        max_backup_age_hours = float(payload.get("max_backup_age_hours", 24.0))
    except (TypeError, ValueError) as exc:
        raise LoadTestConfigurationError(
            "thresholds, poll interval, retries, and backup age must be numeric"
        ) from exc
    if any(value < 0 for value in thresholds.values()):
        raise LoadTestConfigurationError("metric thresholds cannot be negative")
    if not REQUIRED_METRIC_THRESHOLD_NAMES.issubset(thresholds) or not set(thresholds).issubset(
        METRIC_THRESHOLD_NAMES
    ):
        missing_thresholds = sorted(REQUIRED_METRIC_THRESHOLD_NAMES - set(thresholds))
        unknown_thresholds = sorted(set(thresholds) - METRIC_THRESHOLD_NAMES)
        raise LoadTestConfigurationError(
            "thresholds must contain every required metric and only supported metrics; "
            f"missing={missing_thresholds}, extra={unknown_thresholds}"
        )
    if poll_interval_seconds <= 0:
        raise LoadTestConfigurationError("poll_interval_seconds must be positive")
    if max_retries < 0 or max_retries > 20:
        raise LoadTestConfigurationError("max_retries must be between 0 and 20")
    if max_backup_age_hours <= 0 or max_backup_age_hours > 168:
        raise LoadTestConfigurationError(
            "max_backup_age_hours must be greater than 0 and at most 168"
        )
    lifecycle_mode = str(payload.get("lifecycle_mode", "all_complete")).strip()
    if lifecycle_mode not in LOAD_LIFECYCLE_MODES:
        raise LoadTestConfigurationError(
            f"lifecycle_mode must be one of {sorted(LOAD_LIFECYCLE_MODES)}"
        )
    return LoadScenario(
        source=source,
        weights=weights,
        weights_confirmed=payload.get("weights_confirmed") is True,
        stages=tuple(stages),
        poll_interval_seconds=poll_interval_seconds,
        operation_timeout_seconds=operation_timeouts,
        max_retries=max_retries,
        max_backup_age_hours=max_backup_age_hours,
        lifecycle_mode=lifecycle_mode,
        preflight=preflight,
        thresholds=thresholds,
        approved_workflows={
            key: dict(value) for key, value in approved.items() if isinstance(value, dict)
        },
    )


def load_fixture_manifest(path: Path) -> FixtureManifest:
    source = path.expanduser().resolve()
    payload = _load_yaml(source)
    if str(payload.get("schema_version")) != "1.0":
        raise LoadTestConfigurationError("fixture schema_version must be 1.0")
    raw_apis = _mapping(payload.get("apis"), "fixtures.apis")
    if set(raw_apis) != set(API_NAMES):
        raise LoadTestConfigurationError("fixtures.apis must contain exactly the six API names")
    entries: dict[str, dict[str, Path | tuple[Path, ...]]] = {}
    for api_name in API_NAMES:
        raw_entry = _mapping(raw_apis[api_name], f"fixtures.apis.{api_name}")
        missing = set(REQUIRED_FIXTURE_PATHS[api_name]) - set(raw_entry)
        if missing:
            raise LoadTestConfigurationError(f"fixture {api_name} is missing {sorted(missing)}")
        parsed: dict[str, Path | tuple[Path, ...]] = {}
        for key, value in raw_entry.items():
            if key == "reference_images":
                if value is None:
                    parsed[key] = ()
                elif isinstance(value, list):
                    parsed[key] = tuple(_resolve_fixture_path(source, item) for item in value)
                else:
                    raise LoadTestConfigurationError("reference_images must be a list")
            else:
                parsed[key] = _resolve_fixture_path(source, value)
        entries[api_name] = parsed
    return FixtureManifest(source=source, entries=entries)


def _resolve_fixture_path(manifest: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise LoadTestConfigurationError("fixture paths must be non-empty strings")
    path = Path(value).expanduser()
    return (manifest.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadTestConfigurationError(f"invalid JSON fixture {path}: {exc}") from exc
    return _mapping(payload, str(path))


def validate_fixture_files(fixtures: FixtureManifest, *, repository_root: Path) -> None:
    for path in fixtures.all_paths():
        if _is_within(path, repository_root):
            raise LoadTestConfigurationError(
                f"load fixtures must remain outside the repository: {path}"
            )
        if not path.is_file() or path.stat().st_size < 1:
            raise LoadTestConfigurationError(f"fixture is missing or empty: {path}")

    imageclip_entry = fixtures.paths_for("imageclip_batch")
    archive_path = _as_path(imageclip_entry["archive"])
    manifest = _json_file(_as_path(imageclip_entry["manifest"]))
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise LoadTestConfigurationError("ImageClip fixture manifest needs non-empty frames")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = {item.filename: item for item in archive.infolist() if not item.is_dir()}
            for frame in frames:
                item = _mapping(frame, "ImageClip frame")
                relative_path = str(item.get("relative_path", ""))
                info = infos.get(relative_path)
                if info is None:
                    raise LoadTestConfigurationError(
                        f"ImageClip archive is missing {relative_path}"
                    )
                if info.compress_type != zipfile.ZIP_STORED:
                    raise LoadTestConfigurationError("ImageClip input archive must use ZIP_STORED")
                content = archive.read(info)
                if len(content) != int(item.get("size_bytes", -1)):
                    raise LoadTestConfigurationError(f"ImageClip size mismatch for {relative_path}")
                if hashlib.sha256(content).hexdigest() != str(item.get("sha256", "")):
                    raise LoadTestConfigurationError(
                        f"ImageClip SHA-256 mismatch for {relative_path}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise LoadTestConfigurationError(f"invalid ImageClip archive: {exc}") from exc

    from packages.gpu_control_core.assets import (
        AssetCreateMetadata,
        RetopologyAuditMetadata,
        RetopologyProcessMetadata,
        SubstanceBakeMetadata,
    )

    try:
        AssetCreateMetadata.model_validate(
            _json_file(_as_path(fixtures.paths_for("uv_process")["metadata"]))
        )
        RetopologyAuditMetadata.model_validate(
            _json_file(_as_path(fixtures.paths_for("retopology_audit")["metadata"]))
        )
        process = RetopologyProcessMetadata.model_validate(
            _json_file(_as_path(fixtures.paths_for("retopology_process")["metadata"]))
        )
        bake = SubstanceBakeMetadata.model_validate(
            _json_file(_as_path(fixtures.paths_for("substance_bake")["metadata"]))
        )
    except ValueError as exc:
        raise LoadTestConfigurationError(f"asset metadata fixture is invalid: {exc}") from exc

    reference_values = fixtures.paths_for("retopology_process").get("reference_images", ())
    references = reference_values if isinstance(reference_values, tuple) else ()
    if sorted(path.name for path in references) != sorted(
        item.filename for item in process.reference_views
    ):
        raise LoadTestConfigurationError(
            "retopology reference_images must match metadata.reference_views"
        )
    bake_entry = fixtures.paths_for("substance_bake")
    if bake.options.profile in {"normal-dx-v1", "pbr-core-v1", "li3d-pbr-full-v2"}:
        _require_fixture_keys(bake_entry, "substance_bake", ("high_mesh",))
    if bake.options.profile == "li3d-pbr-full-v2":
        _require_fixture_keys(
            bake_entry,
            "substance_bake",
            ("base_color_texture", "roughness_texture", "metallic_texture"),
        )


def _require_fixture_keys(
    entry: Mapping[str, Path | tuple[Path, ...]], api_name: str, keys: Sequence[str]
) -> None:
    missing = [key for key in keys if key not in entry]
    if missing:
        raise LoadTestConfigurationError(f"fixture {api_name} is missing {missing}")


def _as_path(value: Path | tuple[Path, ...]) -> Path:
    if not isinstance(value, Path):
        raise LoadTestConfigurationError("expected one fixture path")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_key_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoadTestConfigurationError(f"cannot read backup control file {path.name}") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or "=" not in line:
            raise LoadTestConfigurationError(f"invalid backup control line in {path.name}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise LoadTestConfigurationError(f"invalid backup control key in {path.name}")
        values[key] = value
    return values


def _backup_timestamp(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise LoadTestConfigurationError("backup CREATED_UTC is invalid") from exc


def _validate_zero_quiesce_gate(path: Path) -> None:
    expected = {
        "active_jobs",
        "active_batches",
        "active_asset_jobs",
        "busy_nodes",
        "accepting_online_nodes",
    }
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoadTestConfigurationError(f"cannot read production {path.name}") from exc
    for line in lines:
        if "=" not in line:
            raise LoadTestConfigurationError(f"production {path.name} is invalid")
        key, raw_value = line.split("=", 1)
        if key not in expected or key in values or not raw_value.isdigit():
            raise LoadTestConfigurationError(f"production {path.name} is invalid")
        values[key] = int(raw_value)
    if set(values) != expected or any(values.values()):
        raise LoadTestConfigurationError(
            f"production {path.name} must prove every quiesce counter was zero"
        )


def _validate_production_backup(
    backup_dir: Path,
    *,
    approved_window_start: datetime,
    max_age_hours: float,
) -> dict[str, Any]:
    """Validate a backup.sh full recovery point without executing restore code."""

    candidate = backup_dir.expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise LoadTestConfigurationError("LOAD_TEST_BACKUP_DIR must be a real directory")
    root = candidate.resolve()
    root_stat = root.stat()
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise LoadTestConfigurationError("production backup directory mode must be 0700")
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise LoadTestConfigurationError("cannot enumerate production backup") from exc
    if not entries:
        raise LoadTestConfigurationError("production backup directory is empty")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise LoadTestConfigurationError(
                "production backup top level must contain only regular files"
            )
        entry_stat = entry.stat()
        if entry_stat.st_uid != root_stat.st_uid:
            raise LoadTestConfigurationError("production backup ownership is inconsistent")
        if stat.S_IMODE(entry_stat.st_mode) != 0o600:
            raise LoadTestConfigurationError("production backup file mode must be 0600")

    required = {"BACKUP_COMPLETE", "BACKUP_MANIFEST", "SHA256SUMS"}
    entry_by_name = {entry.name: entry for entry in entries}
    if not required.issubset(entry_by_name):
        raise LoadTestConfigurationError("production backup control files are incomplete")
    missing_payloads = sorted(REQUIRED_FULL_BACKUP_PAYLOADS - set(entry_by_name))
    if missing_payloads:
        raise LoadTestConfigurationError(
            f"production full backup is missing required payloads: {missing_payloads}"
        )
    empty_payloads = sorted(
        name for name in NONEMPTY_FULL_BACKUP_PAYLOADS if entry_by_name[name].stat().st_size < 1
    )
    if empty_payloads:
        raise LoadTestConfigurationError(
            f"production full backup has empty required payloads: {empty_payloads}"
        )
    complete = _backup_key_values(root / "BACKUP_COMPLETE")
    manifest = _backup_key_values(root / "BACKUP_MANIFEST")
    if complete.get("STATUS") != "COMPLETE" or complete.get("MODE") != "full":
        raise LoadTestConfigurationError("BACKUP_COMPLETE must be STATUS=COMPLETE MODE=full")
    if (
        manifest.get("BACKUP_FORMAT") != "2"
        or manifest.get("MODE") != "full"
        or manifest.get("QUIESCE_CHECK") != "ENFORCED_PRE_AND_POST"
    ):
        raise LoadTestConfigurationError(
            "BACKUP_MANIFEST must be format 2 full with enforced pre/post quiesce"
        )
    manifest_required = {
        "REPOSITORY_ROOT",
        "GIT_HEAD",
        "POSTGRES_CONTAINER",
        "POSTGRES_USER",
        "POSTGRES_DB",
    }
    if any(not manifest.get(key) for key in manifest_required):
        raise LoadTestConfigurationError(
            "BACKUP_MANIFEST is missing required full-backup identity fields"
        )
    if not Path(manifest["REPOSITORY_ROOT"]).is_absolute():
        raise LoadTestConfigurationError("BACKUP_MANIFEST REPOSITORY_ROOT must be absolute")
    if not COMMIT_PATTERN.fullmatch(manifest["GIT_HEAD"]):
        raise LoadTestConfigurationError("BACKUP_MANIFEST GIT_HEAD is invalid")
    try:
        recorded_git_head = (root / "git-head.txt").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoadTestConfigurationError("cannot read production git-head.txt") from exc
    if recorded_git_head != manifest["GIT_HEAD"]:
        raise LoadTestConfigurationError("BACKUP_MANIFEST GIT_HEAD does not match git-head.txt")
    try:
        with (root / "database.dump").open("rb") as database_file:
            database_header = database_file.read(5)
        with (root / "repository.bundle").open("rb") as bundle_file:
            bundle_header = bundle_file.read(32)
    except OSError as exc:
        raise LoadTestConfigurationError("cannot read critical full-backup payload") from exc
    if database_header != b"PGDMP":
        raise LoadTestConfigurationError(
            "production database.dump is not a PostgreSQL custom-format dump"
        )
    if not bundle_header.startswith((b"# v2 git bundle", b"# v3 git bundle")):
        raise LoadTestConfigurationError("production repository.bundle header is invalid")
    _validate_zero_quiesce_gate(root / "quiesce-gate-pre.txt")
    _validate_zero_quiesce_gate(root / "quiesce-gate-post.txt")
    created_raw = complete.get("CREATED_UTC", "")
    if manifest.get("CREATED_UTC") != created_raw:
        raise LoadTestConfigurationError("backup marker and manifest timestamps differ")
    created_at = _backup_timestamp(created_raw)
    window_start = approved_window_start.astimezone(UTC)
    age_hours = (window_start - created_at).total_seconds() / 3600
    if age_hours <= 0:
        raise LoadTestConfigurationError("production backup must predate the approved window")
    if age_hours > max_age_hours:
        raise LoadTestConfigurationError("production backup is older than max_backup_age_hours")

    expected_sums_hash = complete.get("SHA256SUMS_SHA256", "")
    if not HASH_PATTERN.fullmatch(expected_sums_hash):
        raise LoadTestConfigurationError("BACKUP_COMPLETE has no valid SHA256SUMS hash")
    sums_path = root / "SHA256SUMS"
    if file_sha256(sums_path) != expected_sums_hash:
        raise LoadTestConfigurationError("SHA256SUMS does not match BACKUP_COMPLETE")

    listed: dict[str, str] = {}
    try:
        sum_lines = sums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LoadTestConfigurationError("cannot read production SHA256SUMS") from exc
    for line in sum_lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise LoadTestConfigurationError("production SHA256SUMS line is invalid")
        digest = line[:64]
        name = line[66:]
        if not HASH_PATTERN.fullmatch(digest) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*", name
        ):
            raise LoadTestConfigurationError("production SHA256SUMS entry is invalid")
        if name in {"SHA256SUMS", "BACKUP_COMPLETE"} or name in listed:
            raise LoadTestConfigurationError("production SHA256SUMS has an unsafe entry")
        payload = root / name
        if payload.is_symlink() or not payload.is_file():
            raise LoadTestConfigurationError("production SHA256SUMS payload is missing")
        listed[name] = digest
    if not listed:
        raise LoadTestConfigurationError("production SHA256SUMS is empty")
    expected_files = {
        entry.name for entry in entries if entry.name not in {"SHA256SUMS", "BACKUP_COMPLETE"}
    }
    if set(listed) != expected_files:
        raise LoadTestConfigurationError(
            "production SHA256SUMS does not exactly cover backup payloads"
        )
    for name, digest in listed.items():
        if file_sha256(root / name) != digest:
            raise LoadTestConfigurationError(f"production backup checksum failed for {name}")

    latest_mtime = max(datetime.fromtimestamp(entry.stat().st_mtime, UTC) for entry in entries)
    if latest_mtime >= window_start:
        raise LoadTestConfigurationError(
            "production backup must be completely finalized before the approved window"
        )

    for sibling in root.parent.iterdir():
        if sibling == root or sibling.is_symlink() or not sibling.is_dir():
            continue
        sibling_marker = sibling / "BACKUP_COMPLETE"
        sibling_manifest = sibling / "BACKUP_MANIFEST"
        if not sibling_marker.is_file() or not sibling_manifest.is_file():
            continue
        try:
            other_complete = _backup_key_values(sibling_marker)
            other_manifest = _backup_key_values(sibling_manifest)
            other_created = _backup_timestamp(other_complete.get("CREATED_UTC", ""))
        except LoadTestConfigurationError:
            continue
        if (
            other_complete.get("STATUS") == "COMPLETE"
            and other_complete.get("MODE") == "full"
            and other_manifest.get("MODE") == "full"
            and other_created > created_at
        ):
            raise LoadTestConfigurationError(
                "LOAD_TEST_BACKUP_DIR is not the newest completed full backup"
            )

    return {
        "path": str(root),
        "created_utc": created_raw,
        "age_at_window_start_hours": round(age_hours, 6),
        "max_backup_age_hours": max_age_hours,
        "sha256sums_sha256": expected_sums_hash,
        "payload_count": len(listed),
        "latest_file_mtime_utc": latest_mtime.isoformat(),
        "status": "VERIFIED_FULL_PRE_WINDOW",
    }


def validate_production_backup(
    backup_dir: Path,
    *,
    approved_window_start: datetime,
    max_age_hours: float,
) -> dict[str, Any]:
    try:
        return _validate_production_backup(
            backup_dir,
            approved_window_start=approved_window_start,
            max_age_hours=max_age_hours,
        )
    except OSError as exc:
        raise LoadTestConfigurationError(
            f"cannot verify production backup: {type(exc).__name__}"
        ) from exc


def write_result_manifest(result_dir: Path, *, session_id: str) -> None:
    """Refresh the evidence inventory after all result writers have closed."""

    root = result_dir.resolve()
    if not root.is_dir():
        raise LoadTestConfigurationError(f"result directory does not exist: {root}")
    excluded = {"checksums.sha256", "manifest.json"}
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name not in excluded)
    inventory = [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in files
    ]
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "gpu-control-load-result-manifest.v1",
                "session_id": session_id,
                "created_at": datetime.now(UTC).isoformat(),
                "external_anchor_status": "PENDING_GIT_PUBLISH",
                "production_acceptance_eligible": False,
                "files": inventory,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "checksums.sha256").write_text(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in inventory),
        encoding="utf-8",
    )


def percentile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * ratio) + 0.999999) - 1))
    return round(float(ordered[index]), 3)


def summarize_records(
    records: Sequence[Mapping[str, Any]], elapsed_seconds: float
) -> dict[str, Any]:
    completed = [record for record in records if record.get("terminal_status")]
    latencies = [
        float(record["total_ms"]) for record in completed if record.get("total_ms") is not None
    ]
    queue_latencies = [
        float(record["queue_ms"]) for record in completed if record.get("queue_ms") is not None
    ]
    errors = Counter(
        str(record.get("error_code") or record.get("terminal_status"))
        for record in completed
        if record.get("terminal_status") not in LOAD_SUCCESS_STATUSES
    )
    return {
        "created": len(records),
        "completed": len(completed),
        "throughput_completed_per_second": round(len(completed) / max(elapsed_seconds, 0.001), 6),
        "terminal_statuses": dict(Counter(str(record["terminal_status"]) for record in completed)),
        "apis": dict(Counter(str(record.get("api")) for record in records)),
        "nodes": dict(
            Counter(str(record["node_id"]) for record in completed if record.get("node_id"))
        ),
        "workers": dict(
            Counter(str(record["worker_id"]) for record in completed if record.get("worker_id"))
        ),
        "retries": sum(int(record.get("retries", 0)) for record in records),
        "recoveries": sum(bool(record.get("recovered")) for record in records),
        "errors": dict(errors),
        "total_ms": {
            "p50": percentile(latencies, 0.50),
            "p90": percentile(latencies, 0.90),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
        "queue_ms": {
            "p50": percentile(queue_latencies, 0.50),
            "p90": percentile(queue_latencies, 0.90),
            "p95": percentile(queue_latencies, 0.95),
            "p99": percentile(queue_latencies, 0.99),
        },
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": round(max(values), 3) if values else None,
    }


def evaluate_load_thresholds(
    summary: Mapping[str, Any], thresholds: Mapping[str, float]
) -> dict[str, Any]:
    """Evaluate route metrics while separating async submit from sync E2E."""

    raw_http = summary.get("http")
    http: Mapping[str, Any] = raw_http if isinstance(raw_http, Mapping) else {}
    raw_total = http.get("total")
    total: Mapping[str, Any] = raw_total if isinstance(raw_total, Mapping) else {}
    raw_entries = http.get("entries")
    entries: Mapping[str, Any] = raw_entries if isinstance(raw_entries, Mapping) else {}

    def maximum_p95(operation: str) -> float | None:
        values = [
            float(item["p95_ms"])
            for name, item in entries.items()
            if isinstance(item, Mapping)
            and str(name).endswith(f":{operation}")
            and _number(item.get("p95_ms")) is not None
        ]
        return max(values) if values else None

    raw_queue = summary.get("queue_ms")
    queue: Mapping[str, Any] = raw_queue if isinstance(raw_queue, Mapping) else {}
    created = int(summary.get("created") or 0)
    retries = int(summary.get("http_retry_attempts") or 0)
    failure_ratio = _number(total.get("failure_rate"))
    queue_p95 = _number(queue.get("p95"))
    observed: dict[str, float | None] = {
        "http_failure_rate_percent": (failure_ratio * 100 if failure_ratio is not None else None),
        "submit_p95_ms": maximum_p95("submit"),
        "sync_e2e_p95_ms": maximum_p95("sync-e2e"),
        "poll_p95_ms": maximum_p95("poll"),
        "artifact_p95_ms": maximum_p95("artifact-download"),
        "queue_p95_ms": queue_p95,
        "retry_rate_percent": (retries / created * 100) if created else None,
    }
    checks: dict[str, Any] = {}
    for name, raw_limit in thresholds.items():
        if name not in METRIC_THRESHOLD_NAMES:
            raise LoadTestConfigurationError(f"unsupported metric threshold: {name}")
        limit = float(raw_limit)
        value = observed[name]
        passed = value is not None and value <= limit
        checks[name] = {
            "observed": round(value, 6) if value is not None else None,
            "maximum": limit,
            "passed": passed,
            "reason": (None if passed else "missing measurement" if value is None else "exceeded"),
        }
    return {
        "passed": bool(checks) and all(item["passed"] for item in checks.values()),
        "checks": checks,
        "observed": {
            key: round(value, 6) if value is not None else None for key, value in observed.items()
        },
        "route_classification": {
            "async_submit_api_names": [
                name for name in API_NAMES if name not in SYNC_FINAL_API_NAMES
            ],
            "sync_end_to_end_api_names": sorted(SYNC_FINAL_API_NAMES),
            "sync_end_to_end_operation": "sync-e2e",
        },
    }


def summarize_telemetry(
    samples: Sequence[Mapping[str, Any]],
    *,
    expected_gpu_ids: Sequence[str] = (),
    expected_worker_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Summarize sanitized resource samples without inventing unavailable CPU data."""

    gpu_series: dict[str, dict[str, list[float]]] = {}
    worker_series: dict[str, dict[str, list[float]]] = {}
    cluster_series: dict[str, list[float]] = {
        "queue_depth": [],
        "gpu_used_slots": [],
        "gpu_available_slots": [],
        "asset_used_slots": [],
        "asset_available_slots": [],
        "asset_queue_depth": [],
    }

    valid_samples = [sample for sample in samples if sample.get("valid") is True]
    for sample in valid_samples:
        raw_nodes = sample.get("gpu_nodes")
        nodes = raw_nodes if isinstance(raw_nodes, list) else []
        for raw_node in nodes:
            if not isinstance(raw_node, dict) or not raw_node.get("id"):
                continue
            node_id = str(raw_node["id"])
            series = gpu_series.setdefault(
                node_id,
                {
                    "gpu_util_percent": [],
                    "free_vram_mb": [],
                    "current_jobs": [],
                    "slot_occupancy_percent": [],
                },
            )
            for key in ("gpu_util_percent", "free_vram_mb", "current_jobs"):
                value = _number(raw_node.get(key))
                if value is not None:
                    series[key].append(value)
            current_jobs = _number(raw_node.get("current_jobs"))
            max_concurrency = _number(raw_node.get("max_concurrency"))
            if current_jobs is not None and max_concurrency is not None and max_concurrency > 0:
                series["slot_occupancy_percent"].append(
                    min(100.0, max(0.0, current_jobs / max_concurrency * 100))
                )

        raw_workers = sample.get("asset_workers")
        workers = raw_workers if isinstance(raw_workers, list) else []
        for raw_worker in workers:
            if not isinstance(raw_worker, dict) or not raw_worker.get("id"):
                continue
            worker_id = str(raw_worker["id"])
            series = worker_series.setdefault(
                worker_id,
                {"current_jobs": [], "slot_occupancy_percent": []},
            )
            current_jobs = _number(raw_worker.get("current_jobs"))
            max_concurrency = _number(raw_worker.get("max_concurrency"))
            if current_jobs is not None:
                series["current_jobs"].append(current_jobs)
            if current_jobs is not None and max_concurrency is not None and max_concurrency > 0:
                series["slot_occupancy_percent"].append(
                    min(100.0, max(0.0, current_jobs / max_concurrency * 100))
                )

        raw_scheduler = sample.get("scheduler")
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        raw_gpu_cluster = scheduler.get("cluster")
        gpu_cluster = raw_gpu_cluster if isinstance(raw_gpu_cluster, dict) else {}
        raw_asset_capacity = sample.get("asset_capacity")
        asset_capacity = raw_asset_capacity if isinstance(raw_asset_capacity, dict) else {}
        cluster_values = {
            "queue_depth": scheduler.get("queue_depth"),
            "gpu_used_slots": gpu_cluster.get("used_slots"),
            "gpu_available_slots": gpu_cluster.get("available_slots"),
            "asset_used_slots": asset_capacity.get("used_slots"),
            "asset_available_slots": asset_capacity.get("available_slots"),
            "asset_queue_depth": asset_capacity.get("queued_jobs"),
        }
        for key, raw_value in cluster_values.items():
            value = _number(raw_value)
            if value is not None:
                cluster_series[key].append(value)

    gpu_summary: dict[str, Any] = {}
    for node_id, series in gpu_series.items():
        utilization = series["gpu_util_percent"]
        free_vram = series["free_vram_mb"]
        gpu_summary[node_id] = {
            "samples": len(utilization),
            "gpu_util_percent": _distribution(utilization),
            "saturation_ge_90_percent_ratio": (
                round(sum(value >= 90 for value in utilization) / len(utilization), 6)
                if utilization
                else None
            ),
            "free_vram_mb": {
                "minimum": round(min(free_vram), 3) if free_vram else None,
                "p50": percentile(free_vram, 0.50),
            },
            "current_jobs": _distribution(series["current_jobs"]),
            "slot_occupancy_percent": _distribution(series["slot_occupancy_percent"]),
        }

    worker_summary: dict[str, Any] = {}
    for worker_id, series in worker_series.items():
        worker_summary[worker_id] = {
            "samples": len(series["slot_occupancy_percent"]),
            "current_jobs": _distribution(series["current_jobs"]),
            "slot_occupancy_percent": _distribution(series["slot_occupancy_percent"]),
            "cpu_util_percent": None,
            "cpu_util_note": "backend does not expose CPU%; slot occupancy is authoritative",
        }

    def peak(key: str) -> float | None:
        values = cluster_series[key]
        return round(max(values), 3) if values else None

    def minimum(key: str) -> float | None:
        values = cluster_series[key]
        return round(min(values), 3) if values else None

    return {
        "schema_version": "gpu-control-six-api-telemetry-summary.v1",
        "sample_count": len(samples),
        "valid_sample_count": len(valid_samples),
        "invalid_sample_count": len(samples) - len(valid_samples),
        "gpu_nodes": gpu_summary,
        "asset_workers": worker_summary,
        "cluster": {
            "queue_depth_peak": peak("queue_depth"),
            "gpu_used_slots_peak": peak("gpu_used_slots"),
            "gpu_available_slots_minimum": minimum("gpu_available_slots"),
            "asset_used_slots_peak": peak("asset_used_slots"),
            "asset_available_slots_minimum": minimum("asset_available_slots"),
            "asset_queue_depth_peak": peak("asset_queue_depth"),
        },
        "expected_resources": {
            "gpu_node_ids": list(expected_gpu_ids),
            "asset_worker_ids": list(expected_worker_ids),
            "all_gpu_samples_present": all(
                gpu_summary.get(node_id, {}).get("samples") == len(valid_samples)
                for node_id in expected_gpu_ids
            ),
            "all_worker_samples_present": all(
                worker_summary.get(worker_id, {}).get("samples") == len(valid_samples)
                for worker_id in expected_worker_ids
            ),
            "all_gpus_reached_90_percent": all(
                (gpu_summary.get(node_id, {}).get("saturation_ge_90_percent_ratio") or 0) > 0
                for node_id in expected_gpu_ids
            ),
        },
        "cpu_utilization_policy": (
            "NOT_EXPOSED: report Asset Worker slot occupancy; never infer CPU percent"
        ),
    }


def evaluate_telemetry_evidence(
    samples: Sequence[Mapping[str, Any]],
    *,
    expected_resources: Mapping[str, Any],
    sampling_interval_seconds: float,
) -> dict[str, Any]:
    """Validate the observed sample window and its explicit stop-time tail.

    A run can start or stop between interval boundaries, so deriving a sample
    count from total Locust elapsed time creates false gaps. Instead, require a
    contiguous sequence, bounded gaps inside the actual telemetry window, and
    one explicit final sample captured after the sampler is stopped.
    """

    if sampling_interval_seconds <= 0:
        raise LoadTestConfigurationError("telemetry sampling interval must be positive")
    invalid_count = sum(sample.get("valid") is not True for sample in samples)
    sequences: list[int] = []
    elapsed_values: list[float] = []
    structure_valid = True
    for sample in samples:
        raw_sequence = sample.get("sequence")
        raw_elapsed = sample.get("actual_elapsed_ms")
        if (
            isinstance(raw_sequence, bool)
            or not isinstance(raw_sequence, int)
            or raw_sequence < 1
            or (elapsed := _number(raw_elapsed)) is None
            or elapsed < 0
        ):
            structure_valid = False
            continue
        sequences.append(raw_sequence)
        elapsed_values.append(elapsed)

    sequence_contiguous = bool(sequences) and sequences == list(range(1, len(samples) + 1))
    elapsed_monotonic = all(
        current >= previous
        for previous, current in zip(elapsed_values, elapsed_values[1:], strict=False)
    )
    gaps = [
        current - previous
        for previous, current in zip(elapsed_values, elapsed_values[1:], strict=False)
    ]
    maximum_gap_ms = max(gaps) if gaps else None
    maximum_allowed_gap_ms = sampling_interval_seconds * 1500
    first_sample_on_time = bool(elapsed_values) and elapsed_values[0] <= (
        sampling_interval_seconds * 1000
    )
    explicit_final_sample = bool(samples) and samples[-1].get("final_sample") is True
    resource_complete = (
        expected_resources.get("all_gpu_samples_present") is True
        and expected_resources.get("all_worker_samples_present") is True
    )
    passed = (
        len(samples) >= 2
        and invalid_count == 0
        and structure_valid
        and sequence_contiguous
        and elapsed_monotonic
        and first_sample_on_time
        and (maximum_gap_ms is None or maximum_gap_ms <= maximum_allowed_gap_ms)
        and explicit_final_sample
        and resource_complete
    )
    return {
        "passed": passed,
        "policy": "observed_window_with_explicit_final_sample",
        "sample_count": len(samples),
        "minimum_required_samples": 2,
        "invalid_sample_count": invalid_count,
        "sequence_contiguous": sequence_contiguous,
        "elapsed_monotonic": elapsed_monotonic,
        "first_sample_on_time": first_sample_on_time,
        "explicit_final_sample": explicit_final_sample,
        "resource_samples_complete": resource_complete,
        "observed_window_seconds": (
            round((elapsed_values[-1] - elapsed_values[0]) / 1000, 3)
            if len(elapsed_values) >= 2
            else 0.0
        ),
        "maximum_gap_ms": round(maximum_gap_ms, 3) if maximum_gap_ms is not None else None,
        "maximum_allowed_gap_ms": round(maximum_allowed_gap_ms, 3),
    }


def build_plan(
    runtime: RuntimeSettings,
    scenario: LoadScenario,
    fixtures: FixtureManifest,
    *,
    repository_root: Path,
    verified_release_evidence: Mapping[str, Any] | None = None,
    verified_live_deployment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    backup_evidence: dict[str, Any] | None = None
    backup_error: str | None = None
    if runtime.is_production_target() and runtime.backup_dir is not None:
        window_start = _parse_window_timestamp(runtime.window_start)
        if window_start is not None:
            try:
                backup_evidence = validate_production_backup(
                    runtime.backup_dir,
                    approved_window_start=window_start,
                    max_age_hours=scenario.max_backup_age_hours,
                )
            except LoadTestConfigurationError as exc:
                backup_error = str(exc)
                backup_evidence = {"status": "INVALID", "reason": str(exc)}
    blockers = runtime.execution_blockers(
        scenario,
        fixtures,
        repository_root=repository_root,
        validate_backup=False,
        verified_release_evidence=verified_release_evidence,
        verified_live_deployment=verified_live_deployment,
    )
    if backup_error is not None and backup_error not in blockers:
        blockers.append(backup_error)
    fixture_inventory: dict[str, dict[str, Any]] = {}
    for api_name, entry in fixtures.entries.items():
        api_files: dict[str, Any] = {}
        for key, value in entry.items():
            values = value if isinstance(value, tuple) else (value,)
            api_files[key] = [
                {
                    "path": str(path),
                    "exists": path.is_file(),
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                    "sha256": file_sha256(path) if path.is_file() else None,
                }
                for path in values
            ]
        fixture_inventory[api_name] = api_files
    return {
        "schema_version": "gpu-control-six-api-load-plan.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "EXECUTION_ELIGIBLE" if not blockers else "PLAN_ONLY",
        "session_id": runtime.session_id,
        "target": runtime.target,
        "environment": runtime.environment,
        "authorization": {
            "production_target": runtime.is_production_target(),
            "allow_load_test": runtime.allow_load_test,
            "allow_production_load_test": runtime.allow_production_load_test,
            "change_id": runtime.change_id or None,
            "window_start": runtime.window_start or None,
            "window_end": runtime.window_end or None,
            "backup_dir": str(runtime.backup_dir) if runtime.backup_dir else None,
            "backup_evidence": backup_evidence,
            "target_allowlist": list(runtime.target_allowlist),
            "required_window_seconds": (
                scenario.total_duration_seconds + runtime.required_production_window_seconds
                if runtime.is_production_target()
                else None
            ),
            "teardown_reserve_seconds": (
                PRODUCTION_TEARDOWN_RESERVE_SECONDS if runtime.is_production_target() else None
            ),
            "preflight_evidence_reserve_seconds": (
                PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS
                if runtime.is_production_target()
                else None
            ),
        },
        "target_release_identity": runtime.target_release_identity,
        "release_evidence_verification": (
            copy_load_evidence_json(verified_release_evidence)
            if verified_release_evidence is not None
            else {
                "verified": False,
                "reason": "plan-only mode does not contact origin/main",
            }
        ),
        "live_deployment_verification": (
            copy_load_evidence_json(verified_live_deployment)
            if verified_live_deployment is not None
            else {
                "verified": False,
                "reason": "plan-only mode does not inspect production containers",
            }
        ),
        "expected_confirmation_token": runtime.expected_confirmation_token,
        "execution_blockers": blockers,
        "secret_inventory": {
            "api_key_count": len(runtime.api_keys),
            "unique_api_key_count": len(set(runtime.api_keys)),
            "tenant_id_count": len(runtime.tenant_ids),
            "unique_tenant_id_count": len(set(runtime.tenant_ids)),
            "admin_bearer_configured": bool(runtime.admin_bearer_token),
            "secret_values_recorded": False,
        },
        "scenario": {
            "source": str(scenario.source),
            "source_sha256": file_sha256(scenario.source),
            "weights": scenario.weights,
            "normalized_weights": scenario.normalized_weights(),
            "weights_confirmed": scenario.weights_confirmed,
            "resource_mix": scenario.resource_mix(),
            "maximum_users": scenario.maximum_users,
            "total_duration_seconds": scenario.total_duration_seconds,
            "max_backup_age_hours": scenario.max_backup_age_hours,
            "lifecycle_mode": scenario.lifecycle_mode,
            "stages": [stage.__dict__ for stage in scenario.stages],
            "thresholds": scenario.thresholds,
            "approved_workflows": scenario.approved_workflows,
        },
        "contracts": API_CONTRACTS,
        "fixtures": {
            "manifest_source": str(fixtures.source),
            "manifest_sha256": file_sha256(fixtures.source),
            "apis": fixture_inventory,
        },
    }
