from pathlib import Path


def test_worker_image_runs_fail_closed_bootstrap_before_worker() -> None:
    dockerfile = Path("apps/blender_worker/Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python3", "-m", "gpu_control_blender_worker.bootstrap"]' in dockerfile
    assert 'CMD ["python3", "-m", "gpu_control_blender_worker.main"]' in dockerfile


def test_worker_image_carries_immutable_release_identity() -> None:
    dockerfile = Path("apps/blender_worker/Dockerfile").read_text(encoding="utf-8")

    assert "ARG ASSET_WORKER_VERSION=1.4.55-uv-multimesh-mof-v1" in dockerfile
    assert "ARG GPU_CONTROL_REVISION=unknown" in dockerfile
    assert 'org.opencontainers.image.version="${ASSET_WORKER_VERSION}"' in dockerfile
    assert 'org.opencontainers.image.revision="${GPU_CONTROL_REVISION}"' in dockerfile
    assert "ASSET_WORKER_BUILD_VERSION=${ASSET_WORKER_VERSION}" in dockerfile
    assert "GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}" in dockerfile
    assert dockerfile.index("RUN apt-get update") < dockerfile.index(
        "ARG ASSET_WORKER_VERSION"
    )


def test_all_three_workers_receive_the_same_approved_skill_root() -> None:
    control_compose = Path("deploy/control-plane/compose.yaml").read_text(encoding="utf-8")
    node_compose = Path("deploy/gpu-node/compose.yaml").read_text(encoding="utf-8")

    expected = "CODEX_SKILLS_ROOT: /opt"
    assert expected in control_compose
    assert expected in node_compose
    alignment_root = "ALIGNMENT_SKILL_ROOT: /opt/codex/skills/blender-align-bake-models"
    assert alignment_root in control_compose
    assert alignment_root in node_compose


def test_worker_release_defaults_are_aligned() -> None:
    environment = Path(".env.example").read_text(encoding="utf-8")
    control_compose = Path("deploy/control-plane/compose.yaml").read_text(encoding="utf-8")
    node_compose = Path("deploy/gpu-node/compose.yaml").read_text(encoding="utf-8")

    assert "ASSET_WORKER_VERSION=1.4.55-uv-multimesh-mof-v1" in environment
    assert "ASSET_WORKER_IMAGE_TAG=1.4.55-uv-multimesh-mof-v1" in environment
    for compose in (control_compose, node_compose):
        assert (
            "ASSET_WORKER_VERSION: ${ASSET_WORKER_VERSION:-1.4.55-uv-multimesh-mof-v1}"
        ) in compose
        assert "GPU_CONTROL_REVISION: ${GPU_CONTROL_REVISION:-unknown}" in compose
        assert (
            "li3d/blender-worker:${ASSET_WORKER_IMAGE_TAG:-1.4.55-uv-multimesh-mof-v1}"
        ) in compose


def test_legacy_uv_max_weld_release_is_pinned_and_strict_by_default() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    bootstrap = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/bootstrap.py"
    ).read_text(encoding="utf-8")
    verifier = Path("scripts/verify_asset_skills.sh").read_text(encoding="utf-8")
    settings = Path("packages/gpu_control_core/settings.py").read_text(encoding="utf-8")
    control_compose = Path("deploy/control-plane/compose.yaml").read_text(encoding="utf-8")

    unwrap_sha = "04c09e0907ad8ad3838be2ece177b8c9c4b4d33c151633849bfd6262a70748c9"
    qa_sha = "a263d0fc05947d70988317972f9b0bb38e7c85a165274756d3c4dbf4e05f91c3"
    for digest in (unwrap_sha, qa_sha):
        assert digest in worker
        assert digest in bootstrap
        assert digest in verifier
    assert 'uv_qa_enforcement: Literal["strict", "advisory"] = "strict"' in settings
    assert "UV_QA_ENFORCEMENT: ${UV_QA_ENFORCEMENT:-strict}" in control_compose


def test_worker_pins_the_automatic_uv_classifier() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    classifier = Path(
        "packages/asset_processing/blender_uv_auto_classify.py"
    ).read_bytes()

    import hashlib

    digest = hashlib.sha256(classifier).hexdigest()
    assert digest == "f18c6d1e359f7264f1e5c62bc8edbcb69e2243a28b4bb94401c10a3dd1e69849"
    assert digest in worker
    assert "/app/packages/asset_processing/blender_uv_auto_classify.py" in worker
