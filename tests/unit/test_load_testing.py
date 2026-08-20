import ast
import hashlib
import json
import os
import signal
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from packages.gpu_control_core.load_testing import (
    API_NAMES,
    FIXED_LOAD_ARTIFACT_KINDS,
    MINIMUM_PRODUCTION_LOAD_IDENTITIES,
    PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS,
    PRODUCTION_TEARDOWN_RESERVE_SECONDS,
    REQUIRED_FULL_BACKUP_PAYLOADS,
    RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS,
    SUBSTANCE_LOAD_ARTIFACT_KINDS,
    LoadShapeStopSignal,
    LoadStage,
    LoadTestConfigurationError,
    LoadTestPreempted,
    RuntimeSettings,
    build_load_artifact_evidence,
    build_plan,
    capture_load_evidence_json,
    configure_locust_client_tls,
    copy_load_evidence_json,
    discover_scoped_teardown_tasks,
    evaluate_load_lifecycle,
    evaluate_load_thresholds,
    evaluate_telemetry_evidence,
    execute_bounded_teardown_cancel,
    expected_load_artifact_kinds,
    find_load_session_identity_collisions,
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
    validate_downloaded_load_artifact,
    validate_load_artifact_manifest,
    validate_load_service_provenance,
    validate_production_backup,
    validate_test_client_capacities,
    verify_live_load_deployment,
    verify_remote_load_release_evidence,
    write_result_manifest,
)
from scripts.run_six_api_load import (
    SAFE_LOCUST_INTERRUPT_GRACE_SECONDS,
    SAFE_LOCUST_STOP_TIMEOUT_SECONDS,
    locust_child_environment,
    locust_command,
    run_locust_process,
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
            "modelview-inpaint": {
                "version": "approved-inpaint",
                "template_sha256": "5" * 64,
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
        "inpaint.png": b"png",
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
                "modelview_inpaint": {"image": str(tmp_path / "inpaint.png")},
                "modelview_roughness": {"image": str(tmp_path / "roughness.png")},
                "uv_process": {
                    "asset": str(tmp_path / "asset.fbx"),
                    "metadata": str(tmp_path / "uv.json"),
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
        "LOAD_TEST_SESSION_ID": "123e4567-e89b-42d3-a456-426614174000",
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


def add_target_release_identity(environment: dict[str, str]) -> None:
    environment.update(
        {
            "LOAD_TEST_SOURCE_REVISION": "a" * 40,
            "LOAD_TEST_API_IMAGE_DIGEST": f"sha256:{'1' * 64}",
            "LOAD_TEST_SCHEDULER_IMAGE_DIGEST": f"sha256:{'2' * 64}",
            "LOAD_TEST_ASSET_API_IMAGE_DIGEST": f"sha256:{'3' * 64}",
            "LOAD_TEST_WEB_IMAGE_DIGEST": f"sha256:{'4' * 64}",
            "LOAD_TEST_WORKER_IMAGE_DIGEST": f"sha256:{'5' * 64}",
            "LOAD_TEST_RELEASE_EVIDENCE_COMMIT": "b" * 40,
            "LOAD_TEST_RELEASE_EVIDENCE_PATH": (
                "artifacts/control-plane/1.5.9/deployment/live-deployment-receipt.json"
            ),
            "LOAD_TEST_RELEASE_EVIDENCE_SHA256": "c" * 64,
            "LOAD_TEST_SUBSTANCE_AGENT_SHA256": "d" * 64,
        }
    )


def verified_release_evidence(runtime: RuntimeSettings) -> dict[str, object]:
    return {
        "schema_version": "gpu-control-load-release-evidence-verification.v1",
        "verified": True,
        "origin_url": "https://github.com/kiana0512/gpu_control.git",
        "remote_ref": "refs/heads/main",
        "evidence_commit": runtime.release_evidence_commit,
        "evidence_path": runtime.release_evidence_path,
        "evidence_sha256": runtime.release_evidence_sha256,
        "source_revision": runtime.source_revision,
        "images": {
            component: {"local_image_id": digest}
            for component, digest in runtime.target_release_identity["image_digests"].items()
        },
        "deployment_inventory": runtime.target_release_identity["deployment_inventory"],
        "substance_agent": runtime.target_release_identity["substance_agent"],
    }


def verified_live_deployment(runtime: RuntimeSettings) -> dict[str, object]:
    return {
        "schema_version": "gpu-control-load-live-deployment-verification.v1",
        "verified": True,
        "release_evidence_commit": runtime.release_evidence_commit,
        "source_revision": runtime.source_revision,
        "inventory": runtime.target_release_identity["deployment_inventory"],
        "substance_agent": runtime.target_release_identity["substance_agent"],
    }


def add_production_load_identities(environment: dict[str, str], count: int = 12) -> None:
    environment["LOAD_TEST_API_KEYS"] = ",".join(f"gpc_test_{index:02d}" for index in range(count))
    environment["LOAD_TEST_TENANT_IDS"] = ",".join(f"tenant-{index:02d}" for index in range(count))


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


def test_six_api_artifact_contracts_match_server_contracts() -> None:
    assert FIXED_LOAD_ARTIFACT_KINDS == {
        "imageclip_batch": frozenset({"result_archive"}),
        "modelview_inpaint": frozenset({"output"}),
        "modelview_roughness": frozenset({"output"}),
        "uv_process": frozenset({"blend", "fbx", "report", "qa", "fbx_qa"}),
        "retopology_audit": frozenset({"audit", "manifest"}),
    }
    assert RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS == frozenset(
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
    assert (
        expected_load_artifact_kinds(
            "retopology_process", metadata={"reference_views": [{"filename": "front.png"}]}
        )
        == RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS
    )
    assert (
        expected_load_artifact_kinds("retopology_process", metadata={"reference_views": []})
        == RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS
    )
    for profile, kinds in SUBSTANCE_LOAD_ARTIFACT_KINDS.items():
        assert (
            expected_load_artifact_kinds(
                "substance_bake", metadata={"options": {"profile": profile}}
            )
            == kinds
        )

    root = Path(__file__).resolve().parents[2]
    asset_source = (root / "apps/asset_api/src/gpu_control_asset_api/main.py").read_text(
        encoding="utf-8"
    )
    asset_module = ast.parse(asset_source)
    assignments = {
        node.targets[0].id: node.value
        for node in asset_module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    server_audit = ast.literal_eval(assignments["RETOPOLOGY_AUDIT_ARTIFACTS"])
    assert frozenset(server_audit) == FIXED_LOAD_ARTIFACT_KINDS["retopology_audit"]
    server_substance = ast.literal_eval(assignments["SUBSTANCE_BAKE_OUTPUTS"])
    assert {
        profile: frozenset(contract) for profile, contract in server_substance.items()
    } == SUBSTANCE_LOAD_ARTIFACT_KINDS

    direct_v2 = ast.literal_eval(assignments["RETOPOLOGY_DIRECT_V2_ARTIFACTS"])
    assert frozenset(direct_v2) == RETOPOLOGY_PROCESS_LOAD_ARTIFACT_KINDS

    uv_complete = next(
        node
        for node in ast.walk(asset_module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "worker_complete_uv_v2"
    )
    uv_contract = next(
        node.value
        for node in uv_complete.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "contract" for target in node.targets)
    )
    assert isinstance(uv_contract, ast.Dict)
    assert {ast.literal_eval(key) for key in uv_contract.keys if key is not None} == set(
        FIXED_LOAD_ARTIFACT_KINDS["uv_process"]
    )
    scheduler_source = (root / "apps/scheduler/src/gpu_control_scheduler/main.py").read_text(
        encoding="utf-8"
    )
    assert 'kind="result_archive"' in scheduler_source
    assert 'kind="output"' in scheduler_source


def test_artifact_manifest_requires_exact_unique_positive_contract() -> None:
    def artifact(kind: str) -> dict[str, object]:
        return {
            "id": f"artifact-{kind}",
            "kind": kind,
            "filename": f"{kind}.bin",
            "size_bytes": 7,
            "sha256": "a" * 64,
            "download_url": f"/api/v1/assets/jobs/job/artifacts/{kind}",
        }

    expected = FIXED_LOAD_ARTIFACT_KINDS["uv_process"]
    valid = [artifact(kind) for kind in sorted(expected)]
    assert len(
        validate_load_artifact_manifest("uv_process", valid, expected_kinds=expected)
    ) == len(expected)

    with pytest.raises(LoadTestConfigurationError, match="kinds/cardinality"):
        validate_load_artifact_manifest("uv_process", valid[:-1], expected_kinds=expected)
    with pytest.raises(LoadTestConfigurationError, match="not unique"):
        validate_load_artifact_manifest("uv_process", [*valid, valid[0]], expected_kinds=expected)
    empty = [dict(item) for item in valid]
    empty[0]["size_bytes"] = 0
    with pytest.raises(LoadTestConfigurationError, match="non-positive"):
        validate_load_artifact_manifest("uv_process", empty, expected_kinds=expected)

    roughness = artifact("output")
    roughness.pop("filename")
    assert validate_load_artifact_manifest(
        "modelview_roughness", [roughness], expected_kinds={"output"}
    )


def test_downloaded_artifact_requires_size_and_three_matching_sha_values() -> None:
    content = b"verified artifact"
    digest = hashlib.sha256(content).hexdigest()
    artifact = {"kind": "output", "size_bytes": len(content), "sha256": digest}
    assert (
        validate_downloaded_load_artifact(
            "modelview_roughness", artifact, content, header_sha256=digest
        )
        == digest
    )

    for mutation, header, message in (
        ({**artifact, "size_bytes": len(content) + 1}, digest, "size"),
        (artifact, None, "header"),
        (artifact, "b" * 64, "header"),
        ({**artifact, "sha256": "b" * 64}, "b" * 64, "body"),
    ):
        with pytest.raises(LoadTestConfigurationError, match=message):
            validate_downloaded_load_artifact(
                "modelview_roughness", mutation, content, header_sha256=header
            )


def test_artifact_manifest_rejects_query_bearing_download_urls() -> None:
    artifact = {
        "id": "artifact-output",
        "kind": "output",
        "size_bytes": 7,
        "sha256": "a" * 64,
        "download_url": "/api/v1/jobs/job/artifacts/output?token=secret",
    }

    with pytest.raises(LoadTestConfigurationError, match="non-local"):
        validate_load_artifact_manifest(
            "modelview_roughness", [artifact], expected_kinds={"output"}
        )


def test_raw_load_evidence_is_deep_copied_and_rejects_credentials() -> None:
    payload = {"status": "SUCCEEDED", "artifacts": [{"kind": "output"}]}
    copied = copy_load_evidence_json(payload)
    payload["artifacts"][0]["kind"] = "mutated"

    assert copied["artifacts"][0]["kind"] == "output"
    with pytest.raises(LoadTestConfigurationError, match="credential"):
        copy_load_evidence_json({"status": "SUCCEEDED", "authorization": "secret"})

    captured, error = capture_load_evidence_json({"status": "SUCCEEDED", "authorization": "secret"})
    assert captured is None
    assert error is not None and "credential" in error

    captured, error = capture_load_evidence_json(
        {
            "artifacts": [
                {
                    "download_url": "/api/v1/jobs/job/artifacts/output?token=secret",
                }
            ]
        }
    )
    assert captured is None
    assert error is not None and "query/fragment-bearing URL" in error


def test_artifact_evidence_records_identity_metadata_header_and_body_proof() -> None:
    body = b"artifact-body"
    digest = hashlib.sha256(body).hexdigest()
    artifact = {
        "id": "artifact-123",
        "kind": "blend",
        "filename": "result.blend",
        "size_bytes": len(body),
        "sha256": digest,
    }

    evidence = build_load_artifact_evidence(
        "uv_process",
        artifact,
        header_sha256=digest,
        body_sha256=digest,
        body_size_bytes=len(body),
    )

    assert evidence == {
        "kind": "blend",
        "id": "artifact-123",
        "filename": "result.blend",
        "metadata_size_bytes": len(body),
        "metadata_sha256": digest,
        "x_artifact_sha256": digest,
        "body_size_bytes": len(body),
        "body_sha256": digest,
    }
    with pytest.raises(LoadTestConfigurationError, match="SHA-256"):
        build_load_artifact_evidence(
            "uv_process",
            artifact,
            header_sha256="b" * 64,
            body_sha256=digest,
            body_size_bytes=len(body),
        )


def test_load_service_provenance_must_match_the_planned_revision() -> None:
    revision = "a" * 40
    api = {
        "component": "api",
        "package_version": "1.5.9",
        "build_version": "1.5.9",
        "source_revision": revision,
        "version_aligned": True,
        "provenance_complete": True,
    }
    asset_api = {**api, "component": "asset-api"}

    assert validate_load_service_provenance(
        api,
        asset_api,
        expected_revision=revision,
    ) == {"api": api, "asset-api": asset_api}

    with pytest.raises(LoadTestConfigurationError, match="live source revision"):
        validate_load_service_provenance(
            api,
            {**asset_api, "source_revision": "b" * 40},
            expected_revision=revision,
        )

    with pytest.raises(LoadTestConfigurationError, match="provenance"):
        validate_load_service_provenance(
            {**api, "provenance_complete": False},
            asset_api,
            expected_revision=revision,
        )


SUBSTANCE_SCRIPT_BLOB = b"# signed Windows Substance Agent v6 fixture\n"


def candidate_evidence_blob(runtime: RuntimeSettings) -> bytes:
    image_specs = {
        "api": ("api", "gpu-control-api", "GPU Control API", "1.5.9"),
        "scheduler": (
            "scheduler",
            "gpu-control-scheduler",
            "GPU Control Scheduler",
            "1.5.9",
        ),
        "asset_api": (
            "asset-api",
            "unified-scheduler-asset-api",
            "GPU Control Asset API",
            "1.5.9",
        ),
        "web": ("web", "gpu-control-web", "GPU Control Web", "1.5.9"),
        "worker": (
            "blender-worker",
            "li3d/blender-worker",
            "GPU Control Blender Worker",
            "1.2.5",
        ),
    }
    images: dict[str, object] = {}
    offline: dict[str, object] = {}
    for index, (runtime_key, specification) in enumerate(image_specs.items(), start=6):
        evidence_key, repository, title, version = specification
        digest = runtime.target_release_identity["image_digests"][runtime_key]
        manifest_digest = f"sha256:{hex(index + 4)[-1] * 64}"
        config_digest = f"sha256:{str(index)[-1] * 64}"
        images[evidence_key] = {
            "reference": f"{repository}:{version}",
            "local_image_id": digest,
            "oci_image_manifest_digest": manifest_digest,
            "local_image_id_semantics": ("ENGINE_LOCAL_CONTENT_ID_NOT_ASSUMED_CONFIG_DIGEST"),
            "oci_config_digest": config_digest,
            "docker_archive_config_digest": config_digest,
            "docker_oci_config_match": True,
            "registry_manifest_digest": "PENDING_REGISTRY_PUSH",
            "oci_labels": {
                "org.opencontainers.image.title": title,
                "org.opencontainers.image.version": version,
                "org.opencontainers.image.revision": runtime.source_revision,
                "org.opencontainers.image.source": ("https://github.com/kiana0512/gpu_control.git"),
            },
        }
        offline[evidence_key] = {
            "oci_image_manifest_digest": manifest_digest,
            "oci_config_digest": config_digest,
            "docker_archive_config_digest": config_digest,
            "docker_oci_config_match": True,
        }
    payload = {
        "schema_version": "gpu-control-release-candidate.v2",
        "release_status": "CANDIDATE_ARCHIVE_ONLY",
        "deployed": False,
        "production_accepted": False,
        "version": "1.5.9",
        "worker_version": "1.2.5",
        "revision": runtime.source_revision,
        "source": {
            "repository": "https://github.com/kiana0512/gpu_control.git",
            "remote_ref": "origin/main",
            "remote_sha": runtime.source_revision,
        },
        "images": images,
        "offline_oci_exports": offline,
        "attestations": {"provenance_status": "VERIFIED_OFFLINE_OCI"},
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def live_deployment_receipt_blob(
    runtime: RuntimeSettings,
    candidate_blob: bytes,
    substance_script_blob: bytes,
) -> bytes:
    candidate = json.loads(candidate_blob)
    components: dict[str, object] = {}
    component_keys = {
        "api": "api",
        "scheduler": "scheduler",
        "asset_api": "asset-api",
        "web": "web",
        "worker": "blender-worker",
    }
    for runtime_key, evidence_key in component_keys.items():
        image = candidate["images"][evidence_key]
        components[runtime_key] = {
            "evidence_component": evidence_key,
            "reference": image["reference"],
            "identity_type": "docker_local_image_id+offline_oci_manifest_and_config",
            "local_image_id": image["local_image_id"],
            "oci_image_manifest_digest": image["oci_image_manifest_digest"],
            "oci_config_digest": image["oci_config_digest"],
        }
    substance_agent = dict(runtime.target_release_identity["substance_agent"])
    substance_agent["repository_script_sha256"] = hashlib.sha256(substance_script_blob).hexdigest()
    payload = {
        "schema_version": "gpu-control-live-deployment.v1",
        "deployment_status": "DEPLOYED_NOT_ACCEPTED",
        "deployed": True,
        "production_accepted": False,
        "source_revision": runtime.source_revision,
        "source": {
            "repository": "https://github.com/kiana0512/gpu_control.git",
            "revision": runtime.source_revision,
        },
        "candidate_evidence": {
            "path": ("artifacts/control-plane/1.5.9/release-parts/release-candidate-evidence.json"),
            "sha256": hashlib.sha256(candidate_blob).hexdigest(),
        },
        "components": components,
        "inventory": runtime.target_release_identity["deployment_inventory"],
        "substance_agent": substance_agent,
    }
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def test_production_release_identity_is_anchored_to_remote_git_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    environment["LOAD_TEST_SUBSTANCE_AGENT_SHA256"] = hashlib.sha256(
        SUBSTANCE_SCRIPT_BLOB
    ).hexdigest()
    provisional = RuntimeSettings.from_environment(environment)
    candidate_blob = candidate_evidence_blob(provisional)
    receipt_blob = live_deployment_receipt_blob(
        provisional,
        candidate_blob,
        SUBSTANCE_SCRIPT_BLOB,
    )
    environment["LOAD_TEST_RELEASE_EVIDENCE_SHA256"] = hashlib.sha256(receipt_blob).hexdigest()
    runtime = RuntimeSettings.from_environment(environment)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        assert kwargs == {"check": False, "capture_output": True, "timeout": 30}
        operation = command[3:]
        if operation == ["remote", "get-url", "origin"]:
            stdout = b"https://github.com/kiana0512/gpu_control.git\n"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
        if operation == [
            "ls-remote",
            "--exit-code",
            "--refs",
            "origin",
            "refs/heads/main",
        ]:
            stdout = f"{runtime.release_evidence_commit}\trefs/heads/main\n".encode()
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
        if operation == ["rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{runtime.release_evidence_commit}\n".encode(),
                stderr=b"",
            )
        if operation == ["status", "--porcelain=v1", "--untracked-files=no"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        if operation[:3] == ["show", "--no-ext-diff", "--no-textconv"]:
            object_name = operation[3]
            if object_name.endswith(runtime.release_evidence_path):
                stdout = receipt_blob
            elif object_name.endswith("release-candidate-evidence.json"):
                stdout = candidate_blob
            elif object_name.endswith("Invoke-GPUControlSubstanceAgent.ps1"):
                stdout = SUBSTANCE_SCRIPT_BLOB
            else:
                raise AssertionError(f"unexpected Git object: {object_name}")
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
        if operation == [
            "merge-base",
            "--is-ancestor",
            runtime.source_revision,
            runtime.release_evidence_commit,
        ]:
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected Git command: {operation}")

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        fake_run,
    )

    verified = verify_remote_load_release_evidence(tmp_path, runtime)

    assert verified["verified"] is True
    assert verified["evidence_commit"] == runtime.release_evidence_commit
    assert verified["source_revision"] == runtime.source_revision
    assert verified["images"]["worker"]["local_image_id"] == runtime.worker_image_digest
    assert [command[3] for command in commands] == [
        "remote",
        "ls-remote",
        "rev-parse",
        "status",
        "show",
        "merge-base",
        "show",
        "show",
    ]
    assert all("shell" not in command for command in commands)


def test_remote_release_evidence_rejects_candidate_as_live_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    environment["LOAD_TEST_RELEASE_EVIDENCE_PATH"] = (
        "artifacts/control-plane/1.5.9/release-parts/release-candidate-evidence.json"
    )
    runtime = RuntimeSettings.from_environment(environment)

    def unexpected_run(*_: object, **__: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("Git must not run for a direct candidate anchor")

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        unexpected_run,
    )
    with pytest.raises(LoadTestConfigurationError, match="live deployment receipt"):
        verify_remote_load_release_evidence(tmp_path, runtime)


def test_remote_release_evidence_rejects_environment_digest_self_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    environment["LOAD_TEST_SUBSTANCE_AGENT_SHA256"] = hashlib.sha256(
        SUBSTANCE_SCRIPT_BLOB
    ).hexdigest()
    provisional = RuntimeSettings.from_environment(environment)
    candidate_blob = candidate_evidence_blob(provisional)
    receipt_blob = live_deployment_receipt_blob(
        provisional,
        candidate_blob,
        SUBSTANCE_SCRIPT_BLOB,
    )
    environment["LOAD_TEST_RELEASE_EVIDENCE_SHA256"] = hashlib.sha256(receipt_blob).hexdigest()
    environment["LOAD_TEST_WORKER_IMAGE_DIGEST"] = f"sha256:{'f' * 64}"
    runtime = RuntimeSettings.from_environment(environment)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        operation = command[3:]
        if operation[0] == "remote":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"https://github.com/kiana0512/gpu_control.git\n",
                stderr=b"",
            )
        if operation[0] == "ls-remote":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{runtime.release_evidence_commit}\trefs/heads/main\n".encode(),
                stderr=b"",
            )
        if operation[0] == "rev-parse":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{runtime.release_evidence_commit}\n".encode(),
                stderr=b"",
            )
        if operation[0] == "status":
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        if operation[0] == "show":
            object_name = operation[3]
            if object_name.endswith(runtime.release_evidence_path):
                stdout = receipt_blob
            elif object_name.endswith("release-candidate-evidence.json"):
                stdout = candidate_blob
            else:
                stdout = SUBSTANCE_SCRIPT_BLOB
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        fake_run,
    )

    with pytest.raises(LoadTestConfigurationError, match="worker.*remote evidence"):
        verify_remote_load_release_evidence(tmp_path, runtime)


def test_remote_release_evidence_requires_origin_main_tip_and_blob_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    runtime = RuntimeSettings.from_environment(environment)

    def stale_remote(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        operation = command[3:]
        stdout = (
            b"https://github.com/kiana0512/gpu_control.git\n"
            if operation[0] == "remote"
            else f"{'d' * 40}\trefs/heads/main\n".encode()
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        stale_remote,
    )
    with pytest.raises(LoadTestConfigurationError, match="current origin/main"):
        verify_remote_load_release_evidence(tmp_path, runtime)

    def tampered_blob(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        operation = command[3:]
        if operation[0] == "remote":
            stdout = b"https://github.com/kiana0512/gpu_control.git\n"
        elif operation[0] == "ls-remote":
            stdout = f"{runtime.release_evidence_commit}\trefs/heads/main\n".encode()
        elif operation[0] == "rev-parse":
            stdout = f"{runtime.release_evidence_commit}\n".encode()
        elif operation[0] == "status":
            stdout = b""
        else:
            stdout = b'{"tampered":true}\n'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        tampered_blob,
    )
    with pytest.raises(LoadTestConfigurationError, match="SHA-256"):
        verify_remote_load_release_evidence(tmp_path, runtime)


def test_live_deployment_inventory_uses_fixed_docker_and_ssh_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    runtime = RuntimeSettings.from_environment(environment)
    release = verified_release_evidence(runtime)
    expected_inventory = runtime.target_release_identity["deployment_inventory"]
    expected_rows = list(expected_inventory.items())
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        ordinal = len(commands)
        commands.append(command)
        assert kwargs == {"check": False, "capture_output": True, "timeout": 20}
        if ordinal == 7:
            assert command[-2:] == [
                "/usr/bin/sha256sum",
                "/mnt/d/GPUControl/agent/Invoke-GPUControlSubstanceAgent.ps1",
            ]
            stdout = (
                f"{runtime.substance_agent_sha256}  "
                "/mnt/d/GPUControl/agent/Invoke-GPUControlSubstanceAgent.ps1\n"
            ).encode()
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")
        row = expected_rows[ordinal]
        assert command[-1] == row[1]["container_name"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{row[1]['image_id']}\n".encode(),
            stderr=b"",
        )

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        fake_run,
    )

    verified = verify_live_load_deployment(runtime, release)

    assert verified == verified_live_deployment(runtime)
    assert len(commands) == 8
    assert all(command[0] == "/usr/bin/docker" for command in commands[:5])
    assert commands[5] == [
        "/usr/bin/ssh",
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
        "/usr/bin/docker",
        "inspect",
        "--type",
        "container",
        "--format={{.Image}}",
        "gpu-control-node-blender-worker-1",
    ]
    assert commands[6][10:12] == ["gpucontrol@10.3.34.14", "/usr/bin/docker"]


def test_live_deployment_inventory_rejects_one_mismatched_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = allowed_environment(tmp_path)
    add_target_release_identity(environment)
    runtime = RuntimeSettings.from_environment(environment)

    def wrong_image(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"sha256:{'f' * 64}\n".encode(),
            stderr=b"",
        )

    monkeypatch.setattr(
        "packages.gpu_control_core.load_testing.subprocess.run",
        wrong_image,
    )

    with pytest.raises(LoadTestConfigurationError, match="control-api.*remote"):
        verify_live_load_deployment(runtime, verified_release_evidence(runtime))


def test_session_collision_scan_uses_exact_current_tenant_identities() -> None:
    session_id = "123e4567-e89b-42d3-a456-426614174000"
    collisions = find_load_session_identity_collisions(
        [
            {
                "kind": "batch",
                "batch_id": "batch-1",
                "tenant_id": "tenant-a",
                "external_batch_id": f"loadtest:{session_id}:imageclip_batch:00000001",
                "status": "SUCCEEDED",
            },
            {
                "kind": "job",
                "job_id": "job-1",
                "tenant_id": "tenant-b",
                "request_id": f"lt:{session_id}:mvr:00000002",
                "status": "SUCCEEDED",
            },
            {
                "kind": "job",
                "job_id": "inpaint-job-1",
                "tenant_id": "tenant-a",
                "request_id": f"lt:{session_id}:mvi:00000003",
                "status": "SUCCEEDED",
            },
            {
                "kind": "batch",
                "batch_id": "ignored-cross-tenant",
                "tenant_id": "business",
                "external_batch_id": f"loadtest:{session_id}:imageclip_batch:00000003",
            },
        ],
        [
            {
                "job_id": "asset-1",
                "client_id": "tenant-a",
                "external_asset_id": f"loadtest:{session_id}:uv_process:00000004",
                "status": "FAILED",
            },
            {
                "job_id": "ignored-cross-session",
                "client_id": "tenant-a",
                "external_asset_id": "loadtest:another-session:uv_process:00000004",
            },
        ],
        tenant_ids=("tenant-a", "tenant-b"),
        session_id=session_id,
    )
    assert {(item["plane"], item["task_id"]) for item in collisions} == {
        ("gpu", "batch-1"),
        ("gpu", "job-1"),
        ("gpu", "inpaint-job-1"),
        ("asset", "asset-1"),
    }


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
    with pytest.raises(LoadTestPreempted) as stopped:
        signal.raise_if_requested("imageclip_batch:submit")
    assert stopped.value.reason == "telemetry_sample_failed"
    assert stopped.value.operation == "imageclip_batch:submit"

    signal.reset()
    assert signal.requested is False
    signal.raise_if_requested("imageclip_batch:submit")
    assert select_load_shape_stage(stages, 1.0, stop_requested=False) == (10, 2.0)
    with pytest.raises(LoadTestConfigurationError, match="reason cannot be empty"):
        signal.request("  ")
    with pytest.raises(LoadTestConfigurationError, match="operation cannot be empty"):
        signal.raise_if_requested("  ")


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


def test_watchdog_fences_before_requesting_immediate_runner_stop() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    watchdog = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "handle_telemetry_watchdog"
    )
    watchdog_source = ast.get_source_segment(source, watchdog) or ""

    assert '_shape_stop_signal.request("foreign_work_detected")' in watchdog_source
    assert "request_immediate_runner_stop(environment)" in watchdog_source
    assert watchdog_source.index("_shape_stop_signal.request") < watchdog_source.index(
        "request_immediate_runner_stop"
    )


def test_telemetry_shutdown_drains_inflight_sample_before_fallback_kill() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    telemetry_loop = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "telemetry_loop"
    )
    stop_telemetry = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "stop_telemetry"
    )
    loop_source = ast.get_source_segment(source, telemetry_loop) or ""
    stop_source = ast.get_source_segment(source, stop_telemetry) or ""

    assert "if _telemetry_stop:" in loop_source
    assert ".join(timeout=TELEMETRY_SHUTDOWN_GRACE_SECONDS)" in stop_source
    assert stop_source.index(".join(") < stop_source.index(".kill(")


