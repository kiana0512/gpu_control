from pathlib import Path


def test_control_deploy_is_build_only_and_includes_asset_worker() -> None:
    script = Path("scripts/deploy_control.sh").read_text(encoding="utf-8")

    assert "--build-only" in script
    assert "--profile asset-plane" in script
    assert 'build api scheduler asset-api web asset-worker-control' in script
    assert ' up -d' not in script
    assert ' restart ' not in script
    assert ' stop ' not in script


def test_node_deploy_only_builds_worker_and_never_reconciles_comfyui() -> None:
    script = Path("scripts/deploy_node.sh").read_text(encoding="utf-8")

    assert "--build-worker-only" in script
    assert "--profile asset-plane" in script
    assert 'build blender-worker' in script
    assert ' up -d' not in script
    assert ' restart ' not in script
    assert ' stop ' not in script
    assert 'build comfyui' not in script


def test_gpuctl_forwards_safe_deploy_arguments() -> None:
    script = Path("scripts/gpuctl").read_text(encoding="utf-8")

    assert 'deploy control --build-only' in script
    assert 'deploy node --build-worker-only' in script
    assert '"${script_dir}/deploy_control.sh" "$@"' in script
    assert '"${script_dir}/deploy_node.sh" "$@"' in script
