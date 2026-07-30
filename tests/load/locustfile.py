"""Guarded six-business-API Locust workload.

Importing this file for a Locust run performs all offline safety checks.  It
cannot create an HTTP client unless the explicit environment gates, target
allowlist, environment-specific confirmation token, approved scenario, and
external fixtures all validate. Production has an additional change-window
gate and zero-work preflight. The read-only HTTP preflight runs before any
virtual user is spawned.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gevent
import httpx
from locust import HttpUser, LoadTestShape, between, events, task

from packages.gpu_control_core.load_testing import (
    API_CONTRACTS,
    API_NAMES,
    LOAD_SUCCESS_STATUSES,
    LoadShapeStopSignal,
    LoadTestConfigurationError,
    RuntimeSettings,
    approved_load_tls_verify,
    configure_locust_client_tls,
    discover_scoped_teardown_tasks,
    evaluate_load_lifecycle,
    evaluate_load_thresholds,
    evaluate_telemetry_evidence,
    execute_bounded_teardown_cancel,
    file_sha256,
    identify_foreign_active_work,
    load_fixture_manifest,
    load_queue_start,
    load_response_is_retryable,
    load_scenario,
    normalize_scheduler_capacity_v1,
    select_load_shape_stage,
    summarize_records,
    summarize_telemetry,
    validate_asset_worker_roles,
    validate_test_client_capacities,
    write_result_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_FILE = Path(
    os.environ.get(
        "LOAD_TEST_SCENARIO_FILE",
        REPOSITORY_ROOT / "tests/load/scenarios/six_api_120.example.yaml",
    )
)
FIXTURE_FILE = Path(
    os.environ.get(
        "LOAD_TEST_FIXTURE_MANIFEST",
        REPOSITORY_ROOT / "tests/load/fixtures/six_api.example.yaml",
    )
)
RUNTIME = RuntimeSettings.from_environment()
SCENARIO = load_scenario(SCENARIO_FILE)
FIXTURES = load_fixture_manifest(FIXTURE_FILE)
RUNTIME.assert_execution_allowed(
    SCENARIO,
    FIXTURES,
    repository_root=REPOSITORY_ROOT,
)
if RUNTIME.result_dir is None or not RUNTIME.result_dir.is_dir():
    raise LoadTestConfigurationError(
        "LOAD_TEST_RESULT_DIR must already be created by scripts/run_six_api_load.py"
    )
if not (RUNTIME.result_dir / "plan.json").is_file():
    raise LoadTestConfigurationError("result directory has no immutable plan.json")

RESULT_DIR = RUNTIME.result_dir.resolve()


def verify_plan_binding() -> None:
    try:
        plan = json.loads((RESULT_DIR / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LoadTestConfigurationError(f"cannot read immutable plan.json: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("mode") != "EXECUTION_ELIGIBLE":
        raise LoadTestConfigurationError("plan.json was not execution eligible")
    expected = {
        "session_id": RUNTIME.session_id,
        "target": RUNTIME.target,
        "environment": RUNTIME.environment,
        "expected_confirmation_token": RUNTIME.expected_confirmation_token,
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        raise LoadTestConfigurationError("runtime is not bound to plan.json")
    scenario_plan = plan.get("scenario")
    fixture_plan = plan.get("fixtures")
    if not isinstance(scenario_plan, dict) or not isinstance(fixture_plan, dict):
        raise LoadTestConfigurationError("plan.json omitted configuration hashes")
    if scenario_plan.get("source_sha256") != file_sha256(SCENARIO.source):
        raise LoadTestConfigurationError("scenario changed after plan generation")
    if fixture_plan.get("manifest_sha256") != file_sha256(FIXTURES.source):
        raise LoadTestConfigurationError("fixture manifest changed after plan generation")
    planned_apis = fixture_plan.get("apis")
    if not isinstance(planned_apis, dict):
        raise LoadTestConfigurationError("plan.json omitted fixture file hashes")
    for api_name, entry in FIXTURES.entries.items():
        planned_entry = planned_apis.get(api_name)
        if not isinstance(planned_entry, dict):
            raise LoadTestConfigurationError(f"plan.json omitted fixtures for {api_name}")
        for key, raw_paths in entry.items():
            paths = raw_paths if isinstance(raw_paths, tuple) else (raw_paths,)
            actual = [
                {
                    "path": str(path),
                    "exists": path.is_file(),
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                    "sha256": file_sha256(path) if path.is_file() else None,
                }
                for path in paths
            ]
            if planned_entry.get(key) != actual:
                raise LoadTestConfigurationError(
                    f"fixture {api_name}.{key} changed after plan generation"
                )
    if scenario_plan.get("approved_workflows") != SCENARIO.approved_workflows:
        raise LoadTestConfigurationError("approved workflow identities changed after planning")


verify_plan_binding()

TERMINAL_STATUSES = {
    "SUCCEEDED",
    "WAITING_REVIEW",
    "REVIEW_REJECTED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
}
ACTIVE_STATUSES = {
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
ACTIVE_STATUS_QUERY_ORDER = (
    "RECEIVED",
    "VALIDATING",
    "RETRY_WAIT",
    "QUEUED",
    "CLAIMED",
    "UPLOADING",
    "SUBMITTED",
    "RUNNING",
    "DOWNLOADING",
    "ASSEMBLING",
    "CANCELLING",
)
TELEMETRY_INTERVAL_SECONDS = 5.0
TEARDOWN_CANCEL_MAX_ATTEMPTS = 3
TEARDOWN_CANCEL_INITIAL_BACKOFF_SECONDS = 0.25
TEARDOWN_CANCEL_MAXIMUM_BACKOFF_SECONDS = 1.0
TEARDOWN_CANCEL_THROTTLE_SECONDS = 0.1
TEARDOWN_CANCEL_TIMEOUT_SECONDS = 5.0
TEARDOWN_SETTLE_TIMEOUT_SECONDS = 300
TEARDOWN_SETTLE_POLL_INTERVAL_SECONDS = 1.0
ADMIN_STATUS_QUERY_MAX_ATTEMPTS = 3
ADMIN_STATUS_QUERY_INITIAL_BACKOFF_SECONDS = 0.25
ADMIN_STATUS_QUERY_MAXIMUM_BACKOFF_SECONDS = 1.0
# The administrator policy defaults to 5 requests/second with a burst of 10.
# A complete active-state audit performs eleven scoped reads, so pace those
# reads instead of weakening the production rate limit for a load test.
ADMIN_STATUS_QUERY_THROTTLE_SECONDS = 0.25

_operation_counter = itertools.count(1)
_user_counter = itertools.count(0)
_telemetry_greenlet: Any | None = None
_telemetry_stop = False
_telemetry_started_monotonic: float | None = None
_telemetry_sequence = 0
_telemetry_final_sample_written = False
_production_watchdog_triggered = False
_shape_stop_signal = LoadShapeStopSignal()
_expected_gpu_node_ids: tuple[str, ...] = ()
_expected_asset_worker_ids: tuple[str, ...] = ()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_ms(start: object, end: object) -> int | None:
    left = parse_timestamp(start)
    right = parse_timestamp(end)
    if left is None or right is None:
        return None
    return max(0, int((right - left).total_seconds() * 1000))


def fixture_path(api_name: str, key: str) -> Path:
    value = FIXTURES.paths_for(api_name)[key]
    if not isinstance(value, Path):
        raise RuntimeError(f"fixture {api_name}.{key} is not one path")
    return value


def fixture_json(api_name: str, key: str) -> dict[str, Any]:
    payload = json.loads(fixture_path(api_name, key).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"fixture {api_name}.{key} must be a JSON object")
    return payload


class SessionRegistry:
    """Session-scoped evidence and teardown registry; never stores credentials."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.events_path = output / "events.jsonl"
        self.started_monotonic = time.monotonic()
        self.started_at = utc_now()
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._admission = Counter()
        self._retry_events = Counter()

    def event(self, event: str, **fields: Any) -> None:
        payload = {
            "captured_at": utc_now(),
            "session_id": RUNTIME.session_id,
            "event": event,
            **fields,
        }
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def admission(self, api_name: str, status_code: int) -> None:
        with self._lock:
            self._admission[f"{api_name}:{status_code}"] += 1

    def retry(self, api_name: str, operation: str, status_code: int) -> None:
        with self._lock:
            self._retry_events[f"{api_name}:{operation}:{status_code}"] += 1
        self.event(
            "http.retry",
            api=api_name,
            operation=operation,
            status_code=status_code,
        )

    def register(
        self,
        identifier: str,
        *,
        api_name: str,
        kind: str,
        status_url: str,
        cancel_url: str,
        external_id: str | None,
        api_key_index: int,
        idempotency_key: str,
        request_id: str,
        traceparent: str,
        created_monotonic: float,
        submission_retries: int,
    ) -> None:
        record = {
            "id": identifier,
            "api": api_name,
            "resource": API_CONTRACTS[api_name]["resource"],
            "kind": kind,
            "status_url": status_url,
            "cancel_url": cancel_url,
            "external_id": external_id,
            "api_key_index": api_key_index,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
            "traceparent": traceparent,
            "created_at": utc_now(),
            "created_monotonic": created_monotonic,
            "last_status": "QUEUED",
            "terminal_status": None,
            "retries": submission_retries,
            "recovered": submission_retries > 0,
            "artifact_count": 0,
            "artifact_bytes": 0,
        }
        with self._lock:
            self._records[identifier] = record
        self.event(
            "task.created",
            api=api_name,
            task_id=identifier,
            kind=kind,
            request_id=request_id,
            traceparent=traceparent,
        )

    def update_status(self, identifier: str, payload: Mapping[str, Any]) -> None:
        status = str(payload.get("status") or "UNKNOWN")
        with self._lock:
            record = self._records.get(identifier)
            if record is None:
                return
            record["last_status"] = status
            record["node_id"] = payload.get("node_id") or record.get("node_id")
            record["worker_id"] = payload.get("worker_id") or record.get("worker_id")
            if isinstance(payload.get("node_distribution"), dict):
                record["node_distribution"] = dict(payload["node_distribution"])
            timing = payload.get("timing")
            if isinstance(timing, dict):
                record["queue_position"] = timing.get("queue_position")
                record["estimated_start_seconds"] = timing.get("estimated_start_seconds")
            elif payload.get("counts"):
                record["queue_position"] = None
            if status in TERMINAL_STATUSES:
                record["terminal_status"] = status
                record["finished_at"] = utc_now()
                record["total_ms"] = int(
                    (time.monotonic() - float(record["created_monotonic"])) * 1000
                )
                record["queue_ms"] = duration_ms(
                    load_queue_start(payload),
                    payload.get("started_at"),
                )
                error = payload.get("error")
                if isinstance(error, dict):
                    record["error_code"] = error.get("code")
                performance = payload.get("performance")
                if isinstance(performance, dict):
                    record["performance"] = performance
        self.event("task.status", api=record["api"], task_id=identifier, status=status)

    def add_retries(self, identifier: str, retries: int) -> None:
        if retries < 1:
            return
        with self._lock:
            record = self._records.get(identifier)
            if record:
                record["retries"] = int(record.get("retries", 0)) + retries
                record["recovered"] = True

    def add_artifact(self, identifier: str, size_bytes: int) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record:
                record["artifact_count"] = int(record.get("artifact_count", 0)) + 1
                record["artifact_bytes"] = int(record.get("artifact_bytes", 0)) + size_bytes

    def mark_poll_timeout(self, identifier: str) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record:
                record["poll_timed_out"] = True
                record["error_code"] = "CLIENT_POLL_TIMEOUT"

    def mark_artifact_contract_failure(self, identifier: str, reason: str) -> None:
        with self._lock:
            record = self._records.get(identifier)
            if record:
                record["artifact_contract_failed"] = True
                record["artifact_contract_failure_reason"] = reason

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._records.values()]

    def active(self) -> list[dict[str, Any]]:
        return [record for record in self.records() if not record.get("terminal_status")]

    def summary(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self.started_monotonic
        records = self.records()
        summary = summarize_records(records, elapsed)
        node_distribution: Counter[str] = Counter()
        for record in records:
            for node_id, count in (record.get("node_distribution") or {}).items():
                node_distribution[str(node_id)] += int(count)
        summary.update(
            {
                "schema_version": "gpu-control-six-api-load-result.v1",
                "session_id": RUNTIME.session_id,
                "target": RUNTIME.target,
                "environment": RUNTIME.environment,
                "started_at": self.started_at,
                "finished_at": utc_now(),
                "elapsed_seconds": round(elapsed, 3),
                "admission": dict(self._admission),
                "retry_events": dict(self._retry_events),
                "http_retry_attempts": sum(self._retry_events.values()),
                "batch_node_distribution": dict(node_distribution),
                "secrets_recorded": False,
            }
        )
        return summary


REGISTRY = SessionRegistry(RESULT_DIR)


def correlation(api_name: str, ordinal: int) -> tuple[str, str, str]:
    short_api = {
        "imageclip_batch": "icb",
        "modelview_roughness": "mvr",
        "uv_process": "uv",
        "retopology_audit": "rta",
        "retopology_process": "rtp",
        "substance_bake": "sb",
    }[api_name]
    token = f"{RUNTIME.session_id}:{short_api}:{ordinal:08d}"
    request_id = f"lt:{token}"[:64]
    idempotency_key = f"load:{token}"[:128]
    trace_id = hashlib.sha256(token.encode()).hexdigest()[:32]
    span_id = hashlib.sha256(f"{token}:span".encode()).hexdigest()[:16]
    traceparent = f"00-{trace_id}-{span_id}-01"
    return request_id, idempotency_key, traceparent


def request_headers(
    api_key: str,
    request_id: str,
    traceparent: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-API-Key": api_key,
        "X-Request-ID": request_id,
        "traceparent": traceparent,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def external_id(api_name: str, ordinal: int) -> str:
    return f"loadtest:{RUNTIME.session_id}:{api_name}:{ordinal:08d}"[:128]


def httpx_verify() -> bool | str:
    return approved_load_tls_verify(RUNTIME.ca_file)


def preflight_json(
    client: httpx.Client, path: str, headers: Mapping[str, str]
) -> dict[str, Any] | list[dict[str, Any]]:
    response = client.get(path, headers=dict(headers), timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict | list):
        raise LoadTestConfigurationError(f"preflight {path} returned invalid JSON")
    return payload


def admin_status_query_sender(
    client: httpx.Client,
    headers: Mapping[str, str],
    *,
    client_kind: str,
    status: str,
    response_holder: list[httpx.Response],
) -> Callable[[], int]:
    def send_status_query() -> int:
        response = client.get(
            f"/admin/jobs?client_kind={client_kind}&status={status}&limit=500",
            headers=dict(headers),
            timeout=30,
        )
        response_holder[:] = [response]
        return response.status_code

    return send_status_query


def active_gpu_admin_jobs(
    client: httpx.Client,
    headers: Mapping[str, str],
    *,
    client_kind: str,
    passes: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if client_kind not in {"all", "test"} or passes < 1 or passes > 2:
        raise LoadTestConfigurationError("invalid active GPU admin query scope")
    jobs_by_id: dict[str, dict[str, Any]] = {}
    rows_scanned = 0
    http_attempts = 0
    for _ in range(passes):
        for status in ACTIVE_STATUS_QUERY_ORDER:
            response_holder: list[httpx.Response] = []
            _, attempts = execute_bounded_teardown_cancel(
                admin_status_query_sender(
                    client,
                    headers,
                    client_kind=client_kind,
                    status=status,
                    response_holder=response_holder,
                ),
                gevent.sleep,
                max_attempts=ADMIN_STATUS_QUERY_MAX_ATTEMPTS,
                initial_backoff_seconds=ADMIN_STATUS_QUERY_INITIAL_BACKOFF_SECONDS,
                maximum_backoff_seconds=ADMIN_STATUS_QUERY_MAXIMUM_BACKOFF_SECONDS,
            )
            http_attempts += attempts
            response = response_holder[-1]
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise LoadTestConfigurationError(
                    "active GPU admin endpoint returned the wrong shape"
                )
            if len(rows) >= 500:
                raise LoadTestConfigurationError(f"active GPU {status} audit window is saturated")
            rows_scanned += len(rows)
            for row in rows:
                if not isinstance(row, dict):
                    raise LoadTestConfigurationError(
                        "active GPU admin endpoint returned a non-object row"
                    )
                identifier = str(row.get("job_id") or row.get("batch_id") or "")
                if not identifier or str(row.get("status") or "") != status:
                    raise LoadTestConfigurationError(
                        "active GPU admin endpoint returned an invalid scoped row"
                    )
                jobs_by_id[identifier] = row
            gevent.sleep(ADMIN_STATUS_QUERY_THROTTLE_SECONDS)
    return list(jobs_by_id.values()), {
        "rows_scanned": rows_scanned,
        "status_queries": len(ACTIVE_STATUS_QUERY_ORDER) * passes,
        "http_attempts": http_attempts,
        "transient_retries": http_attempts - len(ACTIVE_STATUS_QUERY_ORDER) * passes,
    }


def perform_preflight() -> dict[str, Any]:
    admin_headers = {"Authorization": f"Bearer {RUNTIME.admin_bearer_token}"}
    with httpx.Client(
        base_url=RUNTIME.target,
        verify=httpx_verify(),
        follow_redirects=False,
    ) as client:
        client_capacities = [
            normalize_scheduler_capacity_v1(
                preflight_json(
                    client,
                    "/api/v1/scheduler/capacity",
                    {"X-API-Key": api_key},
                )
            )
            for api_key in RUNTIME.api_keys
        ]
        api_headers = {"X-API-Key": RUNTIME.api_keys[0]}
        asset_capacity = preflight_json(client, "/api/v1/assets/capacity", api_headers)
        public_workflows = preflight_json(client, "/api/v1/workflows", api_headers)
        workflows = preflight_json(client, "/admin/workflows", admin_headers)
        nodes = preflight_json(client, "/admin/nodes", admin_headers)
        gpu_jobs, gpu_audit = active_gpu_admin_jobs(
            client,
            admin_headers,
            client_kind="all",
        )
        asset_overview = preflight_json(client, "/admin/asset-processing?limit=500", admin_headers)

    if not all(isinstance(item, dict) for item in client_capacities) or not isinstance(
        asset_capacity, dict
    ):
        raise LoadTestConfigurationError("capacity preflight returned the wrong shape")
    capacity_rows = [item for item in client_capacities if isinstance(item, dict)]
    client_checks = validate_test_client_capacities(
        capacity_rows, expected_count=len(RUNTIME.api_keys)
    )
    capacity = capacity_rows[0]
    if not isinstance(public_workflows, list) or not isinstance(workflows, list):
        raise LoadTestConfigurationError("workflow preflight returned the wrong shape")
    if not isinstance(nodes, list) or not isinstance(gpu_jobs, list):
        raise LoadTestConfigurationError("admin preflight returned the wrong shape")
    if not isinstance(asset_overview, dict):
        raise LoadTestConfigurationError("asset preflight returned the wrong shape")
    public_versions = {
        (str(item.get("workflow_key")), str(item.get("version"))) for item in public_workflows
    }
    for workflow_key, approval in SCENARIO.approved_workflows.items():
        version = str(approval["version"])
        if (workflow_key, version) not in public_versions:
            raise LoadTestConfigurationError(
                f"approved workflow is not publicly enabled: {workflow_key}:{version}"
            )
        matches = [
            item
            for item in workflows
            if item.get("workflow_key") == workflow_key
            and item.get("version") == version
            and item.get("enabled") is True
        ]
        if len(matches) != 1 or matches[0].get("template_sha256") != approval["template_sha256"]:
            raise LoadTestConfigurationError(
                f"approved template SHA mismatch: {workflow_key}:{version}"
            )

    imageclip = SCENARIO.approved_workflows["imageclip-rgba"]
    healthy_gpu_nodes = []
    for node in nodes:
        labels = node.get("labels") if isinstance(node.get("labels"), dict) else {}
        if (
            node.get("health") == "ONLINE"
            and node.get("mode") == "ACTIVE"
            and not node.get("external_busy")
            and labels.get("imageclip_commit") == imageclip["pipeline_commit"]
            and labels.get("imageclip_pipeline_sha256") == imageclip["pipeline_sha256"]
        ):
            healthy_gpu_nodes.append(node)
    if len(healthy_gpu_nodes) < SCENARIO.preflight["minimum_healthy_gpu_nodes"]:
        raise LoadTestConfigurationError("not enough healthy nodes with approved ImageClip SHA")

    active_gpu = [item for item in gpu_jobs if item.get("status") in ACTIVE_STATUSES]
    if len(active_gpu) > SCENARIO.preflight["maximum_preexisting_gpu_jobs"]:
        raise LoadTestConfigurationError("pre-existing GPU work exceeds the scenario limit")
    asset_jobs = asset_overview.get("jobs")
    workers = asset_overview.get("workers")
    if not isinstance(asset_jobs, list) or not isinstance(workers, list):
        raise LoadTestConfigurationError("asset overview omitted jobs or workers")
    if len(asset_jobs) >= 500:
        raise LoadTestConfigurationError("asset job audit window is saturated")
    active_assets = [item for item in asset_jobs if item.get("status") in ACTIVE_STATUSES]
    if len(active_assets) > SCENARIO.preflight["maximum_preexisting_asset_jobs"]:
        raise LoadTestConfigurationError("pre-existing asset work exceeds the scenario limit")
    worker_roles = validate_asset_worker_roles(
        workers,
        minimum_cpu_workers=SCENARIO.preflight["minimum_online_asset_workers"],
        minimum_cpu_slots=SCENARIO.preflight["minimum_cpu_slots"],
        minimum_substance_slots=SCENARIO.preflight["minimum_substance_slots"],
    )
    online_workers = list(worker_roles["online_workers"])
    cpu_workers = list(worker_roles["cpu_workers"])
    substance_workers = list(worker_roles["substance_workers"])
    substance_slots = int(worker_roles["substance_available_slots"])

    contracts = asset_overview.get("contracts")
    if not isinstance(contracts, dict):
        raise LoadTestConfigurationError("asset overview omitted server contracts")
    expected_asset_submits = {
        "/api/v1/assets/uv/process",
        "/api/v1/assets/retopology/audit",
        "/api/v1/assets/retopology/process",
        "/api/v1/services/modelview-roughness",
        "/api/v1/assets/bake/process",
    }
    actual_asset_submits = {
        str(item.get("submit")) for item in contracts.values() if isinstance(item, dict)
    }
    if expected_asset_submits != actual_asset_submits:
        raise LoadTestConfigurationError("server six-API contract set has drifted")

    if RUNTIME.is_production_target():
        cluster = capacity.get("cluster")
        if not isinstance(cluster, dict):
            raise LoadTestConfigurationError("production capacity omitted cluster counters")
        production_counters = {
            "GPU queue depth": capacity.get("queue_depth"),
            "GPU running jobs": cluster.get("running_jobs"),
            "GPU used slots": cluster.get("used_slots"),
            "asset used slots": asset_capacity.get("used_slots"),
            "active GPU admin jobs": len(active_gpu),
            "active asset admin jobs": len(active_assets),
        }
        for label, value in production_counters.items():
            if isinstance(value, bool) or not isinstance(value, int) or value != 0:
                raise LoadTestConfigurationError(f"production preflight requires {label}=0")
        if any(int(item.get("current_jobs", -1)) != 0 for item in healthy_gpu_nodes):
            raise LoadTestConfigurationError(
                "production preflight requires every approved GPU node to be idle"
            )
        if any(int(item.get("current_jobs", -1)) != 0 for item in online_workers):
            raise LoadTestConfigurationError(
                "production preflight requires every online asset worker to be idle"
            )

    sanitized = {
        "schema_version": "gpu-control-six-api-preflight.v1",
        "captured_at": utc_now(),
        "session_id": RUNTIME.session_id,
        "target": RUNTIME.target,
        "client_kind": "test",
        "api_key_checks": client_checks,
        "gpu_capacity": capacity,
        "asset_capacity": asset_capacity,
        "approved_workflows": SCENARIO.approved_workflows,
        "healthy_gpu_nodes": [
            {
                "id": item.get("id"),
                "health": item.get("health"),
                "mode": item.get("mode"),
                "current_jobs": item.get("current_jobs"),
                "imageclip_commit": (item.get("labels") or {}).get("imageclip_commit"),
                "imageclip_pipeline_sha256": (item.get("labels") or {}).get(
                    "imageclip_pipeline_sha256"
                ),
            }
            for item in healthy_gpu_nodes
        ],
        "online_asset_workers": [
            {
                "id": item.get("id"),
                "current_jobs": item.get("current_jobs"),
                "max_concurrency": item.get("max_concurrency"),
            }
            for item in online_workers
        ],
        "online_cpu_asset_worker_ids": [item.get("id") for item in cpu_workers],
        "online_substance_worker_ids": [item.get("id") for item in substance_workers],
        "cpu_available_slots": worker_roles["cpu_available_slots"],
        "preexisting": {"gpu": len(active_gpu), "asset": len(active_assets)},
        "gpu_active_audit": gpu_audit,
        "substance_available_slots": substance_slots,
        "secrets_recorded": False,
    }
    (RESULT_DIR / "preflight.json").write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sanitized


def collect_telemetry(client: httpx.Client) -> dict[str, Any]:
    api_headers = {"X-API-Key": RUNTIME.api_keys[0]}
    admin_headers = {"Authorization": f"Bearer {RUNTIME.admin_bearer_token}"}
    nodes = preflight_json(client, "/admin/nodes", admin_headers)
    asset_overview = preflight_json(client, "/admin/asset-processing?limit=500", admin_headers)
    gpu_jobs, gpu_audit = active_gpu_admin_jobs(
        client,
        admin_headers,
        client_kind="all",
    )
    capacity = normalize_scheduler_capacity_v1(
        preflight_json(client, "/api/v1/scheduler/capacity", api_headers)
    )
    asset_capacity = preflight_json(client, "/api/v1/assets/capacity", api_headers)
    if not isinstance(nodes, list) or not isinstance(gpu_jobs, list):
        raise LoadTestConfigurationError("telemetry nodes response has the wrong shape")
    if not isinstance(asset_overview, dict):
        raise LoadTestConfigurationError("telemetry asset response has the wrong shape")
    workers = asset_overview.get("workers")
    asset_jobs = asset_overview.get("jobs")
    if not isinstance(workers, list):
        raise LoadTestConfigurationError("telemetry asset response omitted workers")
    if not isinstance(asset_jobs, list):
        raise LoadTestConfigurationError("telemetry asset response omitted jobs")
    if len(asset_jobs) >= 500:
        raise LoadTestConfigurationError("production watchdog audit window is saturated")
    foreign_work = identify_foreign_active_work(
        gpu_jobs,
        asset_jobs,
        test_tenant_ids=RUNTIME.tenant_ids,
    )
    if not isinstance(capacity, dict) or not isinstance(asset_capacity, dict):
        raise LoadTestConfigurationError("telemetry capacity response has the wrong shape")
    cluster = capacity.get("cluster")
    if not isinstance(cluster, dict):
        raise LoadTestConfigurationError("telemetry scheduler response omitted cluster")
    asset_summary = asset_overview.get("summary")
    if not isinstance(asset_summary, dict) or not isinstance(asset_summary.get("counts"), dict):
        raise LoadTestConfigurationError("telemetry asset response omitted queue counts")

    def required_number(
        payload: Mapping[str, Any], key: str, *, maximum: float | None = None
    ) -> float:
        value = payload.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
            or (maximum is not None and float(value) > maximum)
        ):
            raise LoadTestConfigurationError(f"telemetry field {key} is invalid")
        return float(value)

    node_ids: set[str] = set()
    sanitized_nodes: list[dict[str, Any]] = []
    for item in nodes:
        if not isinstance(item, dict) or not item.get("id"):
            raise LoadTestConfigurationError("telemetry node has no id")
        node_id = str(item["id"])
        if node_id in node_ids:
            raise LoadTestConfigurationError("telemetry returned a duplicate node id")
        node_ids.add(node_id)
        utilization = required_number(item, "gpu_util_percent", maximum=100)
        free_vram = required_number(item, "free_vram_mb")
        total_vram = required_number(item, "total_vram_mb")
        current_jobs = required_number(item, "current_jobs")
        max_concurrency = required_number(item, "max_concurrency")
        if free_vram > total_vram or current_jobs > max_concurrency:
            raise LoadTestConfigurationError("telemetry node capacity invariant failed")
        sanitized_nodes.append(
            {
                "id": node_id,
                "gpu_util_percent": utilization,
                "free_vram_mb": free_vram,
                "total_vram_mb": total_vram,
                "current_jobs": current_jobs,
                "max_concurrency": max_concurrency,
                "mode": item.get("mode"),
                "health": item.get("health"),
                "last_heartbeat_at": item.get("last_heartbeat_at"),
            }
        )

    worker_ids: set[str] = set()
    sanitized_workers: list[dict[str, Any]] = []
    for item in workers:
        if not isinstance(item, dict) or not item.get("id"):
            raise LoadTestConfigurationError("telemetry worker has no id")
        worker_id = str(item["id"])
        if worker_id in worker_ids:
            raise LoadTestConfigurationError("telemetry returned a duplicate worker id")
        worker_ids.add(worker_id)
        current_jobs = required_number(item, "current_jobs")
        max_concurrency = required_number(item, "max_concurrency")
        if max_concurrency <= 0 or current_jobs > max_concurrency:
            raise LoadTestConfigurationError("telemetry worker capacity invariant failed")
        sanitized_workers.append(
            {
                "id": worker_id,
                "node_id": item.get("node_id"),
                "status": item.get("status"),
                "current_jobs": current_jobs,
                "max_concurrency": max_concurrency,
                "last_heartbeat_at": item.get("last_heartbeat_at"),
            }
        )

    for payload, keys in (
        (cluster, ("total_slots", "used_slots", "available_slots", "running_jobs")),
        (asset_capacity, ("total_slots", "used_slots", "available_slots")),
    ):
        for key in keys:
            required_number(payload, key)
        if float(payload["total_slots"]) != float(payload["used_slots"]) + float(
            payload["available_slots"]
        ):
            raise LoadTestConfigurationError("telemetry slot capacity invariant failed")
    required_number(capacity, "queue_depth")
    # The admin summary is a sparse GROUP BY map: an absent QUEUED key means
    # zero queued jobs, not malformed telemetry.
    queued_asset_jobs = required_number(
        {"queued_jobs": asset_summary["counts"].get("QUEUED", 0)},
        "queued_jobs",
    )
    return {
        "schema_version": "gpu-control-six-api-telemetry.v1",
        "captured_at": utc_now(),
        "session_id": RUNTIME.session_id,
        "valid": True,
        "gpu_nodes": sanitized_nodes,
        "asset_workers": sanitized_workers,
        "scheduler": {
            "queue_depth": capacity.get("queue_depth"),
            "accepting_batches": capacity.get("accepting_batches"),
            "compatible_nodes": capacity.get("compatible_nodes"),
            "cluster": {
                "eligible_nodes": cluster.get("eligible_nodes"),
                "total_slots": cluster.get("total_slots"),
                "used_slots": cluster.get("used_slots"),
                "available_slots": cluster.get("available_slots"),
                "queued_jobs": cluster.get("queued_jobs"),
                "running_jobs": cluster.get("running_jobs"),
            },
        },
        "asset_capacity": {
            "queued_jobs": queued_asset_jobs,
            "online_workers": asset_capacity.get("online_workers"),
            "total_slots": asset_capacity.get("total_slots"),
            "used_slots": asset_capacity.get("used_slots"),
            "available_slots": asset_capacity.get("available_slots"),
        },
        "production_watchdog": foreign_work,
        "gpu_active_audit": gpu_audit,
        "privacy": {
            "addresses_recorded": False,
            "credentials_recorded": False,
            "cpu_util_percent_available": False,
        },
    }


def append_telemetry_payload(payload: Mapping[str, Any]) -> None:
    with (RESULT_DIR / "telemetry.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def capture_telemetry_sample(client: httpx.Client, *, final_sample: bool) -> dict[str, Any]:
    global _telemetry_sequence

    cycle_started = time.monotonic()
    sequence = _telemetry_sequence + 1
    sample = collect_telemetry(client)
    capture_span_ms = int((time.monotonic() - cycle_started) * 1000)
    if capture_span_ms > int(TELEMETRY_INTERVAL_SECONDS * 1000):
        raise LoadTestConfigurationError("telemetry capture exceeded its sampling interval")
    telemetry_started = _telemetry_started_monotonic or cycle_started
    actual_elapsed_ms = int((cycle_started - telemetry_started) * 1000)
    sample.update(
        {
            "sequence": sequence,
            "scheduled_elapsed_ms": (
                actual_elapsed_ms
                if final_sample
                else int((sequence - 1) * TELEMETRY_INTERVAL_SECONDS * 1000)
            ),
            "actual_elapsed_ms": actual_elapsed_ms,
            "capture_span_ms": capture_span_ms,
            "final_sample": final_sample,
        }
    )
    _telemetry_sequence = sequence
    append_telemetry_payload(sample)
    return sample


def record_telemetry_failure(exc: Exception, *, final_sample: bool) -> int:
    global _telemetry_sequence

    _telemetry_sequence += 1
    sequence = _telemetry_sequence
    telemetry_started = _telemetry_started_monotonic or time.monotonic()
    append_telemetry_payload(
        {
            "schema_version": "gpu-control-six-api-telemetry-error.v1",
            "captured_at": utc_now(),
            "session_id": RUNTIME.session_id,
            "sequence": sequence,
            "actual_elapsed_ms": int(max(0.0, time.monotonic() - telemetry_started) * 1000),
            "final_sample": final_sample,
            "valid": False,
            "error_code": type(exc).__name__,
        }
    )
    return sequence


def handle_telemetry_watchdog(sample: Mapping[str, Any], environment: Any) -> bool:
    global _production_watchdog_triggered

    watchdog = sample.get("production_watchdog")
    if not isinstance(watchdog, Mapping) or watchdog.get("detected") is not True:
        return False
    _production_watchdog_triggered = True
    first_request = _shape_stop_signal.request("foreign_work_detected")
    environment.process_exit_code = 2
    if first_request:
        REGISTRY.event(
            "safety.foreign_work_detected",
            count=watchdog.get("count"),
            jobs=watchdog.get("jobs"),
            action="shape_stop_requested",
        )
    return True


def telemetry_loop(environment: Any) -> None:
    global _telemetry_stop

    with httpx.Client(
        base_url=RUNTIME.target,
        verify=httpx_verify(),
        follow_redirects=False,
        timeout=30,
    ) as client:
        while not _telemetry_stop:
            cycle_started = time.monotonic()
            try:
                sample = capture_telemetry_sample(client, final_sample=False)
            except Exception as exc:
                sequence = record_telemetry_failure(exc, final_sample=False)
                first_request = _shape_stop_signal.request("telemetry_sample_failed")
                environment.process_exit_code = 2
                if first_request:
                    REGISTRY.event(
                        "telemetry.sample_failed",
                        sequence=sequence,
                        error=type(exc).__name__,
                        action="shape_stop_requested",
                    )
                return
            if handle_telemetry_watchdog(sample, environment):
                return
            elapsed = time.monotonic() - cycle_started
            gevent.sleep(max(0.1, TELEMETRY_INTERVAL_SECONDS - elapsed))


def start_telemetry(environment: Any) -> None:
    global _telemetry_final_sample_written, _telemetry_greenlet
    global _telemetry_sequence, _telemetry_started_monotonic, _telemetry_stop

    _telemetry_stop = False
    _telemetry_sequence = 0
    _telemetry_started_monotonic = time.monotonic()
    _telemetry_final_sample_written = False
    _telemetry_greenlet = gevent.spawn(telemetry_loop, environment)


@events.test_stop.add_listener
def stop_telemetry(environment: Any | None = None, **_: Any) -> None:
    global _telemetry_final_sample_written, _telemetry_greenlet, _telemetry_stop

    _telemetry_stop = True
    greenlet = _telemetry_greenlet
    _telemetry_greenlet = None
    if greenlet is not None and greenlet is not gevent.getcurrent():
        greenlet.kill(block=True, timeout=5)
    if _telemetry_started_monotonic is None or _telemetry_final_sample_written:
        return
    _telemetry_final_sample_written = True
    try:
        with httpx.Client(
            base_url=RUNTIME.target,
            verify=httpx_verify(),
            follow_redirects=False,
            timeout=30,
        ) as client:
            sample = capture_telemetry_sample(client, final_sample=True)
    except Exception as exc:
        sequence = record_telemetry_failure(exc, final_sample=True)
        if environment is not None:
            environment.process_exit_code = 2
        REGISTRY.event(
            "telemetry.final_sample_failed",
            sequence=sequence,
            error=type(exc).__name__,
        )
        return
    if environment is not None:
        handle_telemetry_watchdog(sample, environment)


@events.test_start.add_listener
def guarded_preflight(environment: Any, **_: Any) -> None:
    global _expected_asset_worker_ids, _expected_gpu_node_ids

    _shape_stop_signal.reset()
    try:
        result = perform_preflight()
    except Exception as exc:
        REGISTRY.event("preflight.failed", error=type(exc).__name__, message=str(exc))
        environment.process_exit_code = 2
        if environment.runner is not None:
            environment.runner.quit()
        raise
    REGISTRY.event(
        "preflight.passed",
        healthy_gpu_nodes=len(result["healthy_gpu_nodes"]),
        online_asset_workers=len(result["online_asset_workers"]),
    )
    _expected_gpu_node_ids = tuple(
        str(item["id"]) for item in result["healthy_gpu_nodes"] if item.get("id")
    )
    _expected_asset_worker_ids = tuple(
        str(item["id"]) for item in result["online_asset_workers"] if item.get("id")
    )
    start_telemetry(environment)


class SixApiUser(HttpUser):
    host = RUNTIME.target
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        configure_locust_client_tls(self.client, RUNTIME.ca_file)
        self.api_key_index = next(_user_counter) % len(RUNTIME.api_keys)
        self.api_key = RUNTIME.api_keys[self.api_key_index]

    def validation_failure(
        self,
        api_name: str,
        check: str,
        message: str,
        *,
        response_length: int = 0,
    ) -> None:
        """Record post-response contract failures in Locust and the evidence log."""

        error = AssertionError(message)
        self.environment.events.request.fire(
            request_type="VALIDATION",
            name=f"{api_name}:{check}",
            response_time=0,
            response_length=response_length,
            exception=error,
            context={},
        )
        REGISTRY.event(
            "contract.validation_failed",
            api=api_name,
            check=check,
            message=message,
        )

    def request_with_retry(
        self,
        method: str,
        path: str,
        *,
        api_name: str,
        operation: str,
        headers: Mapping[str, str],
        request_factory: Callable[[], dict[str, Any]] | None = None,
        timeout: float | tuple[float, float] = 120,
    ) -> tuple[Any, int]:
        retries = 0
        while True:
            kwargs = request_factory() if request_factory else {}
            with self.client.request(
                method,
                path,
                headers=dict(headers),
                name=f"{api_name}:{operation}",
                timeout=timeout,
                catch_response=True,
                **kwargs,
            ) as response:
                status_code = int(response.status_code or 0)
                transport_error = getattr(response, "error", None)
                if not load_response_is_retryable(
                    status_code, has_transport_error=transport_error is not None
                ):
                    if status_code >= 400:
                        response.failure(f"HTTP {status_code}: {response.text[:200]}")
                    else:
                        response.success()
                    return response, retries
                failure_label = (
                    f"transport failure: {type(transport_error).__name__}"
                    if transport_error is not None or status_code <= 0
                    else f"transient HTTP {status_code}"
                )
                response.failure(failure_label)
                if retries >= SCENARIO.max_retries:
                    return response, retries
                retries += 1
                REGISTRY.retry(api_name, operation, status_code)
            gevent.sleep(min(8.0, 0.25 * (2**retries)))

    def post_multipart(
        self,
        path: str,
        *,
        api_name: str,
        operation: str = "submit",
        headers: Mapping[str, str],
        builder: Callable[[ExitStack], tuple[dict[str, str], list[tuple[str, Any]]]],
        timeout: float | tuple[float, float] = (15, 300),
    ) -> tuple[Any, int]:
        retries = 0
        while True:
            with ExitStack() as stack:
                data, files = builder(stack)
                with self.client.post(
                    path,
                    headers=dict(headers),
                    data=data,
                    files=files,
                    name=f"{api_name}:{operation}",
                    timeout=timeout,
                    catch_response=True,
                ) as response:
                    status_code = int(response.status_code or 0)
                    transport_error = getattr(response, "error", None)
                    REGISTRY.admission(api_name, status_code)
                    if not load_response_is_retryable(
                        status_code, has_transport_error=transport_error is not None
                    ):
                        if status_code not in {200, 202}:
                            response.failure(f"HTTP {status_code}: {response.text[:200]}")
                        else:
                            response.success()
                        return response, retries
                    failure_label = (
                        f"transport failure: {type(transport_error).__name__}"
                        if transport_error is not None or status_code <= 0
                        else f"transient HTTP {status_code}"
                    )
                    response.failure(failure_label)
                    if retries >= SCENARIO.max_retries:
                        return response, retries
                    retries += 1
                    REGISTRY.retry(api_name, operation, status_code)
            gevent.sleep(min(8.0, 0.25 * (2**retries)))

    def submit_async_asset(
        self,
        api_name: str,
        ordinal: int,
        metadata: dict[str, Any],
        builder: Callable[[ExitStack, str], tuple[dict[str, str], list[tuple[str, Any]]]],
    ) -> None:
        request_id, idempotency_key, traceparent = correlation(api_name, ordinal)
        business_id = external_id(api_name, ordinal)
        metadata["external_asset_id"] = business_id
        headers = request_headers(self.api_key, request_id, traceparent, idempotency_key)
        started = time.monotonic()
        response, retries = self.post_multipart(
            API_CONTRACTS[api_name]["submit"],
            api_name=api_name,
            headers=headers,
            builder=lambda stack: builder(
                stack, json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            ),
        )
        payload = safe_json(response)
        job_id = str(payload.get("job_id") or "")
        if response.status_code not in {200, 202} or not job_id:
            REGISTRY.event(
                "task.submit_failed",
                api=api_name,
                status_code=response.status_code,
                request_id=request_id,
            )
            return
        expected_status_url = f"/api/v1/assets/jobs/{job_id}"
        expected_cancel_url = f"{expected_status_url}/cancel"
        if payload.get("status_url") not in (None, expected_status_url) or payload.get(
            "cancel_url"
        ) not in (None, expected_cancel_url):
            self.validation_failure(
                api_name,
                "job-links",
                "asset submit response returned unexpected status/cancel links",
            )
        REGISTRY.register(
            job_id,
            api_name=api_name,
            kind="asset",
            status_url=expected_status_url,
            cancel_url=expected_cancel_url,
            external_id=business_id,
            api_key_index=self.api_key_index,
            idempotency_key=idempotency_key,
            request_id=request_id,
            traceparent=traceparent,
            created_monotonic=started,
            submission_retries=retries,
        )
        self.poll_and_collect(job_id, api_name, headers)

    def poll_and_collect(
        self,
        identifier: str,
        api_name: str,
        headers: Mapping[str, str],
    ) -> None:
        record = next((item for item in REGISTRY.records() if item["id"] == identifier), None)
        if record is None:
            return
        deadline = time.monotonic() + SCENARIO.operation_timeout_seconds[api_name]
        final_payload: dict[str, Any] = {}
        poll_retries = 0
        while time.monotonic() < deadline:
            response, retries = self.request_with_retry(
                "GET",
                str(record["status_url"]),
                api_name=api_name,
                operation="poll",
                headers=headers,
                timeout=30,
            )
            poll_retries += retries
            if response.status_code != 200:
                gevent.sleep(SCENARIO.poll_interval_seconds)
                continue
            final_payload = safe_json(response)
            REGISTRY.update_status(identifier, final_payload)
            if str(final_payload.get("status")) in TERMINAL_STATUSES:
                break
            gevent.sleep(SCENARIO.poll_interval_seconds)
        REGISTRY.add_retries(identifier, poll_retries)
        final_status = str(final_payload.get("status"))
        if not final_payload or final_status not in TERMINAL_STATUSES:
            REGISTRY.mark_poll_timeout(identifier)
            REGISTRY.event("task.poll_timeout", api=api_name, task_id=identifier)
            self.validation_failure(
                api_name,
                "poll-timeout",
                "task did not reach a terminal state before its operation timeout",
            )
            return
        if API_CONTRACTS[api_name]["resource"] in {"CPU", "GPU_FENCED_ASSET"}:
            artifacts = final_payload.get("artifacts")
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    if isinstance(artifact, dict):
                        self.download_artifact(identifier, api_name, artifact, headers)
                    else:
                        REGISTRY.mark_artifact_contract_failure(
                            identifier, "asset artifact entry is not an object"
                        )
                        self.validation_failure(
                            api_name,
                            "artifact-contract",
                            "asset artifact entry is not an object",
                        )
            if final_status in LOAD_SUCCESS_STATUSES and not artifacts:
                REGISTRY.mark_artifact_contract_failure(
                    identifier, "successful asset job returned no artifacts"
                )
                self.validation_failure(
                    api_name,
                    "artifact-contract",
                    "successful asset job returned no artifacts",
                )
            if final_status not in LOAD_SUCCESS_STATUSES:
                self.validation_failure(
                    api_name,
                    "business-terminal",
                    f"task ended in unsuccessful business status {final_status}",
                )
            return
        if final_status not in LOAD_SUCCESS_STATUSES:
            self.validation_failure(
                api_name,
                "business-terminal",
                f"task ended in unsuccessful business status {final_status}",
            )
            return
        if api_name == "imageclip_batch":
            artifact = final_payload.get("artifact")
            if isinstance(artifact, dict):
                self.download_artifact(identifier, api_name, artifact, headers)
            else:
                REGISTRY.mark_artifact_contract_failure(
                    identifier, "successful ImageClip batch returned no final artifact"
                )
                self.validation_failure(
                    api_name,
                    "artifact-contract",
                    "successful ImageClip batch returned no final artifact",
                )
            return
        listing, retries = self.request_with_retry(
            "GET",
            f"/api/v1/jobs/{identifier}/artifacts",
            api_name=api_name,
            operation="artifact-list",
            headers=headers,
            timeout=30,
        )
        REGISTRY.add_retries(identifier, retries)
        if listing.status_code == 200 and isinstance(listing.json(), list):
            for artifact in listing.json():
                if isinstance(artifact, dict) and artifact.get("id"):
                    artifact = {
                        **artifact,
                        "download_url": f"/api/v1/jobs/{identifier}/artifacts/{artifact['id']}",
                    }
                    self.download_artifact(identifier, api_name, artifact, headers)
                else:
                    self.validation_failure(
                        api_name,
                        "artifact-contract",
                        "artifact listing item has no id",
                    )

    def download_artifact(
        self,
        identifier: str,
        api_name: str,
        artifact: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> None:
        url = str(artifact.get("download_url") or "")
        expected_sha = str(artifact.get("sha256") or "")
        if not url or len(expected_sha) != 64:
            REGISTRY.mark_artifact_contract_failure(
                identifier, "artifact URL or SHA-256 is missing"
            )
            REGISTRY.event("artifact.invalid_contract", api=api_name, task_id=identifier)
            self.validation_failure(
                api_name,
                "artifact-contract",
                "artifact URL or SHA-256 is missing",
            )
            return
        if not url.startswith("/") or url.startswith("//"):
            REGISTRY.mark_artifact_contract_failure(
                identifier, "artifact URL is not a same-origin relative path"
            )
            self.validation_failure(
                api_name,
                "artifact-origin",
                "artifact URL must be a same-origin relative API path",
            )
            return
        response, retries = self.request_with_retry(
            "GET",
            url,
            api_name=api_name,
            operation="artifact-download",
            headers=headers,
            timeout=(15, 600),
        )
        REGISTRY.add_retries(identifier, retries)
        if response.status_code != 200:
            REGISTRY.mark_artifact_contract_failure(
                identifier, f"artifact download returned HTTP {response.status_code}"
            )
            return
        actual_sha = hashlib.sha256(response.content).hexdigest()
        header_sha = response.headers.get("X-Artifact-SHA256")
        if actual_sha != expected_sha or (header_sha and header_sha != expected_sha):
            REGISTRY.mark_artifact_contract_failure(identifier, "artifact SHA-256 mismatch")
            self.validation_failure(
                api_name,
                "artifact-sha256",
                "artifact SHA-256 mismatch",
                response_length=len(response.content),
            )
            REGISTRY.event(
                "artifact.sha_mismatch",
                api=api_name,
                task_id=identifier,
                expected_sha256=expected_sha,
                actual_sha256=actual_sha,
            )
            return
        REGISTRY.add_artifact(identifier, len(response.content))
        REGISTRY.event(
            "artifact.verified",
            api=api_name,
            task_id=identifier,
            size_bytes=len(response.content),
            sha256=actual_sha,
        )

    def run_imageclip_batch(self, ordinal: int) -> None:
        api_name = "imageclip_batch"
        request_id, idempotency_key, traceparent = correlation(api_name, ordinal)
        manifest = fixture_json(api_name, "manifest")
        business_id = external_id(api_name, ordinal)
        manifest["external_batch_id"] = business_id
        headers = request_headers(self.api_key, request_id, traceparent, idempotency_key)

        def builder(stack: ExitStack) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            archive = fixture_path(api_name, "archive")
            handle = stack.enter_context(archive.open("rb"))
            return (
                {"manifest": json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))},
                [("archive", (archive.name, handle, "application/zip"))],
            )

        started = time.monotonic()
        response, retries = self.post_multipart(
            API_CONTRACTS[api_name]["submit"],
            api_name=api_name,
            headers=headers,
            builder=builder,
            timeout=(15, 900),
        )
        payload = safe_json(response)
        batch_id = str(payload.get("batch_id") or "")
        if response.status_code not in {200, 202} or not batch_id:
            return
        expected_status_url = f"/api/v1/batches/{batch_id}"
        if payload.get("status_url") not in (None, expected_status_url):
            self.validation_failure(
                api_name,
                "batch-links",
                "ImageClip submit response returned an unexpected status link",
            )
        REGISTRY.register(
            batch_id,
            api_name=api_name,
            kind="batch",
            status_url=expected_status_url,
            cancel_url=f"/api/v1/batches/{batch_id}/cancel",
            external_id=business_id,
            api_key_index=self.api_key_index,
            idempotency_key=idempotency_key,
            request_id=request_id,
            traceparent=traceparent,
            created_monotonic=started,
            submission_retries=retries,
        )
        self.poll_and_collect(batch_id, api_name, headers)

    def run_modelview_roughness(self, ordinal: int) -> None:
        api_name = "modelview_roughness"
        request_id, idempotency_key, traceparent = correlation(api_name, ordinal)
        headers = request_headers(self.api_key, request_id, traceparent, idempotency_key)

        def builder(stack: ExitStack) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            image = fixture_path(api_name, "image")
            handle = stack.enter_context(image.open("rb"))
            return {"parameters": "{}"}, [("image", (image.name, handle, "image/png"))]

        started = time.monotonic()
        response, retries = self.post_multipart(
            API_CONTRACTS[api_name]["submit"],
            api_name=api_name,
            operation="sync-e2e",
            headers=headers,
            builder=builder,
            timeout=(15, SCENARIO.operation_timeout_seconds[api_name]),
        )
        job_id = str(response.headers.get("X-Job-ID") or "")
        if response.status_code != 200 or not job_id:
            return
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/") or not response.content:
            self.validation_failure(
                api_name,
                "final-image",
                "roughness endpoint did not return a final image",
                response_length=len(response.content),
            )
            return
        REGISTRY.register(
            job_id,
            api_name=api_name,
            kind="job",
            status_url=f"/api/v1/jobs/{job_id}",
            cancel_url=f"/api/v1/jobs/{job_id}/cancel",
            external_id=None,
            api_key_index=self.api_key_index,
            idempotency_key=idempotency_key,
            request_id=request_id,
            traceparent=traceparent,
            created_monotonic=started,
            submission_retries=retries,
        )
        REGISTRY.add_artifact(job_id, len(response.content))
        self.poll_and_collect(job_id, api_name, headers)

    def run_uv_process(self, ordinal: int) -> None:
        api_name = "uv_process"
        metadata = fixture_json(api_name, "metadata")

        def builder(
            stack: ExitStack, metadata_text: str
        ) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            asset = fixture_path(api_name, "asset")
            handle = stack.enter_context(asset.open("rb"))
            return {"metadata": metadata_text}, [
                ("asset", (asset.name, handle, "application/octet-stream"))
            ]

        self.submit_async_asset(api_name, ordinal, metadata, builder)

    def run_retopology_audit(self, ordinal: int) -> None:
        api_name = "retopology_audit"
        metadata = fixture_json(api_name, "metadata")

        def builder(
            stack: ExitStack, metadata_text: str
        ) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            project = fixture_path(api_name, "project")
            handle = stack.enter_context(project.open("rb"))
            return {"metadata": metadata_text}, [
                ("project", (project.name, handle, "application/octet-stream"))
            ]

        self.submit_async_asset(api_name, ordinal, metadata, builder)

    def run_retopology_process(self, ordinal: int) -> None:
        api_name = "retopology_process"
        metadata = fixture_json(api_name, "metadata")

        def builder(
            stack: ExitStack, metadata_text: str
        ) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            project = fixture_path(api_name, "project")
            project_handle = stack.enter_context(project.open("rb"))
            files: list[tuple[str, Any]] = [
                ("project", (project.name, project_handle, "application/octet-stream"))
            ]
            reference_values = FIXTURES.paths_for(api_name).get("reference_images", ())
            references = reference_values if isinstance(reference_values, tuple) else ()
            for image in references:
                handle = stack.enter_context(image.open("rb"))
                files.append(("reference_images", (image.name, handle, "image/png")))
            return {"metadata": metadata_text}, files

        self.submit_async_asset(api_name, ordinal, metadata, builder)

    def run_substance_bake(self, ordinal: int) -> None:
        api_name = "substance_bake"
        metadata = fixture_json(api_name, "metadata")

        def builder(
            stack: ExitStack, metadata_text: str
        ) -> tuple[dict[str, str], list[tuple[str, Any]]]:
            entry = FIXTURES.paths_for(api_name)
            upload_keys = (
                "low_mesh",
                "high_mesh",
                "cage_mesh",
                "base_color_texture",
                "roughness_texture",
                "metallic_texture",
            )
            files: list[tuple[str, Any]] = []
            for key in upload_keys:
                path = entry.get(key)
                if not isinstance(path, Path):
                    continue
                handle = stack.enter_context(path.open("rb"))
                media_type = "image/png" if "texture" in key else "application/octet-stream"
                files.append((key, (path.name, handle, media_type)))
            return {"metadata": metadata_text}, files

        self.submit_async_asset(api_name, ordinal, metadata, builder)

    @task
    def business_cycle(self) -> None:
        ordinal = next(_operation_counter)
        api_name = random.choices(  # noqa: S311 - configured reproducible workload mix
            list(API_NAMES),
            weights=[SCENARIO.weights[name] for name in API_NAMES],
            k=1,
        )[0]
        handler = {
            "imageclip_batch": self.run_imageclip_batch,
            "modelview_roughness": self.run_modelview_roughness,
            "uv_process": self.run_uv_process,
            "retopology_audit": self.run_retopology_audit,
            "retopology_process": self.run_retopology_process,
            "substance_bake": self.run_substance_bake,
        }[api_name]
        try:
            handler(ordinal)
        except Exception as exc:
            REGISTRY.event(
                "task.client_exception",
                api=api_name,
                ordinal=ordinal,
                error=type(exc).__name__,
                message=str(exc),
            )
            environment = self.environment
            environment.events.request.fire(
                request_type="CLIENT",
                name=f"{api_name}:client-exception",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )


class SixApiStagesShape(LoadTestShape):
    def tick(self) -> tuple[int, float] | None:
        return select_load_shape_stage(
            SCENARIO.stages,
            self.get_run_time(),
            stop_requested=_shape_stop_signal.requested,
        )


def teardown_cancel_sender(
    client: httpx.Client,
    cancel_url: str,
    headers: Mapping[str, str],
    attempt_counter: list[int],
) -> Callable[[], int]:
    def send_cancel() -> int:
        attempt_counter[0] += 1
        return client.post(cancel_url, headers=dict(headers)).status_code

    return send_cancel


def discover_teardown_records(
    client: httpx.Client,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    admin_headers = {"Authorization": f"Bearer {RUNTIME.admin_bearer_token}"}
    gpu_jobs, gpu_audit = active_gpu_admin_jobs(
        client,
        admin_headers,
        client_kind="test",
        passes=2,
    )
    asset_response = client.get("/admin/asset-processing?limit=500", headers=admin_headers)
    asset_response.raise_for_status()
    asset_overview = asset_response.json()
    if not isinstance(gpu_jobs, list) or not isinstance(asset_overview, dict):
        raise LoadTestConfigurationError(
            "teardown recovery admin endpoints returned the wrong shape"
        )
    asset_jobs = asset_overview.get("jobs")
    if not isinstance(asset_jobs, list):
        raise LoadTestConfigurationError("teardown recovery asset overview omitted jobs")
    if len(asset_jobs) >= 500:
        raise LoadTestConfigurationError("teardown recovery asset audit window is saturated")
    candidates = discover_scoped_teardown_tasks(
        gpu_jobs,
        asset_jobs,
        tenant_key_indices={tenant_id: index for index, tenant_id in enumerate(RUNTIME.tenant_ids)},
        session_id=RUNTIME.session_id,
        started_at=REGISTRY.started_at,
    )
    return candidates, {
        "passed": True,
        "scope": "exclusive_test_tenant+run_started_at+business_identity",
        "gpu_rows_scanned": gpu_audit["rows_scanned"],
        "gpu_status_queries": gpu_audit["status_queries"],
        "asset_rows_scanned": len(asset_jobs),
        "active_candidates": len(candidates),
    }


def settle_teardown_tasks(
    client: httpx.Client,
    tasks: Mapping[str, Mapping[str, Any]],
    outcomes: list[dict[str, Any]],
) -> None:
    outcome_by_id = {str(item["task_id"]): item for item in outcomes}
    pending = set(tasks)
    deadline = time.monotonic() + TEARDOWN_SETTLE_TIMEOUT_SECONDS
    while pending and time.monotonic() < deadline:
        for identifier in list(pending):
            record = tasks[identifier]
            outcome = outcome_by_id[identifier]
            key_index = int(record["api_key_index"])
            headers = {
                "X-API-Key": RUNTIME.api_keys[key_index],
                "X-Request-ID": (f"lt:{RUNTIME.session_id}:settle:{identifier}"[:64]),
            }
            try:
                response = client.get(str(record["status_url"]), headers=headers)
                outcome["last_settle_http_status"] = response.status_code
                if response.status_code != 200:
                    continue
                status = str(safe_json(response).get("status") or "")
                outcome["last_observed_status"] = status
                if status in TERMINAL_STATUSES:
                    outcome["settled"] = True
                    outcome["final_status"] = status
                    outcome["cleanup_safe"] = (
                        status in LOAD_SUCCESS_STATUSES or status == "CANCELLED"
                    )
                    pending.remove(identifier)
            except httpx.HTTPError as exc:
                outcome["settle_error"] = type(exc).__name__
        if pending:
            gevent.sleep(TEARDOWN_SETTLE_POLL_INTERVAL_SECONDS)
    for identifier in pending:
        outcome = outcome_by_id[identifier]
        outcome["settled"] = False
        outcome["cleanup_safe"] = False
        outcome["settle_timeout_seconds"] = TEARDOWN_SETTLE_TIMEOUT_SECONDS


def teardown_session_tasks() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=RUNTIME.target,
        verify=httpx_verify(),
        follow_redirects=False,
        timeout=TEARDOWN_CANCEL_TIMEOUT_SECONDS,
    ) as client:
        try:
            discovered, recovery_scan = discover_teardown_records(client)
        except (httpx.HTTPError, ValueError, LoadTestConfigurationError) as exc:
            discovered = []
            recovery_scan = {
                "passed": False,
                "scope": "exclusive_test_tenant+run_started_at+business_identity",
                "error": type(exc).__name__,
                "message": str(exc),
            }
            REGISTRY.event(
                "teardown.recovery_scan_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
        active_records = REGISTRY.active()
        registered_ids = {str(record["id"]) for record in active_records}
        recovered = [record for record in discovered if str(record["id"]) not in registered_ids]
        recovery_scan["unregistered_recovered"] = len(recovered)
        active_records.extend(recovered)
        tasks = {str(record["id"]): record for record in active_records}
        for index, record in enumerate(active_records):
            key_index = int(record["api_key_index"])
            if key_index < 0 or key_index >= len(RUNTIME.api_keys):
                outcome = {
                    "task_id": record["id"],
                    "api": record["api"],
                    "status_code": 0,
                    "cancelled": False,
                    "settled": False,
                    "cleanup_safe": False,
                    "error": "APIKeyIndexOutOfRange",
                    "attempts": 0,
                    "recovered_by_scope_scan": bool(record.get("recovery_source")),
                }
                outcomes.append(outcome)
                REGISTRY.event("teardown.cancel", **outcome)
                tasks.pop(str(record["id"]), None)
                continue
            api_key = RUNTIME.api_keys[key_index]
            request_id = f"lt:{RUNTIME.session_id}:teardown:{record['id']}"[:64]
            headers = {
                "X-API-Key": api_key,
                "X-Request-ID": request_id,
            }
            if record["kind"] == "batch":
                headers["Idempotency-Key"] = f"{record['external_id']}:cancel"
            attempt_counter = [0]
            send_cancel = teardown_cancel_sender(
                client,
                str(record["cancel_url"]),
                headers,
                attempt_counter,
            )

            try:
                status_code, attempts = execute_bounded_teardown_cancel(
                    send_cancel,
                    gevent.sleep,
                    max_attempts=TEARDOWN_CANCEL_MAX_ATTEMPTS,
                    initial_backoff_seconds=TEARDOWN_CANCEL_INITIAL_BACKOFF_SECONDS,
                    maximum_backoff_seconds=TEARDOWN_CANCEL_MAXIMUM_BACKOFF_SECONDS,
                )
                outcome = {
                    "task_id": record["id"],
                    "api": record["api"],
                    "status_code": status_code,
                    "cancelled": status_code == 200,
                    "attempts": attempts,
                    "settled": False,
                    "cleanup_safe": False,
                    "recovered_by_scope_scan": bool(record.get("recovery_source")),
                }
            except httpx.HTTPError as exc:
                attempts = attempt_counter[0]
                outcome = {
                    "task_id": record["id"],
                    "api": record["api"],
                    "status_code": 0,
                    "cancelled": False,
                    "settled": False,
                    "cleanup_safe": False,
                    "error": type(exc).__name__,
                    "attempts": attempts,
                    "recovered_by_scope_scan": bool(record.get("recovery_source")),
                }
            outcomes.append(outcome)
            REGISTRY.event("teardown.cancel", **outcome)
            if index + 1 < len(active_records):
                gevent.sleep(TEARDOWN_CANCEL_THROTTLE_SECONDS)
        if tasks:
            settle_teardown_tasks(client, tasks, outcomes)
        for outcome in outcomes:
            REGISTRY.event(
                "teardown.settled",
                task_id=outcome["task_id"],
                api=outcome["api"],
                settled=outcome.get("settled"),
                final_status=outcome.get("final_status"),
                cleanup_safe=outcome.get("cleanup_safe"),
            )
    return outcomes, recovery_scan


def locust_stats(environment: Any) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for (name, method), stat in environment.stats.entries.items():
        entries[f"{method} {name}"] = {
            "requests": stat.num_requests,
            "failures": stat.num_failures,
            "average_ms": round(stat.avg_response_time, 3),
            "p50_ms": stat.get_response_time_percentile(0.50),
            "p90_ms": stat.get_response_time_percentile(0.90),
            "p95_ms": stat.get_response_time_percentile(0.95),
            "p99_ms": stat.get_response_time_percentile(0.99),
            "rps": round(stat.current_rps, 6),
        }
    total = environment.stats.total
    return {
        "total": {
            "requests": total.num_requests,
            "failures": total.num_failures,
            "failure_rate": total.fail_ratio,
            "p50_ms": total.get_response_time_percentile(0.50),
            "p90_ms": total.get_response_time_percentile(0.90),
            "p95_ms": total.get_response_time_percentile(0.95),
            "p99_ms": total.get_response_time_percentile(0.99),
        },
        "entries": entries,
    }


def read_telemetry_samples() -> list[dict[str, Any]]:
    path = RESULT_DIR / "telemetry.jsonl"
    if not path.is_file():
        return []
    samples: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise LoadTestConfigurationError(
                f"telemetry.jsonl line {line_number} is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise LoadTestConfigurationError(f"telemetry.jsonl line {line_number} is not an object")
        samples.append(payload)
    return samples


@events.test_stop.add_listener
def finalize_results(environment: Any, **_: Any) -> None:
    stop_telemetry(environment=environment)
    teardown, recovery_scan = teardown_session_tasks()
    records = REGISTRY.records()
    summary = REGISTRY.summary()
    api_counts = summary.get("apis") if isinstance(summary.get("apis"), dict) else {}
    missing_apis = [name for name in API_NAMES if int(api_counts.get(name) or 0) < 1]
    summary["six_api_coverage"] = {
        "required": list(API_NAMES),
        "missing": missing_apis,
        "passed": not missing_apis,
    }
    summary["http"] = locust_stats(environment)
    summary["threshold_evaluation"] = evaluate_load_thresholds(summary, SCENARIO.thresholds)
    telemetry_samples: list[dict[str, Any]] = []
    try:
        telemetry_samples = read_telemetry_samples()
        telemetry = summarize_telemetry(
            telemetry_samples,
            expected_gpu_ids=_expected_gpu_node_ids,
            expected_worker_ids=_expected_asset_worker_ids,
        )
    except (OSError, LoadTestConfigurationError) as exc:
        telemetry = {
            "schema_version": "gpu-control-six-api-telemetry-summary.v1",
            "sample_count": 0,
            "evidence_complete": False,
            "error": type(exc).__name__,
        }
    expected_resources = telemetry.get("expected_resources")
    resources = expected_resources if isinstance(expected_resources, dict) else {}
    telemetry_evidence = evaluate_telemetry_evidence(
        telemetry_samples,
        expected_resources=resources,
        sampling_interval_seconds=TELEMETRY_INTERVAL_SECONDS,
    )
    gpu_nodes = telemetry.get("gpu_nodes")
    gpu_node_metrics = gpu_nodes if isinstance(gpu_nodes, dict) else {}
    saturated_gpu_ids = [
        node_id
        for node_id in _expected_gpu_node_ids
        if isinstance(gpu_node_metrics.get(node_id), dict)
        and gpu_node_metrics[node_id].get("saturation_ge_90_percent_ratio") is not None
        and float(gpu_node_metrics[node_id]["saturation_ge_90_percent_ratio"]) > 0
    ]
    missing_saturated_gpu_ids = sorted(set(_expected_gpu_node_ids) - set(saturated_gpu_ids))
    telemetry.update(
        {
            "sampling_interval_seconds": TELEMETRY_INTERVAL_SECONDS,
            "sampling_evidence": telemetry_evidence,
            "evidence_complete": telemetry_evidence["passed"],
            "gpu_saturation_objective": {
                "required_gpu_ids": list(_expected_gpu_node_ids),
                "observed_gpu_ids": sorted(saturated_gpu_ids),
                "missing_gpu_ids": missing_saturated_gpu_ids,
                "passed": (
                    len(_expected_gpu_node_ids) >= SCENARIO.preflight["minimum_healthy_gpu_nodes"]
                    and not missing_saturated_gpu_ids
                ),
            },
        }
    )
    summary["telemetry"] = telemetry
    summary["teardown"] = {
        "attempted": len(teardown),
        "accepted": sum(item["cancelled"] for item in teardown),
        "settled": sum(item.get("settled") is True for item in teardown),
        "scope": "registry_plus_scoped_admin_recovery",
        "recovery_scan": recovery_scan,
    }
    summary["production_watchdog"] = {
        "triggered": _production_watchdog_triggered,
        "action": "shape_stop_requested" if _production_watchdog_triggered else None,
        "running_test_work_is_non_preemptive": True,
    }
    summary["shape_stop"] = {
        "requested": _shape_stop_signal.requested,
        "reason": _shape_stop_signal.reason,
    }
    summary["lifecycle_evaluation"] = evaluate_load_lifecycle(
        records,
        teardown,
        mode=SCENARIO.lifecycle_mode,
        recovery_scan_passed=recovery_scan.get("passed") is True,
    )
    (RESULT_DIR / "records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (RESULT_DIR / "teardown.json").write_text(
        json.dumps(teardown, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (RESULT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if (
        not summary["threshold_evaluation"]["passed"]
        or not summary["six_api_coverage"]["passed"]
        or not summary["lifecycle_evaluation"]["passed"]
        or not telemetry["evidence_complete"]
        or not telemetry["gpu_saturation_objective"]["passed"]
        or _production_watchdog_triggered
    ):
        environment.process_exit_code = environment.process_exit_code or 1
    write_result_manifest(RESULT_DIR, session_id=RUNTIME.session_id)