def test_locust_uses_status_scoped_recovery_and_sync_e2e_route_name() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    active_query = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "active_gpu_admin_jobs"
    )
    status_sender = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "admin_status_query_sender"
    )
    discovery = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "discover_teardown_records"
    )
    sync_image = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "run_sync_image_service"
    )

    active_query_source = ast.get_source_segment(source, active_query) or ""
    status_sender_source = ast.get_source_segment(source, status_sender) or ""
    discovery_source = ast.get_source_segment(source, discovery) or ""
    sync_image_source = ast.get_source_segment(source, sync_image) or ""
    assert "client_kind={client_kind}&active_only=true&limit=500" in status_sender_source
    assert "execute_bounded_teardown_cancel" in active_query_source
    assert "ADMIN_STATUS_QUERY_THROTTLE_SECONDS" in active_query_source
    assert 'client_kind="test"' in discovery_source
    assert "passes=2" in discovery_source
    assert 'operation="sync-e2e"' in sync_image_source


def test_formal_preflight_rejects_distributed_locust_before_network_preflight() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    guarded = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "guarded_preflight"
    )
    guarded_source = ast.get_source_segment(source, guarded) or ""

    assert "MasterRunner, WorkerRunner" in source
    assert "isinstance(runner, MasterRunner | WorkerRunner)" in guarded_source
    assert guarded_source.index("MasterRunner") < guarded_source.index("perform_preflight()")


