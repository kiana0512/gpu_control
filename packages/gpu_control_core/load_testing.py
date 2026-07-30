"""Safety and planning primitives for the six-API mixed load harness.

This module is deliberately network-free.  The Locust entrypoint imports it to
validate all execution gates before Locust can create an HTTP client, while the
plan command and unit tests use the same validation logic without sending any
traffic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

API_NAMES = (
    "imageclip_batch",
    "modelview_roughness",
    "uv_process",
    "retopology_audit",
    "retopology_process",
    "substance_bake",
)
LOAD_SUCCESS_STATUSES = frozenset({"SUCCEEDED", "WAITING_REVIEW"})
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
METRIC_THRESHOLD_NAMES = frozenset(
    {
        "http_failure_rate_percent",
        "submit_p95_ms",
        "poll_p95_ms",
        "artifact_p95_ms",
        "queue_p95_ms",
        "retry_rate_percent",
    }
)

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
CHANGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
DEFAULT_PRODUCTION_HOSTS = frozenset({"10.3.34.11"})
NON_PRODUCTION_ENVIRONMENTS = frozenset({"test", "staging", "development"})
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
    capacities: Sequence[Mapping[str, Any]], *, expected_count: int
) -> list[dict[str, Any]]:
    """Validate every rotating load identity without retaining its secret."""

    if expected_count < 1 or len(capacities) != expected_count:
        raise LoadTestConfigurationError(
            "every LOAD_TEST_API_KEYS identity must have one capacity preflight"
        )
    checks: list[dict[str, Any]] = []
    for index, capacity in enumerate(capacities):
        client = capacity.get("client")
        if not isinstance(client, Mapping) or client.get("kind") != "test":
            raise LoadTestConfigurationError(
                f"load API key index {index} must belong to client_kind=test"
            )
        if capacity.get("accepting_batches") is not True:
            raise LoadTestConfigurationError(
                f"load API key index {index} is not accepting GPU batches"
            )
        checks.append(
            {
                "api_key_index": index,
                "client_kind": "test",
                "accepting_batches": True,
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
) -> dict[str, Any]:
    """Return minimal evidence for active work outside this load session.

    Asset admin rows do not expose ``client_kind``, so their ``client_id`` is
    classified against the exact tenant allowlist bound to the approved load
    plan. GPU rows prefer their authoritative ``client_kind`` and use the
    tenant allowlist as a fallback and second session boundary. Missing or
    unknown ownership fails closed. Business payloads are never returned.
    """

    approved_tenants = {str(value) for value in test_tenant_ids if str(value)}
    if not approved_tenants or len(approved_tenants) != len(test_tenant_ids):
        raise LoadTestConfigurationError(
            "production watchdog requires unique LOAD_TEST_TENANT_IDS"
        )
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
            owner_is_current_session = owner in approved_tenants
            if plane == "gpu" and raw_client_kind is not None:
                belongs_to_session = client_kind == "test" and owner_is_current_session
            else:
                belongs_to_session = owner_is_current_session
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


def evaluate_load_lifecycle(
    records: Sequence[Mapping[str, Any]],
    teardown: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fail closed on unfinished, unsuccessful, or artifact-incomplete work."""

    incomplete = [
        str(record.get("id")) for record in records if not record.get("terminal_status")
    ]
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
    poll_timeouts = [
        str(record.get("id")) for record in records if record.get("poll_timed_out") is True
    ]
    teardown_failed = [
        str(outcome.get("task_id"))
        for outcome in teardown
        if outcome.get("cancelled") is not True
    ]
    passed = bool(records) and not any(
        (
            incomplete,
            unsuccessful,
            missing_artifacts,
            artifact_contract_failures,
            poll_timeouts,
            teardown,
        )
    )
    return {
        "passed": passed,
        "registered": len(records),
        "successful": sum(
            record.get("terminal_status") in LOAD_SUCCESS_STATUSES for record in records
        ),
        "incomplete_task_ids": incomplete,
        "unsuccessful_tasks": unsuccessful,
        "missing_artifact_task_ids": missing_artifacts,
        "artifact_contract_failure_task_ids": artifact_contract_failures,
        "poll_timeout_task_ids": poll_timeouts,
        "teardown_attempted": len(teardown),
        "teardown_failed_task_ids": teardown_failed,
        "policy": "all registered tasks must end successfully with a verified artifact; teardown means the run is incomplete",
    }


