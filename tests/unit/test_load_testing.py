import ast
import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from packages.gpu_control_core.load_testing import (
    API_NAMES,
    REQUIRED_FULL_BACKUP_PAYLOADS,
    LoadShapeStopSignal,
    LoadStage,
    LoadTestConfigurationError,
    RuntimeSettings,
    build_plan,
    configure_locust_client_tls,
    discover_scoped_teardown_tasks,
    evaluate_load_lifecycle,
    evaluate_load_thresholds,
    evaluate_telemetry_evidence,
    execute_bounded_teardown_cancel,
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
    validate_production_backup,
    validate_test_client_capacities,
)
from scripts.run_six_api_load import (
    SAFE_LOCUST_STOP_TIMEOUT_SECONDS,
    locust_child_environment,
    locust_command,
)


def write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def scenario_payload(*, weights_confirmed: bool = True) -> dict:
    return {
        "schema_version": "1.0",
        "weights_confirmed": weights_confirmed,
        "weights": {name: 1 for name in API_NAMES},
        "stages": [
            {"users": 10, "spawn_rate": 2, "duration_seconds": 10},
            {"users": 120, "spawn_rate": 20, "duration_seconds": 30},
        ],
        "poll_interval_seconds": 1,
        "max_retries": 2,
        "max_backup_age_hours": 24,
        "operation_timeout_seconds": {name: 60 for name in API_NAMES},
        "approved_workflows": {
            "imageclip-rgba": {
                "version": "approved-imageclip",
                "template_sha256": "1" * 64,
                "pipeline_commit": "2" * 40,
                "pipeline_sha256": "3" * 64,
                "output_node": "SaveImage #25",
            },
            "modelview-roughness": {
                "version": "approved-roughness",
                "template_sha256": "4" * 64,
            },
        },
        "preflight": {
            "minimum_healthy_gpu_nodes": 3,
            "minimum_online_asset_workers": 3,
            "minimum_substance_slots": 1,
        },
        "thresholds": {
            "http_failure_rate_percent": 1,
            "submit_p95_ms": 3000,
            "poll_p95_ms": 1500,
            "artifact_p95_ms": 30000,
            "queue_p95_ms": 900000,
            "retry_rate_percent": 5,
        },
    }