def test_production_preflight_scans_all_status_history_before_submissions() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    preflight = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "perform_preflight"
    )
    preflight_source = ast.get_source_segment(source, preflight) or ""

    assert "/admin/load-sessions/{RUNTIME.session_id}/collisions" in preflight_source
    assert "gpu-control-load-session-collision.v1" in preflight_source
    assert "exact_global_session_namespace" in preflight_source
    assert "collision_count" in preflight_source


def test_locust_virtual_users_fence_cycles_submissions_and_retries() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    user_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "SixApiUser"
    )
    methods = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in user_class.body
        if isinstance(node, ast.FunctionDef)
    }

    assert 'self.ensure_not_preempted("business-cycle")' in methods["business_cycle"]
    assert "except LoadTestPreempted" in methods["business_cycle"]
    assert ':submit")' in methods["submit_async_asset"]
    assert ':submit")' in methods["run_imageclip_batch"]
    assert ':submit")' in methods["run_sync_image_service"]
    assert "X-Artifact-SHA256" in methods["run_sync_image_service"]
    assert "hashlib.sha256(response.content).hexdigest()" in methods["run_sync_image_service"]
    assert (
        'self.run_sync_image_service("modelview_inpaint", ordinal)'
        in methods["run_modelview_inpaint"]
    )
    assert (
        'self.run_sync_image_service("modelview_roughness", ordinal)'
        in methods["run_modelview_roughness"]
    )
    for method_name in ("request_with_retry", "post_multipart"):
        assert ':request")' in methods[method_name]
        assert ':retry")' in methods[method_name]


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