@dataclass(frozen=True)
class LoadStage:
    users: int
    duration_seconds: int
    spawn_rate: float


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
            self.weights[name]
            for name in API_NAMES
            if API_CONTRACTS[name]["resource"] == "CPU"
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

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> RuntimeSettings:
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
            allow_production_load_test=source.get(
                "ALLOW_PRODUCTION_LOAD_TEST", ""
            ).strip().lower()
            == "true",
            target_allowlist=_split_nonempty(
                source.get("LOAD_TEST_TARGET_ALLOWLIST", "")
            ),
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
            admin_bearer_token=source.get(
                "LOAD_TEST_ADMIN_BEARER_TOKEN", ""
            ).strip(),
            ca_file=Path(ca_value).expanduser() if ca_value else None,
            result_dir=Path(result_value).expanduser() if result_value else None,
        )

    @property
    def expected_confirmation_token(self) -> str:
        tenant_binding = hashlib.sha256(",".join(self.tenant_ids).encode()).hexdigest()
        if self.is_production_target():
            material = (
                "gpu-control-six-api:production:"
                f"{self.change_id}:{self.window_start}:{self.window_end}:"
                f"{self.backup_dir or ''}:"
                f"{self.session_id}:{self.target}:{tenant_binding}:execute"
            ).encode()
        else:
            material = (
                "gpu-control-six-api:nonproduction:"
                f"{self.session_id}:{self.target}:{tenant_binding}:execute"
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
            str(urlsplit(value).hostname or value).lower()
            for value in self.production_targets
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
    ) -> list[str]:
        blockers: list[str] = []
        production = self.is_production_target()
        if not self.allow_load_test:
            blockers.append("ALLOW_LOAD_TEST must be exactly true")
        if production:
            if self.environment != "production":
                blockers.append(
                    "known production targets require LOAD_TEST_ENVIRONMENT=production"
                )
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
                planned_end = current.timestamp() + scenario.total_duration_seconds
                if planned_end > end.timestamp():
                    blockers.append("planned load stages extend beyond the production window")
            if (
                scenario.preflight.get("maximum_preexisting_gpu_jobs") != 0
                or scenario.preflight.get("maximum_preexisting_asset_jobs") != 0
            ):
                blockers.append(
                    "production scenarios must require zero pre-existing GPU and asset jobs"
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
    ) -> None:
        blockers = self.execution_blockers(
            scenario, fixtures, repository_root=repository_root, now=now
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
        raise LoadTestConfigurationError("load-test target cannot contain credentials/query/fragment")
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

    raw_timeouts = _mapping(
        payload.get("operation_timeout_seconds"), "operation_timeout_seconds"
    )
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
            key: int(raw_preflight.get(key, default))
            for key, default in preflight_defaults.items()
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
    if set(thresholds) != METRIC_THRESHOLD_NAMES:
        missing_thresholds = sorted(METRIC_THRESHOLD_NAMES - set(thresholds))
        unknown_thresholds = sorted(set(thresholds) - METRIC_THRESHOLD_NAMES)
        raise LoadTestConfigurationError(
            "thresholds must contain every supported metric; "
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
    return LoadScenario(
        source=source,
        weights=weights,
        weights_confirmed=payload.get("weights_confirmed") is True,
        stages=tuple(stages),
        poll_interval_seconds=poll_interval_seconds,
        operation_timeout_seconds=operation_timeouts,
        max_retries=max_retries,
        max_backup_age_hours=max_backup_age_hours,
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
            raise LoadTestConfigurationError(
                f"fixture {api_name} is missing {sorted(missing)}"
            )
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
                    raise LoadTestConfigurationError(
                        f"ImageClip size mismatch for {relative_path}"
                    )
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

    reference_values = fixtures.paths_for("retopology_process").get(
        "reference_images", ()
    )
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
        raise LoadTestConfigurationError(
            f"cannot read production {path.name}"
        ) from exc
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
        name
        for name in NONEMPTY_FULL_BACKUP_PAYLOADS
        if entry_by_name[name].stat().st_size < 1
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
        raise LoadTestConfigurationError(
            "BACKUP_MANIFEST GIT_HEAD does not match git-head.txt"
        )
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
        entry.name
        for entry in entries
        if entry.name not in {"SHA256SUMS", "BACKUP_COMPLETE"}
    }
    if set(listed) != expected_files:
        raise LoadTestConfigurationError(
            "production SHA256SUMS does not exactly cover backup payloads"
        )
    for name, digest in listed.items():
        if file_sha256(root / name) != digest:
            raise LoadTestConfigurationError(f"production backup checksum failed for {name}")

    latest_mtime = max(
        datetime.fromtimestamp(entry.stat().st_mtime, UTC) for entry in entries
    )
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
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and path.name not in excluded
    )
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


def summarize_records(records: Sequence[Mapping[str, Any]], elapsed_seconds: float) -> dict[str, Any]:
    completed = [record for record in records if record.get("terminal_status")]
    latencies = [
        float(record["total_ms"])
        for record in completed
        if record.get("total_ms") is not None
    ]
    queue_latencies = [
        float(record["queue_ms"])
        for record in completed
        if record.get("queue_ms") is not None
    ]
    errors = Counter(
        str(record.get("error_code") or record.get("terminal_status"))
        for record in completed
        if record.get("terminal_status") not in LOAD_SUCCESS_STATUSES
    )
    return {
        "created": len(records),
        "completed": len(completed),
        "throughput_completed_per_second": round(
            len(completed) / max(elapsed_seconds, 0.001), 6
        ),
        "terminal_statuses": dict(
            Counter(str(record["terminal_status"]) for record in completed)
        ),
        "apis": dict(Counter(str(record.get("api")) for record in records)),
        "nodes": dict(
            Counter(str(record["node_id"]) for record in completed if record.get("node_id"))
        ),
        "workers": dict(
            Counter(
                str(record["worker_id"])
                for record in completed
                if record.get("worker_id")
            )
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
            if (
                current_jobs is not None
                and max_concurrency is not None
                and max_concurrency > 0
            ):
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
            if (
                current_jobs is not None
                and max_concurrency is not None
                and max_concurrency > 0
            ):
                series["slot_occupancy_percent"].append(
                    min(100.0, max(0.0, current_jobs / max_concurrency * 100))
                )

        raw_scheduler = sample.get("scheduler")
        scheduler = raw_scheduler if isinstance(raw_scheduler, dict) else {}
        raw_gpu_cluster = scheduler.get("cluster")
        gpu_cluster = raw_gpu_cluster if isinstance(raw_gpu_cluster, dict) else {}
        raw_asset_capacity = sample.get("asset_capacity")
        asset_capacity = (
            raw_asset_capacity if isinstance(raw_asset_capacity, dict) else {}
        )
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
            "slot_occupancy_percent": _distribution(
                series["slot_occupancy_percent"]
            ),
        }

    worker_summary: dict[str, Any] = {}
    for worker_id, series in worker_series.items():
        worker_summary[worker_id] = {
            "samples": len(series["slot_occupancy_percent"]),
            "current_jobs": _distribution(series["current_jobs"]),
            "slot_occupancy_percent": _distribution(
                series["slot_occupancy_percent"]
            ),
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
                (
                    gpu_summary.get(node_id, {}).get(
                        "saturation_ge_90_percent_ratio"
                    )
                    or 0
                )
                > 0
                for node_id in expected_gpu_ids
            ),
        },
        "cpu_utilization_policy": (
            "NOT_EXPOSED: report Asset Worker slot occupancy; never infer CPU percent"
        ),
    }


def build_plan(
    runtime: RuntimeSettings,
    scenario: LoadScenario,
    fixtures: FixtureManifest,
    *,
    repository_root: Path,
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
        },
        "expected_confirmation_token": runtime.expected_confirmation_token,
        "execution_blockers": blockers,
        "secret_inventory": {
            "api_key_count": len(runtime.api_keys),
            "tenant_id_count": len(runtime.tenant_ids),
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
