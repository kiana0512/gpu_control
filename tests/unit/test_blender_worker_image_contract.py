from pathlib import Path


def test_worker_image_runs_fail_closed_bootstrap_before_worker() -> None:
    dockerfile = Path("apps/blender_worker/Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python3", "-m", "gpu_control_blender_worker.bootstrap"]' in dockerfile
    assert 'CMD ["python3", "-m", "gpu_control_blender_worker.main"]' in dockerfile


def test_worker_image_carries_immutable_release_identity() -> None:
    dockerfile = Path("apps/blender_worker/Dockerfile").read_text(encoding="utf-8")

    assert "ARG ASSET_WORKER_VERSION=1.4.23-uv-source-units-v2" in dockerfile
    assert "ARG GPU_CONTROL_REVISION=unknown" in dockerfile
    assert 'org.opencontainers.image.version="${ASSET_WORKER_VERSION}"' in dockerfile
    assert 'org.opencontainers.image.revision="${GPU_CONTROL_REVISION}"' in dockerfile
    assert "ASSET_WORKER_BUILD_VERSION=${ASSET_WORKER_VERSION}" in dockerfile
    assert "GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}" in dockerfile


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

    assert "ASSET_WORKER_VERSION=1.4.23-uv-source-units-v2" in environment
    assert "ASSET_WORKER_IMAGE_TAG=1.4.23-uv-source-units-v2" in environment
    for compose in (control_compose, node_compose):
        assert (
            "ASSET_WORKER_VERSION: ${ASSET_WORKER_VERSION:-1.4.23-uv-source-units-v2}"
        ) in compose
        assert "GPU_CONTROL_REVISION: ${GPU_CONTROL_REVISION:-unknown}" in compose
        assert (
            "li3d/blender-worker:${ASSET_WORKER_IMAGE_TAG:-1.4.23-uv-source-units-v2}"
        ) in compose