def test_teardown_cancel_respects_shared_deadline_during_backoff() -> None:
    clock = [0.0]
    sends = [0]

    def send() -> int:
        sends[0] += 1
        return 503

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    with pytest.raises(TimeoutError, match="deadline"):
        execute_bounded_teardown_cancel(
            send,
            sleep,
            max_attempts=3,
            initial_backoff_seconds=0.25,
            maximum_backoff_seconds=1.0,
            deadline_monotonic=0.2,
            monotonic=lambda: clock[0],
        )

    assert sends == [1]
    assert clock == [0.2]


def test_locust_teardown_has_final_rescan_and_one_total_deadline() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    teardown = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "teardown_session_tasks"
    )
    teardown_source = ast.get_source_segment(source, teardown) or ""

    assert "TEARDOWN_TOTAL_TIMEOUT_SECONDS" in teardown_source
    assert teardown_source.count("discover_teardown_records(") >= 2
    assert "TEARDOWN_FINAL_EMPTY_SCANS" in teardown_source
    assert 'recovery_scan["final_scope_verified"]' in teardown_source
    assert "deadline_monotonic=deadline" in teardown_source


def test_locust_revalidates_fixture_integrity_before_use_and_each_stage() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    fixture_path_node = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "fixture_path"
    )
    shape = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "SixApiStagesShape"
    )
    fixture_source = ast.get_source_segment(source, fixture_path_node) or ""
    shape_source = ast.get_source_segment(source, shape) or ""

    assert "verify_fixture_path(value)" in fixture_source
    assert "verify_all_fixture_paths()" in shape_source
    assert "fixture_integrity_failed" in shape_source


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

    assert child_environment["LOCUST_STOP_TIMEOUT"] == str(SAFE_LOCUST_STOP_TIMEOUT_SECONDS)
    assert child_environment["PRESERVE_ME"] == "yes"
    assert command.count("--stop-timeout") == 1
    stop_timeout_index = command.index("--stop-timeout")
    assert command[stop_timeout_index + 1] == str(SAFE_LOCUST_STOP_TIMEOUT_SECONDS)


