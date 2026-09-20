from pathlib import Path

import yaml


def test_control_deploy_is_build_only_and_includes_asset_worker() -> None:
    script = Path("scripts/deploy_control.sh").read_text(encoding="utf-8")

    assert "--build-only" in script
    assert "--profile asset-plane" in script
    assert "build api provider-controller scheduler asset-api web asset-worker-control" in script
    assert " up -d" not in script
    assert " restart " not in script
    assert " stop " not in script


def test_node_deploy_only_builds_worker_and_never_reconciles_comfyui() -> None:
    script = Path("scripts/deploy_node.sh").read_text(encoding="utf-8")

    assert "--build-worker-only" in script
    assert "--profile asset-plane" in script
    assert "build blender-worker" in script
    assert " up -d" not in script
    assert " restart " not in script
    assert " stop " not in script
    assert "build comfyui" not in script


def test_gpuctl_forwards_safe_deploy_arguments() -> None:
    script = Path("scripts/gpuctl").read_text(encoding="utf-8")

    assert "deploy control --build-only" in script
    assert "deploy node --build-worker-only" in script
    assert '"${script_dir}/deploy_control.sh" "$@"' in script
    assert '"${script_dir}/deploy_node.sh" "$@"' in script


def test_autodl_tunnel_is_private_hardened_and_scheduler_only() -> None:
    compose = yaml.safe_load(Path("deploy/control-plane/compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["autodl-5090-tunnel"]

    assert service["profiles"] == ["autodl"]
    assert service["restart"] == "unless-stopped"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in service
    assert service["expose"] == ["16006", "18080"]
    assert service["networks"] == ["autodl-5090", "provider-control"]
    health_command = service["healthcheck"]["test"][-1]
    assert "/health/live" in health_command
    assert "/health/ready" not in health_command
    assert health_command.endswith("timeout=3)")
    assert all(volume.endswith(":ro") for volume in service["volumes"])
    assert not any("autodl.token" in volume for volume in service["volumes"])
    assert any("provider-controller-hmac" in volume for volume in service["volumes"])
    assert any("autodl-known-hosts" in volume for volume in service["volumes"])
    recovery_index = service["command"].index("--recovery-profile")
    assert service["command"][recovery_index + 1] == "comfyui-6006-v1"
    timeout_index = service["command"].index("--remote-ready-timeout-seconds")
    assert service["command"][timeout_index + 1] == (
        "${AUTODL_TUNNEL_REMOTE_READY_TIMEOUT_SECONDS:-180}"
    )
    assert service["stop_grace_period"] == "90s"
    health_host_index = service["command"].index("--health-host")
    assert service["command"][health_host_index + 1] == "0.0.0.0"  # noqa: S104

    scheduler_networks = compose["services"]["scheduler"]["networks"]
    assert scheduler_networks == ["backend", "autodl-5090"]
    assert "autodl-5090" in compose["networks"]
    assert compose["services"]["provider-controller"]["networks"] == [
        "backend",
        "provider-control",
    ]
    nginx = compose["services"]["nginx"]
    assert "10.3.34.11:16006:16006" in nginx["ports"]
    assert nginx["networks"] == ["frontend", "backend", "autodl-5090"]
    nginx_config = Path("deploy/control-plane/nginx/nginx.conf").read_text(
        encoding="utf-8"
    )
    assert "listen 16006 ssl;" in nginx_config
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx_config
    assert "proxy_set_header Connection $connection_upgrade;" in nginx_config
    assert "allow 10.3.34.0/24;" in nginx_config
    assert "proxy_request_buffering off;" in nginx_config
    for name, candidate in compose["services"].items():
        if name not in {"scheduler", "autodl-5090-tunnel", "nginx"}:
            assert "autodl-5090" not in candidate.get("networks", [])


def test_autodl_tunnel_image_runs_non_root_with_a_healthcheck() -> None:
    dockerfile = Path("apps/autodl_tunnel/Dockerfile").read_text(encoding="utf-8")

    assert "USER gpucontrol:gpucontrol" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health/live" in dockerfile
    assert "/health/ready" not in dockerfile
    assert "COPY --chmod=0555 scripts/autodl_comfy_tunnel.py" in dockerfile
    assert (
        "COPY --chmod=0444 scripts/autodl_remote_start_comfyui.sh "
        "/usr/local/share/autodl_remote_start_comfyui.sh"
    ) in dockerfile
    assert Path("apps/autodl_tunnel/requirements.txt").read_text(encoding="utf-8") == (
        "paramiko==4.0.0\n"
    )