def fixture_manifest(tmp_path: Path) -> Path:
    image_bytes = b"fixture-image"
    frame_sha = hashlib.sha256(image_bytes).hexdigest()
    archive_path = tmp_path / "frames.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("frames/0001.png", image_bytes)
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "external_batch_id": "loadtest:fixture",
                "failure_policy": "all_or_nothing",
                "output_naming": "preserve_stem_png",
                "parameters": {},
                "frames": [
                    {
                        "ordinal": 0,
                        "relative_path": "frames/0001.png",
                        "size_bytes": len(image_bytes),
                        "sha256": frame_sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    simple_files = {
        "roughness.png": b"png",
        "asset.fbx": b"fbx",
        "audit.blend": b"blend",
        "process.blend": b"blend",
        "front.png": b"png",
        "low.fbx": b"fbx",
    }
    for filename, content in simple_files.items():
        (tmp_path / filename).write_bytes(content)
    metadata = {
        "uv.json": {
            "external_asset_id": "loadtest:uv",
            "options": {"resolution": 2048, "padding_px": 10},
        },
        "audit.json": {
            "external_asset_id": "loadtest:audit",
            "options": {
                "high_object": "high",
                "reference_object": "reference",
                "low_object": "low",
            },
        },
        "process.json": {
            "external_asset_id": "loadtest:process",
            "options": {
                "high_object": "high",
                "reference_object": "reference",
                "low_object": "low",
                "generated_low_object": "generated_v001",
            },
            "reference_views": [{"filename": "front.png", "view": "front"}],
        },
        "bake.json": {
            "external_asset_id": "loadtest:bake",
            "options": {"profile": "ao-self-v1", "resolution": 1024},
        },
    }
    for filename, payload in metadata.items():
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")
    return write_yaml(
        tmp_path / "fixtures.yaml",
        {
            "schema_version": "1.0",
            "apis": {
                "imageclip_batch": {
                    "archive": str(archive_path),
                    "manifest": str(manifest_path),
                },
                "modelview_roughness": {"image": str(tmp_path / "roughness.png")},
                "uv_process": {
                    "asset": str(tmp_path / "asset.fbx"),
                    "metadata": str(tmp_path / "uv.json"),
                },
                "retopology_audit": {
                    "project": str(tmp_path / "audit.blend"),
                    "metadata": str(tmp_path / "audit.json"),
                },
                "retopology_process": {
                    "project": str(tmp_path / "process.blend"),
                    "metadata": str(tmp_path / "process.json"),
                    "reference_images": [str(tmp_path / "front.png")],
                },
                "substance_bake": {
                    "low_mesh": str(tmp_path / "low.fbx"),
                    "metadata": str(tmp_path / "bake.json"),
                },
            },
        },
    )


def allowed_environment(tmp_path: Path, target: str = "https://staging.example") -> dict[str, str]:
    base = {
        "LOAD_TEST_TARGET": target,
        "LOAD_TEST_SESSION_ID": "staging-acceptance-01",
        "LOAD_TEST_ENVIRONMENT": "staging",
        "ALLOW_LOAD_TEST": "true",
        "LOAD_TEST_TARGET_ALLOWLIST": target,
        "LOAD_TEST_API_KEYS": "gpc_test_one,gpc_test_two",
        "LOAD_TEST_TENANT_IDS": "tenant-one,tenant-two",
        "LOAD_TEST_ADMIN_BEARER_TOKEN": "read-only-admin-token",
        "LOAD_TEST_CA_FILE": str(tmp_path / "ca.crt"),
        "LOAD_TEST_RESULT_DIR": str(tmp_path / "results"),
    }
    (tmp_path / "ca.crt").write_text("test-ca", encoding="utf-8")
    provisional = RuntimeSettings.from_environment(base)
    base["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    return base


def complete_full_backup(tmp_path: Path, created_at: datetime) -> Path:
    backup_dir = tmp_path / "backups" / created_at.strftime("%Y%m%dT%H%M%SZ-full")
    backup_dir.mkdir(parents=True)
    stamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    manifest = backup_dir / "BACKUP_MANIFEST"
    manifest.write_text(
        "\n".join(
            [
                "BACKUP_FORMAT=2",
                "MODE=full",
                f"CREATED_UTC={stamp}",
                "REPOSITORY_ROOT=/opt/gpu-control",
                f"GIT_HEAD={'2' * 40}",
                "POSTGRES_CONTAINER=gpu-control-postgres-1",
                "POSTGRES_USER=gpu_control",
                "POSTGRES_DB=gpu_control",
                "QUIESCE_CHECK=ENFORCED_PRE_AND_POST",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    quiesce = (
        "\n".join(
            [
                "active_jobs=0",
                "active_batches=0",
                "active_asset_jobs=0",
                "busy_nodes=0",
                "accepting_online_nodes=0",
            ]
        )
        + "\n"
    )
    special_payloads = {
        "database.dump": b"PGDMP-offline-test\n",
        "repository.bundle": b"# v2 git bundle\noffline-test\n",
        "git-head.txt": ("2" * 40 + "\n").encode(),
        "quiesce-gate-pre.txt": quiesce.encode(),
        "quiesce-gate-post.txt": quiesce.encode(),
    }
    for name in sorted(REQUIRED_FULL_BACKUP_PAYLOADS - {"BACKUP_MANIFEST"}):
        (backup_dir / name).write_bytes(
            special_payloads.get(name, f"offline fixture: {name}\n".encode())
        )
    sums = backup_dir / "SHA256SUMS"
    payload_paths = sorted(
        (path for path in backup_dir.iterdir() if path.name != "SHA256SUMS"),
        key=lambda path: path.name,
    )
    sums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in payload_paths
        ),
        encoding="utf-8",
    )
    marker = backup_dir / "BACKUP_COMPLETE"
    marker.write_text(
        "\n".join(
            [
                "STATUS=COMPLETE",
                f"CREATED_UTC={stamp}",
                "MODE=full",
                f"SHA256SUMS_SHA256={hashlib.sha256(sums.read_bytes()).hexdigest()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    backup_dir.chmod(0o700)
    for path in backup_dir.iterdir():
        path.chmod(0o600)
        os.utime(path, (created_at.timestamp(), created_at.timestamp()))
    os.utime(backup_dir, (created_at.timestamp(), created_at.timestamp()))
    return backup_dir


def test_default_runtime_is_plan_only_and_has_no_credentials() -> None:
    runtime = RuntimeSettings.from_environment({})
    assert runtime.allow_load_test is False
    assert runtime.api_keys == ()
    assert runtime.environment == "plan"


def test_locust_client_is_bound_to_approved_ca(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCUST_SKIP_MONKEY_PATCH", "true")
    monkeypatch.setenv("LOCUST_SKIP_URLLIB3_PATCH", "true")
    locust_clients = pytest.importorskip("locust.clients")
    locust_event = pytest.importorskip("locust.event")
    ca_file = tmp_path / "approved-ca.pem"
    ca_file.write_text("test-ca\n", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/tmp/unapproved-ca.pem")
    client = locust_clients.HttpSession(
        "https://load-target.invalid",
        locust_event.EventHook(),
        None,
    )

    verify = configure_locust_client_tls(client, ca_file)

    assert verify == str(ca_file)
    assert client.verify == str(ca_file)
    assert client.trust_env is False
    settings = client.merge_environment_settings(
        "https://load-target.invalid/health",
        {},
        None,
        None,
        None,
    )
    assert settings["verify"] == str(ca_file)


def test_approved_load_ca_fails_closed_when_missing_or_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TestClient:
        verify: bool | str = True
        trust_env = True

    client = TestClient()
    missing_ca = tmp_path / "missing-ca.pem"
    with pytest.raises(LoadTestConfigurationError, match="not a file"):
        configure_locust_client_tls(client, missing_ca)

    unreadable_ca = tmp_path / "unreadable-ca.pem"
    unreadable_ca.write_text("test-ca\n", encoding="utf-8")
    original_open = Path.open

    def deny_approved_ca(path: Path, *args: object, **kwargs: object) -> object:
        if path == unreadable_ca:
            raise PermissionError("denied for test")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_approved_ca)
    with pytest.raises(LoadTestConfigurationError, match="not readable"):
        configure_locust_client_tls(client, unreadable_ca)


def test_shape_stop_request_is_idempotent_and_shape_owned() -> None:
    stages = (LoadStage(users=10, duration_seconds=60, spawn_rate=2.0),)
    signal = LoadShapeStopSignal()

    assert signal.request("telemetry_sample_failed") is True
    assert signal.request("foreign_work_detected") is False
    assert signal.requested is True
    assert signal.reason == "telemetry_sample_failed"
    assert select_load_shape_stage(stages, 1.0, stop_requested=signal.requested) is None

    signal.reset()
    assert signal.requested is False
    assert select_load_shape_stage(stages, 1.0, stop_requested=False) == (10, 2.0)
    with pytest.raises(LoadTestConfigurationError, match="reason cannot be empty"):
        signal.request("  ")


def test_telemetry_never_quits_the_locust_runner_directly() -> None:
    source = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    telemetry_loop = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "telemetry_loop"
    )
    direct_quits = [
        node
        for node in ast.walk(telemetry_loop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quit"
    ]
    assert direct_quits == []


def test_locust_uses_status_scoped_recovery_and_sync_e2e_route_name() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    active_query = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "active_gpu_admin_jobs"
    )
    discovery = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "discover_teardown_records"
    )
    roughness = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "run_modelview_roughness"
    )

    active_query_source = ast.get_source_segment(source, active_query) or ""
    discovery_source = ast.get_source_segment(source, discovery) or ""
    roughness_source = ast.get_source_segment(source, roughness) or ""
    assert "client_kind={client_kind}&status={status}&limit=500" in active_query_source
    assert 'client_kind="test"' in discovery_source
    assert "passes=2" in discovery_source
    assert 'operation="sync-e2e"' in roughness_source


def test_teardown_cancel_retries_429_and_5xx_with_bounded_backoff() -> None:
    statuses = iter((429, 503, 200))
    sleeps: list[float] = []

    status_code, attempts = execute_bounded_teardown_cancel(
        lambda: next(statuses),
        sleeps.append,
    )

    assert (status_code, attempts) == (200, 3)
    assert sleeps == [0.25, 0.5]

    exhausted = iter((500, 502, 599))
    exhausted_sleeps: list[float] = []
    status_code, attempts = execute_bounded_teardown_cancel(
        lambda: next(exhausted),
        exhausted_sleeps.append,
    )
    assert (status_code, attempts) == (599, 3)
    assert exhausted_sleeps == [0.25, 0.5]


def test_wrapper_forces_safe_locust_stop_timeout(tmp_path: Path) -> None:
    runtime = RuntimeSettings.from_environment(allowed_environment(tmp_path))
    result_dir = tmp_path / "results"
    child_environment = locust_child_environment(
        runtime,
        tmp_path / "scenario.yaml",
        tmp_path / "fixtures.yaml",
        result_dir,
        source={"LOCUST_STOP_TIMEOUT": "21600", "PRESERVE_ME": "yes"},
    )
    command = locust_command(tmp_path / "locust", runtime.target, result_dir)

    assert child_environment["LOCUST_STOP_TIMEOUT"] == str(
        SAFE_LOCUST_STOP_TIMEOUT_SECONDS
    )
    assert child_environment["PRESERVE_ME"] == "yes"
    assert command.count("--stop-timeout") == 1
    stop_timeout_index = command.index("--stop-timeout")
    assert command[stop_timeout_index + 1] == str(SAFE_LOCUST_STOP_TIMEOUT_SECONDS)


def test_scheduler_capacity_v1_normalizes_new_and_legacy_aliases() -> None:
    legacy = normalize_scheduler_capacity_v1(
        {
            "schema_version": "1.0",
            "accepting": True,
            "cluster": {"queued_jobs": 3},
        }
    )
    assert legacy["accepting_batches"] is True
    assert legacy["queue_depth"] == 3

    current = normalize_scheduler_capacity_v1(
        {
            "schema_version": "1.0",
            "accepting_batches": False,
            "queue_depth": 4,
            "cluster": {},
        }
    )
    assert current["accepting"] is False
    assert current["cluster"]["queued_jobs"] == 4

    dual = normalize_scheduler_capacity_v1(
        {
            "schema_version": "1.0",
            "accepting": True,
            "accepting_batches": True,
            "queue_depth": 2,
            "cluster": {"queued_jobs": 2},
        }
    )
    assert dual["queue_depth"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": "2.0", "accepting": True, "cluster": {"queued_jobs": 0}},
        {"schema_version": "1.0", "accepting": True, "cluster": []},
        {"schema_version": "1.0", "accepting": 1, "cluster": {"queued_jobs": 0}},
        {"schema_version": "1.0", "accepting": True, "cluster": {}},
        {"schema_version": "1.0", "accepting": True, "cluster": {"queued_jobs": False}},
        {
            "schema_version": "1.0",
            "accepting": True,
            "accepting_batches": False,
            "cluster": {"queued_jobs": 0},
        },
        {
            "schema_version": "1.0",
            "accepting": True,
            "queue_depth": 1,
            "cluster": {"queued_jobs": 2},
        },
    ],
)
def test_scheduler_capacity_v1_rejects_invalid_or_conflicting_shapes(
    payload: object,
) -> None:
    with pytest.raises(LoadTestConfigurationError):
        normalize_scheduler_capacity_v1(payload)


def test_transport_failures_are_retryable_and_queue_prefers_queued_at() -> None:
    assert load_response_is_retryable(0) is True
    assert load_response_is_retryable(None) is True
    assert load_response_is_retryable(200, has_transport_error=True) is True
    assert load_response_is_retryable(408) is True
    assert load_response_is_retryable(429) is True
    assert load_response_is_retryable(503) is True
    assert load_response_is_retryable(200) is False
    assert load_response_is_retryable(422) is False
    assert (
        load_queue_start(
            {
                "created_at": "2026-07-30T00:00:00Z",
                "queued_at": "2026-07-30T00:00:05Z",
            }
        )
        == "2026-07-30T00:00:05Z"
    )
    assert load_queue_start({"created_at": "legacy"}) == "legacy"


def test_every_api_key_must_be_a_test_client_and_accepting() -> None:
    checks = validate_test_client_capacities(
        [
            {"client": {"kind": "test"}, "accepting_batches": True},
            {"client": {"kind": "test"}, "accepting_batches": True},
        ],
        expected_count=2,
    )
    assert [item["api_key_index"] for item in checks] == [0, 1]

    try:
        validate_test_client_capacities(
            [
                {"client": {"kind": "test"}, "accepting_batches": True},
                {"client": {"kind": "production"}, "accepting_batches": True},
            ],
            expected_count=2,
        )
    except LoadTestConfigurationError as exc:
        assert "index 1" in str(exc)
    else:
        raise AssertionError("every rotating key must be checked independently")


def test_cpu_and_substance_worker_capacity_are_independent() -> None:
    workers = [
        {
            "id": f"asset-worker-cpu-{index}",
            "status": "ONLINE",
            "current_jobs": 0,
            "max_concurrency": 2,
        }
        for index in range(3)
    ] + [
        {
            "id": "asset-worker-3090-b-windows-01",
            "status": "ONLINE",
            "current_jobs": 0,
            "max_concurrency": 1,
        }
    ]
    result = validate_asset_worker_roles(
        workers,
        minimum_cpu_workers=3,
        minimum_cpu_slots=1,
        minimum_substance_slots=1,
    )
    assert len(result["cpu_workers"]) == 3
    assert len(result["substance_workers"]) == 1
    assert result["cpu_available_slots"] == 6
    assert result["substance_available_slots"] == 1

    substance_only = [
        {
            "id": f"asset-worker-3090-b-windows-{index:02d}",
            "status": "ONLINE",
            "current_jobs": 0,
            "max_concurrency": 1,
        }
        for index in range(1, 5)
    ]
    try:
        validate_asset_worker_roles(
            substance_only,
            minimum_cpu_workers=3,
            minimum_cpu_slots=1,
            minimum_substance_slots=1,
        )
    except LoadTestConfigurationError as exc:
        assert "CPU asset workers" in str(exc)
    else:
        raise AssertionError("Substance slots must not satisfy the CPU worker gate")


def test_watchdog_uses_exact_tenant_allowlist_and_fails_closed() -> None:
    result = identify_foreign_active_work(
        [
            {
                "job_id": "own-gpu",
                "status": "RUNNING",
                "client_kind": "test",
                "tenant_id": "load-tenant",
            },
            {
                "job_id": "production-gpu",
                "status": "QUEUED",
                "client_kind": "production",
                "tenant_id": "business-tenant",
            },
            {
                "job_id": "finished-production",
                "status": "SUCCEEDED",
                "client_kind": "production",
                "tenant_id": "business-tenant",
            },
        ],
        [
            {"job_id": "own-asset", "status": "CLAIMED", "client_id": "load-tenant"},
            {"job_id": "foreign-asset", "status": "RUNNING", "client_id": "business"},
        ],
        test_tenant_ids=("load-tenant",),
    )
    assert [item["job_id"] for item in result["jobs"]] == [
        "production-gpu",
        "foreign-asset",
    ]
    assert result["detected"] is True

    fallback = identify_foreign_active_work(
        [{"job_id": "own-legacy-gpu", "status": "RUNNING", "tenant_id": "load-tenant"}],
        [],
        test_tenant_ids=("load-tenant",),
    )
    assert fallback["detected"] is False
    with pytest.raises(LoadTestConfigurationError, match="non-object"):
        identify_foreign_active_work(
            [],
            ["bad-row"],
            test_tenant_ids=("load-tenant",),  # type: ignore[list-item]
        )


def test_execution_requires_unique_tenant_id_for_every_load_key(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    environment = allowed_environment(tmp_path)
    environment.pop("LOAD_TEST_TENANT_IDS")
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    runtime = RuntimeSettings.from_environment(environment)
    blockers = runtime.execution_blockers(
        scenario, fixtures, repository_root=Path("/opt/gpu-control")
    )
    assert any("LOAD_TEST_TENANT_IDS" in blocker for blocker in blockers)

    environment["LOAD_TEST_TENANT_IDS"] = "duplicate,duplicate"
    with pytest.raises(LoadTestConfigurationError, match="one-to-one"):
        RuntimeSettings.from_environment(environment)


def test_lifecycle_gate_rejects_failures_timeouts_artifacts_and_teardown() -> None:
    success = {
        "id": "ok",
        "terminal_status": "SUCCEEDED",
        "artifact_count": 1,
    }
    assert evaluate_load_lifecycle([success], [])["passed"] is True

    cases = [
        [{"id": "failed", "terminal_status": "FAILED", "artifact_count": 1}],
        [{"id": "timeout", "terminal_status": None, "poll_timed_out": True}],
        [{"id": "missing", "terminal_status": "SUCCEEDED", "artifact_count": 0}],
        [
            {
                "id": "bad-sha",
                "terminal_status": "SUCCEEDED",
                "artifact_count": 1,
                "artifact_contract_failed": True,
            }
        ],
    ]
    for records in cases:
        assert evaluate_load_lifecycle(records, [])["passed"] is False
    teardown = [{"task_id": "active", "cancelled": True, "status_code": 200}]
    assert evaluate_load_lifecycle([success], teardown)["passed"] is False


def test_bounded_stress_requires_verified_subset_and_safe_settled_cleanup() -> None:
    records = [
        {
            "id": "verified",
            "terminal_status": "SUCCEEDED",
            "artifact_count": 1,
        },
        {"id": "still-active", "terminal_status": None, "artifact_count": 0},
    ]
    settled_cancel = [
        {
            "task_id": "still-active",
            "cancelled": True,
            "settled": True,
            "final_status": "CANCELLED",
        }
    ]

    result = evaluate_load_lifecycle(
        records,
        settled_cancel,
        mode="bounded_stress",
        recovery_scan_passed=True,
    )

    assert result["passed"] is True
    assert result["verified_successful"] == 1
    assert result["unresolved_incomplete_task_ids"] == []

    for rejected_status in ("FAILED", "REVIEW_REJECTED", "TIMED_OUT"):
        failed_cleanup = [
            {
                "task_id": "still-active",
                "cancelled": True,
                "settled": True,
                "final_status": rejected_status,
            }
        ]
        result = evaluate_load_lifecycle(
            records,
            failed_cleanup,
            mode="bounded_stress",
            recovery_scan_passed=True,
        )
        assert result["passed"] is False
        assert result["teardown_failed_task_ids"] == ["still-active"]

    result = evaluate_load_lifecycle(
        records,
        settled_cancel,
        mode="bounded_stress",
        recovery_scan_passed=False,
    )
    assert result["passed"] is False


def test_scoped_teardown_discovery_recovers_only_exact_run_owned_work() -> None:
    discovered = discover_scoped_teardown_tasks(
        [
            {
                "kind": "job",
                "job_id": "roughness-job",
                "tenant_id": "tenant-a",
                "client_kind": "test",
                "workflow_key": "modelview-roughness",
                "status": "RUNNING",
                "created_at": "2026-07-30T12:00:01Z",
            },
            {
                "kind": "batch",
                "job_id": "imageclip-batch",
                "tenant_id": "tenant-b",
                "client_kind": "test",
                "external_batch_id": "loadtest:run-01:imageclip_batch:00000001",
                "status": "QUEUED",
                "created_at": "2026-07-30T12:00:02Z",
            },
            {
                "kind": "job",
                "job_id": "foreign-job",
                "tenant_id": "production-tenant",
                "client_kind": "production",
                "workflow_key": "modelview-roughness",
                "status": "RUNNING",
                "created_at": "2026-07-30T12:00:03Z",
            },
        ],
        [
            {
                "job_id": "retopo-job",
                "client_id": "tenant-a",
                "external_asset_id": "loadtest:run-01:retopology_process:00000002",
                "job_type": "RETOPOLOGY_PROCESS_V1",
                "status": "CLAIMED",
                "created_at": "2026-07-30T12:00:04Z",
            }
        ],
        tenant_key_indices={"tenant-a": 0, "tenant-b": 1},
        session_id="run-01",
        started_at="2026-07-30T12:00:00Z",
    )

    assert [(item["api"], item["id"], item["api_key_index"]) for item in discovered] == [
        ("imageclip_batch", "imageclip-batch", 1),
        ("modelview_roughness", "roughness-job", 0),
        ("retopology_process", "retopo-job", 0),
    ]


def test_scoped_teardown_discovery_fails_closed_on_ambiguous_tenant_work() -> None:
    base = {
        "kind": "job",
        "job_id": "roughness-job",
        "tenant_id": "tenant-a",
        "client_kind": "test",
        "workflow_key": "modelview-roughness",
        "status": "RUNNING",
        "created_at": "2026-07-30T11:59:59Z",
    }
    with pytest.raises(LoadTestConfigurationError, match="pre-run active work"):
        discover_scoped_teardown_tasks(
            [base],
            [],
            tenant_key_indices={"tenant-a": 0},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )

    ambiguous_asset = {
        "job_id": "asset-job",
        "client_id": "tenant-a",
        "external_asset_id": "someone-elses-work",
        "job_type": "RETOPOLOGY_PROCESS_V1",
        "status": "RUNNING",
        "created_at": "2026-07-30T12:00:01Z",
    }
    with pytest.raises(LoadTestConfigurationError, match="ambiguous"):
        discover_scoped_teardown_tasks(
            [],
            [ambiguous_asset],
            tenant_key_indices={"tenant-a": 0},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )


def test_execution_requires_all_gates_and_external_valid_fixtures(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    runtime = RuntimeSettings.from_environment(allowed_environment(tmp_path))

    runtime.assert_execution_allowed(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
    )


def test_production_target_is_refused_even_with_confirmation(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    environment["LOAD_TEST_TARGET_ALLOWLIST"] = "https://10.3.34.11"
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    runtime = RuntimeSettings.from_environment(environment)

    try:
        runtime.assert_execution_allowed(
            scenario,
            fixtures,
            repository_root=Path("/opt/gpu-control"),
        )
    except LoadTestConfigurationError as exc:
        assert "LOAD_TEST_ENVIRONMENT=production" in str(exc)
        assert "ALLOW_PRODUCTION_LOAD_TEST" in str(exc)
    else:
        raise AssertionError("production target without extra gates must fail closed")


def test_production_target_allows_only_explicit_change_window_gates(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    now = datetime.now(UTC)
    environment.update(
        {
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
            "LOAD_TEST_CHANGE_ID": "CHG-20260730-LOAD",
            "LOAD_TEST_WINDOW_START": (now - timedelta(minutes=1)).isoformat(),
            "LOAD_TEST_WINDOW_END": (now + timedelta(hours=1)).isoformat(),
            "LOAD_TEST_BACKUP_DIR": str(
                complete_full_backup(tmp_path, now - timedelta(minutes=10))
            ),
        }
    )
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    runtime = RuntimeSettings.from_environment(environment)

    runtime.assert_execution_allowed(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        now=now,
    )


def test_confirmation_token_domains_and_exact_origin_allowlist(tmp_path: Path) -> None:
    nonproduction = allowed_environment(tmp_path)
    nonproduction_runtime = RuntimeSettings.from_environment(nonproduction)
    production = dict(nonproduction)
    production.update(
        {
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
            "LOAD_TEST_CHANGE_ID": "CHG-domain-test",
            "LOAD_TEST_WINDOW_START": "2026-07-30T01:00:00Z",
            "LOAD_TEST_WINDOW_END": "2026-07-30T02:00:00Z",
        }
    )
    production_runtime = RuntimeSettings.from_environment(production)

    assert (
        nonproduction_runtime.expected_confirmation_token
        != production_runtime.expected_confirmation_token
    )

    hostname_only = dict(nonproduction)
    hostname_only["LOAD_TEST_TARGET_ALLOWLIST"] = "staging.example"
    hostname_runtime = RuntimeSettings.from_environment(hostname_only)
    assert hostname_runtime.target_is_allowlisted() is False


def test_production_backup_requires_full_integrity_and_pre_window_age(
    tmp_path: Path,
) -> None:
    window_start = datetime.now(UTC)
    backup = complete_full_backup(tmp_path, window_start - timedelta(hours=1))

    evidence = validate_production_backup(
        backup,
        approved_window_start=window_start,
        max_age_hours=24,
    )

    assert evidence["status"] == "VERIFIED_FULL_PRE_WINDOW"
    assert evidence["payload_count"] == len(REQUIRED_FULL_BACKUP_PAYLOADS)

    (backup / "host-data.tar").write_text("tampered\n", encoding="utf-8")
    try:
        validate_production_backup(
            backup,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "checksum failed" in str(exc)
    else:
        raise AssertionError("tampered production backup must fail closed")


def test_production_backup_rejects_label_only_or_incomplete_full_set(
    tmp_path: Path,
) -> None:
    window_start = datetime.now(UTC)
    backup = complete_full_backup(tmp_path, window_start - timedelta(hours=1))
    (backup / "database.dump").unlink()

    try:
        validate_production_backup(
            backup,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "missing required payloads" in str(exc)
        assert "database.dump" in str(exc)
    else:
        raise AssertionError("MODE=full labels cannot replace required recovery payloads")


def test_production_backup_rejects_unenforced_quiesce_and_stale_snapshot(
    tmp_path: Path,
) -> None:
    window_start = datetime.now(UTC)
    backup = complete_full_backup(tmp_path, window_start - timedelta(hours=1))
    manifest = backup / "BACKUP_MANIFEST"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "QUIESCE_CHECK=ENFORCED_PRE_AND_POST",
            "QUIESCE_CHECK=NOT_ENFORCED",
        ),
        encoding="utf-8",
    )
    try:
        validate_production_backup(
            backup,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "enforced pre/post quiesce" in str(exc)
    else:
        raise AssertionError("an unquiesced production backup must fail closed")

    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    stale = complete_full_backup(stale_root, window_start - timedelta(hours=25))
    try:
        validate_production_backup(
            stale,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "older than max_backup_age_hours" in str(exc)
    else:
        raise AssertionError("an expired production backup must fail closed")


def test_production_backup_rejects_checksum_manifest_drift(tmp_path: Path) -> None:
    window_start = datetime.now(UTC)
    backup = complete_full_backup(tmp_path, window_start - timedelta(hours=1))
    marker = backup / "BACKUP_COMPLETE"
    marker.write_text(
        marker.read_text(encoding="utf-8").replace(
            "SHA256SUMS_SHA256=",
            f"SHA256SUMS_SHA256={'0' * 64}\nIGNORED=",
        ),
        encoding="utf-8",
    )
    try:
        validate_production_backup(
            backup,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "SHA256SUMS does not match BACKUP_COMPLETE" in str(exc)
    else:
        raise AssertionError("an unbound SHA256SUMS file must fail closed")


def test_production_backup_rejects_files_finalized_after_window(tmp_path: Path) -> None:
    window_start = datetime.now(UTC)
    created_at = window_start - timedelta(hours=1)
    backup = complete_full_backup(tmp_path, created_at)
    payload = backup / "host-data.tar"
    payload.write_text("changed after the approved window opened\n", encoding="utf-8")
    sums = backup / "SHA256SUMS"
    payload_paths = sorted(
        (
            path
            for path in backup.iterdir()
            if path.name not in {"SHA256SUMS", "BACKUP_COMPLETE"}
        ),
        key=lambda path: path.name,
    )
    sums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in payload_paths
        ),
        encoding="utf-8",
    )
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    marker = backup / "BACKUP_COMPLETE"
    marker.write_text(
        "\n".join(
            [
                "STATUS=COMPLETE",
                f"CREATED_UTC={stamp}",
                "MODE=full",
                f"SHA256SUMS_SHA256={hashlib.sha256(sums.read_bytes()).hexdigest()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    after_window = (window_start + timedelta(minutes=1)).timestamp()
    for path in (payload, sums, marker):
        os.utime(path, (after_window, after_window))

    try:
        validate_production_backup(
            backup,
            approved_window_start=window_start,
            max_age_hours=24,
        )
    except LoadTestConfigurationError as exc:
        assert "completely finalized before the approved window" in str(exc)
    else:
        raise AssertionError("a backup changed after window start must fail closed")


def test_scenario_requires_all_six_positive_weights_and_100_users(tmp_path: Path) -> None:
    payload = scenario_payload()
    del payload["weights"]["substance_bake"]
    try:
        load_scenario(write_yaml(tmp_path / "invalid.yaml", payload))
    except LoadTestConfigurationError as exc:
        assert "exactly six APIs" in str(exc)
    else:
        raise AssertionError("incomplete six-API mix must fail")

    payload = scenario_payload()
    payload["preflight"]["minimum_healthy_gpu_nodes"] = 2
    try:
        load_scenario(write_yaml(tmp_path / "two-gpus.yaml", payload))
    except LoadTestConfigurationError as exc:
        assert "at least three healthy GPU nodes" in str(exc)
    else:
        raise AssertionError("the three-host load plan must not shrink to two GPUs")

    payload = scenario_payload()
    payload["lifecycle_mode"] = "bounded_stress"
    payload["thresholds"]["sync_e2e_p95_ms"] = 600000
    scenario = load_scenario(write_yaml(tmp_path / "bounded.yaml", payload))
    assert scenario.lifecycle_mode == "bounded_stress"
    assert scenario.thresholds["sync_e2e_p95_ms"] == 600000

    payload["lifecycle_mode"] = "best_effort"
    with pytest.raises(LoadTestConfigurationError, match="lifecycle_mode"):
        load_scenario(write_yaml(tmp_path / "bad-mode.yaml", payload))


def test_plan_redacts_secrets_and_exposes_resource_mix(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    environment = allowed_environment(tmp_path)
    runtime = RuntimeSettings.from_environment(environment)
    plan = build_plan(
        runtime,
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
    )

    serialized = json.dumps(plan)
    assert "gpc_test_one" not in serialized
    assert "read-only-admin-token" not in serialized
    assert plan["secret_inventory"]["api_key_count"] == 2
    assert plan["scenario"]["resource_mix"] == {
        "gpu_consuming": 0.5,
        "cpu": 0.5,
    }


def test_summary_reports_required_percentiles_retries_and_nodes() -> None:
    summary = summarize_records(
        [
            {
                "api": "imageclip_batch",
                "terminal_status": "SUCCEEDED",
                "total_ms": 100,
                "queue_ms": 10,
                "node_id": "gpu-a",
                "retries": 1,
                "recovered": True,
            },
            {
                "api": "uv_process",
                "terminal_status": "FAILED",
                "total_ms": 400,
                "queue_ms": 40,
                "worker_id": "cpu-a",
                "retries": 0,
                "error_code": "UV_QA_FAILED",
            },
        ],
        elapsed_seconds=2,
    )

    assert summary["throughput_completed_per_second"] == 1
    assert summary["total_ms"] == {"p50": 100.0, "p90": 400.0, "p95": 400.0, "p99": 400.0}
    assert summary["queue_ms"]["p99"] == 40.0
    assert summary["retries"] == 1
    assert summary["recoveries"] == 1
    assert summary["nodes"] == {"gpu-a": 1}
    assert summary["workers"] == {"cpu-a": 1}
    assert summary["errors"] == {"UV_QA_FAILED": 1}


def test_thresholds_separate_sync_final_e2e_from_async_submit() -> None:
    summary = {
        "created": 10,
        "http_retry_attempts": 0,
        "queue_ms": {"p95": 1000},
        "http": {
            "total": {"failure_rate": 0.0},
            "entries": {
                "POST imageclip_batch:submit": {"p95_ms": 2500},
                "POST modelview_roughness:sync-e2e": {"p95_ms": 467000},
                "GET imageclip_batch:poll": {"p95_ms": 100},
                "GET imageclip_batch:artifact-download": {"p95_ms": 200},
            },
        },
    }
    thresholds = {
        "http_failure_rate_percent": 1,
        "submit_p95_ms": 3000,
        "sync_e2e_p95_ms": 600000,
        "poll_p95_ms": 1500,
        "artifact_p95_ms": 30000,
        "queue_p95_ms": 900000,
        "retry_rate_percent": 5,
    }

    result = evaluate_load_thresholds(summary, thresholds)

    assert result["passed"] is True
    assert result["observed"]["submit_p95_ms"] == 2500
    assert result["observed"]["sync_e2e_p95_ms"] == 467000
    assert result["route_classification"]["sync_end_to_end_api_names"] == ["modelview_roughness"]

    backward_compatible = dict(thresholds)
    backward_compatible.pop("sync_e2e_p95_ms")
    assert evaluate_load_thresholds(summary, backward_compatible)["passed"] is True


def test_telemetry_summary_reports_gpu_saturation_worker_slots_and_queue_peak() -> None:
    samples = []
    for sequence, (utilization, gpu_jobs, worker_jobs, queue_depth) in enumerate(
        [
            (10, 0, 0, 0),
            (90, 1, 2, 2),
            (95, 2, 4, 7),
            (100, 2, 3, 3),
        ],
        1,
    ):
        samples.append(
            {
                "valid": True,
                "sequence": sequence,
                "gpu_nodes": [
                    {
                        "id": "gpu-a",
                        "gpu_util_percent": utilization,
                        "free_vram_mb": 5000 - sequence * 1000,
                        "current_jobs": gpu_jobs,
                        "max_concurrency": 2,
                    }
                ],
                "asset_workers": [
                    {
                        "id": "cpu-a",
                        "current_jobs": worker_jobs,
                        "max_concurrency": 4,
                    }
                ],
                "scheduler": {
                    "queue_depth": queue_depth,
                    "cluster": {"used_slots": gpu_jobs, "available_slots": 2 - gpu_jobs},
                },
                "asset_capacity": {
                    "queued_jobs": sequence - 1,
                    "used_slots": worker_jobs,
                    "available_slots": 4 - worker_jobs,
                },
            }
        )

    summary = summarize_telemetry(
        samples,
        expected_gpu_ids=("gpu-a",),
        expected_worker_ids=("cpu-a",),
    )

    assert summary["gpu_nodes"]["gpu-a"]["gpu_util_percent"] == {
        "p50": 90.0,
        "p90": 100.0,
        "p95": 100.0,
        "max": 100.0,
    }
    assert summary["gpu_nodes"]["gpu-a"]["saturation_ge_90_percent_ratio"] == 0.75
    assert summary["gpu_nodes"]["gpu-a"]["free_vram_mb"]["minimum"] == 1000.0
    assert summary["asset_workers"]["cpu-a"]["slot_occupancy_percent"]["p95"] == 100.0
    assert summary["asset_workers"]["cpu-a"]["cpu_util_percent"] is None
    assert summary["cluster"]["queue_depth_peak"] == 7.0
    assert summary["cluster"]["asset_queue_depth_peak"] == 3.0
    assert summary["expected_resources"]["all_gpu_samples_present"] is True


def test_telemetry_evidence_uses_observed_window_and_explicit_tail() -> None:
    samples = [
        {
            "valid": True,
            "sequence": 1,
            "actual_elapsed_ms": 20,
            "final_sample": False,
        },
        {
            "valid": True,
            "sequence": 2,
            "actual_elapsed_ms": 5010,
            "final_sample": False,
        },
        {
            "valid": True,
            "sequence": 3,
            "actual_elapsed_ms": 9230,
            "final_sample": True,
        },
    ]
    resources = {
        "all_gpu_samples_present": True,
        "all_worker_samples_present": True,
    }

    evidence = evaluate_telemetry_evidence(
        samples,
        expected_resources=resources,
        sampling_interval_seconds=5,
    )

    assert evidence["passed"] is True
    assert evidence["observed_window_seconds"] == 9.21
    assert evidence["maximum_gap_ms"] == 4990

    no_tail = [dict(sample) for sample in samples]
    no_tail[-1]["final_sample"] = False
    assert (
        evaluate_telemetry_evidence(
            no_tail,
            expected_resources=resources,
            sampling_interval_seconds=5,
        )["passed"]
        is False
    )

    gap = [dict(sample) for sample in samples]
    gap[-1]["actual_elapsed_ms"] = 20000
    assert (
        evaluate_telemetry_evidence(
            gap,
            expected_resources=resources,
            sampling_interval_seconds=5,
        )["passed"]
        is False
    )