def test_wrapper_forwards_interrupt_and_waits_for_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.wait_calls: list[int | None] = []
            self.signals: list[int] = []

        def wait(self, timeout: int | None = None) -> int:
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                raise KeyboardInterrupt
            return 2

        def poll(self) -> None:
            return None

        def send_signal(self, value: int) -> None:
            self.signals.append(value)

    process = Process()
    monkeypatch.setattr(
        "scripts.run_six_api_load.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    assert run_locust_process(["/fixed/locust"], {"SAFE": "yes"}) == 2
    assert process.signals == [signal.SIGINT]
    assert process.wait_calls == [None, SAFE_LOCUST_INTERRUPT_GRACE_SECONDS]


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
    capacities = [
        {
            "client": {"id": tenant_id, "kind": "test"},
            "accepting_batches": True,
        }
        for tenant_id in ("load-a", "load-b")
    ]
    asset_capacities = [
        {"client": {"id": tenant_id, "kind": "test"}} for tenant_id in ("load-a", "load-b")
    ]
    checks = validate_test_client_capacities(
        capacities,
        expected_tenant_ids=("load-a", "load-b"),
        asset_capacities=asset_capacities,
    )
    assert [item["api_key_index"] for item in checks] == [0, 1]
    assert [item["tenant_id"] for item in checks] == ["load-a", "load-b"]
    assert all(item["asset_identity_verified"] is True for item in checks)

    with pytest.raises(LoadTestConfigurationError, match="index 1"):
        validate_test_client_capacities(
            capacities,
            expected_tenant_ids=("load-a", "swapped-tenant"),
            asset_capacities=asset_capacities,
        )
    with pytest.raises(LoadTestConfigurationError, match="Asset test tenant"):
        validate_test_client_capacities(
            capacities,
            expected_tenant_ids=("load-a", "load-b"),
            asset_capacities=[asset_capacities[0], {"client": {"id": "wrong", "kind": "test"}}],
        )
    with pytest.raises(LoadTestConfigurationError, match="index 0"):
        validate_test_client_capacities(
            [{"client": {"kind": "test"}, "accepting_batches": True}, capacities[1]],
            expected_tenant_ids=("load-a", "load-b"),
            asset_capacities=asset_capacities,
        )


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
                "kind": "batch",
                "external_batch_id": "loadtest:run-01:imageclip_batch:00000001",
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
            {
                "job_id": "own-asset",
                "status": "CLAIMED",
                "client_id": "load-tenant",
                "job_type": "UV_PROCESS_V2",
                "external_asset_id": "loadtest:run-01:uv_process:00000002",
            },
            {"job_id": "foreign-asset", "status": "RUNNING", "client_id": "business"},
        ],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={},
    )
    assert [item["job_id"] for item in result["jobs"]] == [
        "production-gpu",
        "foreign-asset",
    ]
    assert result["detected"] is True

    fallback = identify_foreign_active_work(
        [
            {
                "job_id": "own-legacy-gpu",
                "status": "RUNNING",
                "tenant_id": "load-tenant",
                "kind": "batch",
                "external_batch_id": "loadtest:run-01:imageclip_batch:00000003",
            }
        ],
        [],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={},
    )
    assert fallback["detected"] is False
    with pytest.raises(LoadTestConfigurationError, match="non-object"):
        identify_foreign_active_work(
            [],
            ["bad-row"],
            test_tenant_ids=("load-tenant",),  # type: ignore[list-item]
            session_id="run-01",
            roughness_request_key_indices={},
        )


def test_watchdog_treats_same_tenant_cross_session_work_as_foreign() -> None:
    result = identify_foreign_active_work(
        [
            {
                "job_id": "other-run-roughness",
                "status": "RUNNING",
                "client_kind": "test",
                "tenant_id": "load-tenant",
                "kind": "job",
                "workflow_key": "modelview-roughness",
                "request_id": "lt:other-run:mvr:00000001",
            }
        ],
        [],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
    )

    assert result["detected"] is True
    assert result["jobs"][0]["job_id"] == "other-run-roughness"


def test_watchdog_uses_roughness_idempotency_when_gateway_rewrites_request_id() -> None:
    row = {
        "job_id": "own-roughness",
        "status": "RUNNING",
        "client_kind": "test",
        "tenant_id": "load-tenant",
        "kind": "job",
        "workflow_key": "modelview-roughness",
        "request_id": "server-generated-request-id",
        "idempotency_key": "load:run-01:mvr:00000001",
    }

    own = identify_foreign_active_work(
        [row],
        [],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
        roughness_idempotency_key_indices={"load:run-01:mvr:00000001": 0},
    )
    assert own["detected"] is False

    foreign = identify_foreign_active_work(
        [row],
        [],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
        roughness_idempotency_key_indices={"load:run-01:mvr:00000002": 0},
    )
    assert foreign["detected"] is True


def test_watchdog_rejects_session_prefix_collisions_and_asset_type_mismatch() -> None:
    result = identify_foreign_active_work(
        [
            {
                "job_id": "batch-suffix-collision",
                "status": "RUNNING",
                "client_kind": "test",
                "tenant_id": "load-tenant",
                "kind": "batch",
                "external_batch_id": ("loadtest:run-01:imageclip_batch:00000001:foreign"),
            }
        ],
        [
            {
                "job_id": "asset-api-mismatch",
                "status": "CLAIMED",
                "client_id": "load-tenant",
                "job_type": "UV_PROCESS_V2",
                "external_asset_id": ("loadtest:run-01:retopology_process:00000002"),
            }
        ],
        test_tenant_ids=("load-tenant",),
        session_id="run-01",
        roughness_request_key_indices={},
    )

    assert result["detected"] is True
    assert {item["job_id"] for item in result["jobs"]} == {
        "batch-suffix-collision",
        "asset-api-mismatch",
    }


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


def verified_six_api_records() -> list[dict[str, object]]:
    return [
        {
            "id": f"ok-{api_name}",
            "api": api_name,
            "terminal_status": "SUCCEEDED",
            "artifact_count": 1,
            "artifact_contract_verified": True,
        }
        for api_name in API_NAMES
    ]


def test_lifecycle_gate_rejects_failures_timeouts_artifacts_and_teardown() -> None:
    successes = verified_six_api_records()
    assert evaluate_load_lifecycle(successes, [])["passed"] is True

    cases = [
        [{"id": "failed", "terminal_status": "FAILED", "artifact_count": 1}],
        [{"id": "review", "terminal_status": "WAITING_REVIEW", "artifact_count": 1}],
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
        assert evaluate_load_lifecycle([*successes, *records], [])["passed"] is False
    teardown = [{"task_id": "active", "cancelled": True, "status_code": 200}]
    assert evaluate_load_lifecycle(successes, teardown)["passed"] is False


def test_bounded_stress_requires_verified_subset_and_safe_settled_cleanup() -> None:
    records = [
        *verified_six_api_records(),
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
    assert result["verified_successful"] == len(API_NAMES)
    assert result["missing_successful_apis"] == []
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


def test_bounded_stress_requires_verified_success_from_every_api() -> None:
    records = verified_six_api_records()[:-1]

    result = evaluate_load_lifecycle(
        records,
        [],
        mode="bounded_stress",
        recovery_scan_passed=True,
    )

    assert result["passed"] is False
    assert result["missing_successful_apis"] == [API_NAMES[-1]]


def test_scoped_teardown_discovery_recovers_only_exact_run_owned_work() -> None:
    discovered = discover_scoped_teardown_tasks(
        [
            {
                "kind": "job",
                "job_id": "roughness-job",
                "tenant_id": "tenant-a",
                "client_kind": "test",
                "workflow_key": "modelview-roughness",
                "request_id": "lt:run-01:mvr:00000003",
                "status": "RUNNING",
                "created_at": "2026-07-30T12:00:01Z",
            },
            {
                "kind": "job",
                "job_id": "inpaint-job",
                "tenant_id": "tenant-b",
                "client_kind": "test",
                "workflow_key": "modelview-inpaint",
                "request_id": "lt:run-01:mvi:00000004",
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
        roughness_request_key_indices={
            "lt:run-01:mvr:00000003": 0,
            "lt:run-01:mvi:00000004": 1,
        },
        session_id="run-01",
        started_at="2026-07-30T12:00:00Z",
    )

    assert [(item["api"], item["id"], item["api_key_index"]) for item in discovered] == [
        ("imageclip_batch", "imageclip-batch", 1),
        ("modelview_inpaint", "inpaint-job", 1),
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
        "request_id": "lt:run-01:mvr:00000001",
        "status": "RUNNING",
        "created_at": "2026-07-30T11:59:59Z",
    }
    with pytest.raises(LoadTestConfigurationError, match="pre-run active work"):
        discover_scoped_teardown_tasks(
            [base],
            [],
            tenant_key_indices={"tenant-a": 0},
            roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
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
            roughness_request_key_indices={},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )

    mismatched_asset = {
        "job_id": "mismatched-asset-job",
        "client_id": "tenant-a",
        "external_asset_id": "loadtest:run-01:retopology_process:00000001",
        "job_type": "UV_PROCESS_V2",
        "status": "RUNNING",
        "created_at": "2026-07-30T12:00:01Z",
    }
    with pytest.raises(LoadTestConfigurationError, match="ambiguous"):
        discover_scoped_teardown_tasks(
            [],
            [mismatched_asset],
            tenant_key_indices={"tenant-a": 0},
            roughness_request_key_indices={},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )

    suffix_batch = {
        "kind": "batch",
        "job_id": "suffix-batch",
        "tenant_id": "tenant-a",
        "client_kind": "test",
        "external_batch_id": "loadtest:run-01:imageclip_batch:00000001:foreign",
        "status": "RUNNING",
        "created_at": "2026-07-30T12:00:01Z",
    }
    with pytest.raises(LoadTestConfigurationError, match="ambiguous"):
        discover_scoped_teardown_tasks(
            [suffix_batch],
            [],
            tenant_key_indices={"tenant-a": 0},
            roughness_request_key_indices={},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        "lt:other-run:mvr:00000001",
        "lt:run-01:uv:00000001",
        "lt:run-01:mvr:0000001",
        "lt:run-01:mvr:00000001:extra",
    ],
)
def test_roughness_teardown_recovery_rejects_non_exact_session_binding(
    request_id: str,
) -> None:
    row = {
        "kind": "job",
        "job_id": "roughness-job",
        "tenant_id": "tenant-a",
        "client_kind": "test",
        "workflow_key": "modelview-roughness",
        "request_id": request_id,
        "status": "RUNNING",
        "created_at": "2026-07-30T12:00:01Z",
    }

    with pytest.raises(LoadTestConfigurationError, match="ambiguous"):
        discover_scoped_teardown_tasks(
            [row],
            [],
            tenant_key_indices={"tenant-a": 0},
            roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )


def test_roughness_teardown_recovery_requires_same_api_key_binding() -> None:
    row = {
        "kind": "job",
        "job_id": "roughness-job",
        "tenant_id": "tenant-b",
        "client_kind": "test",
        "workflow_key": "modelview-roughness",
        "request_id": "lt:run-01:mvr:00000001",
        "status": "RUNNING",
        "created_at": "2026-07-30T12:00:01Z",
    }

    with pytest.raises(LoadTestConfigurationError, match="ambiguous"):
        discover_scoped_teardown_tasks(
            [row],
            [],
            tenant_key_indices={"tenant-a": 0, "tenant-b": 1},
            roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
            session_id="run-01",
            started_at="2026-07-30T12:00:00Z",
        )


def test_roughness_teardown_uses_idempotency_when_gateway_rewrites_request_id() -> None:
    discovered = discover_scoped_teardown_tasks(
        [
            {
                "kind": "job",
                "job_id": "roughness-job",
                "tenant_id": "tenant-a",
                "client_kind": "test",
                "workflow_key": "modelview-roughness",
                "request_id": "server-generated-request-id",
                "idempotency_key": "load:run-01:mvr:00000001",
                "status": "RUNNING",
                "created_at": "2026-07-30T12:00:01Z",
            }
        ],
        [],
        tenant_key_indices={"tenant-a": 0},
        roughness_request_key_indices={"lt:run-01:mvr:00000001": 0},
        roughness_idempotency_key_indices={"load:run-01:mvr:00000001": 0},
        session_id="run-01",
        started_at="2026-07-30T12:00:00Z",
    )

    assert discovered == [
        {
            "id": "roughness-job",
            "api": "modelview_roughness",
            "kind": "job",
            "status_url": "/api/v1/jobs/roughness-job",
            "cancel_url": "/api/v1/jobs/roughness-job/cancel",
            "external_id": None,
            "api_key_index": 0,
            "last_status": "RUNNING",
            "recovery_source": "admin_scope_scan",
            "request_id": "server-generated-request-id",
            "idempotency_key": "load:run-01:mvr:00000001",
            "scope_basis": "tenant+created_at+workflow_key+idempotency_key",
        }
    ]


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


@pytest.mark.parametrize("session_id", ["plan-only", "reused-friendly-name"])
def test_production_requires_explicit_canonical_uuid4_session(
    tmp_path: Path, session_id: str
) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    environment.update(
        {
            "LOAD_TEST_SESSION_ID": session_id,
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
        }
    )
    runtime = RuntimeSettings.from_environment(environment)

    blockers = runtime.execution_blockers(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        validate_backup=False,
    )

    assert any("UUIDv4" in blocker for blocker in blockers)


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
    add_production_load_identities(environment)
    add_target_release_identity(environment)
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    runtime = RuntimeSettings.from_environment(environment)

    runtime.assert_execution_allowed(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        now=now,
        verified_release_evidence=verified_release_evidence(runtime),
        verified_live_deployment=verified_live_deployment(runtime),
    )


def test_production_requires_twelve_unique_identity_pairs_and_release_identity(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    now = datetime.now(UTC)
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    environment.update(
        {
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
            "LOAD_TEST_CHANGE_ID": "CHG-identity-gate",
            "LOAD_TEST_WINDOW_START": now.isoformat(),
            "LOAD_TEST_WINDOW_END": (now + timedelta(hours=1)).isoformat(),
        }
    )
    add_production_load_identities(environment, MINIMUM_PRODUCTION_LOAD_IDENTITIES - 1)
    runtime = RuntimeSettings.from_environment(environment)

    blockers = runtime.execution_blockers(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        now=now,
        validate_backup=False,
    )

    assert any("at least 12 unique LOAD_TEST_API_KEYS" in item for item in blockers)
    assert any("LOAD_TEST_SOURCE_REVISION" in item for item in blockers)
    assert sum("IMAGE_DIGEST" in item for item in blockers) == 5


def test_production_window_reserves_teardown_preflight_and_evidence(tmp_path: Path) -> None:
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", scenario_payload()))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    now = datetime.now(UTC)
    required_seconds = (
        scenario.total_duration_seconds
        + PRODUCTION_TEARDOWN_RESERVE_SECONDS
        + PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS
    )
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    environment.update(
        {
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
            "LOAD_TEST_CHANGE_ID": "CHG-window-gate",
            "LOAD_TEST_WINDOW_START": now.isoformat(),
            "LOAD_TEST_WINDOW_END": (now + timedelta(seconds=required_seconds - 1)).isoformat(),
        }
    )
    add_production_load_identities(environment)
    add_target_release_identity(environment)
    runtime = RuntimeSettings.from_environment(environment)

    blockers = runtime.execution_blockers(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        now=now,
        validate_backup=False,
    )

    assert any("300 seconds teardown and 540 seconds" in item for item in blockers)


def test_formal_six_api_scenario_requires_a_45_minute_window() -> None:
    scenario = load_scenario(
        Path(__file__).resolve().parents[2] / "tests/load/scenarios/six_api_120_20260803.yaml"
    )

    assert (
        scenario.total_duration_seconds
        + PRODUCTION_TEARDOWN_RESERVE_SECONDS
        + PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS
        == 45 * 60
    )


def test_extended_six_api_scenario_requires_a_75_minute_window() -> None:
    scenario = load_scenario(
        Path(__file__).resolve().parents[2]
        / "tests/load/scenarios/six_api_120_extended_20260804.yaml"
    )

    assert scenario.maximum_users == 120
    assert scenario.stages[-1].users == 120
    assert scenario.resource_mix() == {"gpu_consuming": 0.65, "cpu": 0.35}
    assert (
        scenario.total_duration_seconds
        + PRODUCTION_TEARDOWN_RESERVE_SECONDS
        + PRODUCTION_PREFLIGHT_EVIDENCE_RESERVE_SECONDS
        == 75 * 60
    )


@pytest.mark.parametrize(
    ("users", "profile_blocked"),
    [(100, False), (120, False), (121, True)],
)
def test_production_six_api_profile_requires_100_to_120_users_and_finishes_at_peak(
    tmp_path: Path,
    users: int,
    profile_blocked: bool,
) -> None:
    payload = scenario_payload()
    payload["stages"][-1]["users"] = users
    scenario = load_scenario(write_yaml(tmp_path / "scenario.yaml", payload))
    fixtures = load_fixture_manifest(fixture_manifest(tmp_path))
    now = datetime.now(UTC)
    environment = allowed_environment(tmp_path, target="https://10.3.34.11")
    environment.update(
        {
            "LOAD_TEST_ENVIRONMENT": "production",
            "ALLOW_PRODUCTION_LOAD_TEST": "true",
            "LOAD_TEST_CHANGE_ID": "CHG-profile-limit",
            "LOAD_TEST_WINDOW_START": (now - timedelta(minutes=1)).isoformat(),
            "LOAD_TEST_WINDOW_END": (now + timedelta(hours=1)).isoformat(),
        }
    )
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
    runtime = RuntimeSettings.from_environment(environment)

    blockers = runtime.execution_blockers(
        scenario,
        fixtures,
        repository_root=Path("/opt/gpu-control"),
        now=now,
        validate_backup=False,
    )

    assert (
        any("peak between 100 and 120 users" in blocker for blocker in blockers) is profile_blocked
    )
    if users > 120:
        assert any("safety cap of 120" in blocker for blocker in blockers)


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
        (path for path in backup.iterdir() if path.name not in {"SHA256SUMS", "BACKUP_COMPLETE"}),
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
    add_target_release_identity(environment)
    provisional = RuntimeSettings.from_environment(environment)
    environment["LOAD_TEST_CONFIRMATION_TOKEN"] = provisional.expected_confirmation_token
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
    assert plan["secret_inventory"]["unique_api_key_count"] == 2
    assert plan["target_release_identity"] == runtime.target_release_identity
    assert plan["scenario"]["resource_mix"] == {
        "gpu_consuming": 0.666667,
        "cpu": 0.333333,
    }


def test_result_manifest_cannot_claim_external_acceptance_before_git_publish(
    tmp_path: Path,
) -> None:
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    (result_dir / "summary.json").write_text("{}\n", encoding="utf-8")

    write_result_manifest(result_dir, session_id="session-1")

    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["external_anchor_status"] == "PENDING_GIT_PUBLISH"
    assert manifest["production_acceptance_eligible"] is False


def test_locust_records_terminal_json_artifact_sha_evidence_and_release_identity() -> None:
    source_path = Path(__file__).resolve().parents[2] / "tests/load/locustfile.py"
    source = source_path.read_text(encoding="utf-8")
    runner_source = (Path(__file__).resolve().parents[2] / "scripts/run_six_api_load.py").read_text(
        encoding="utf-8"
    )

    for required_field in (
        "raw_terminal_status_json",
        "artifact_listing_metadata",
        "artifact_evidence",
        "target_release_identity",
        "release_evidence_verification",
        "live_deployment_verification",
        "final_live_deployment_verification",
        "stable_since_start",
        "PENDING_GIT_PUBLISH",
        "build_load_artifact_evidence",
        '"artifact.verified"',
        "**artifact_evidence",
    ):
        assert required_field in source
    assert "postrun-deployment.json" in runner_source
    assert "verify_live_load_deployment" in runner_source
    assert "stable_since_start" in runner_source


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
    assert result["route_classification"]["sync_end_to_end_api_names"] == [
        "modelview_inpaint",
        "modelview_roughness",
    ]

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
