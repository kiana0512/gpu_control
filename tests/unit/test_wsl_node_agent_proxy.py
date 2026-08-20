import importlib.util
import subprocess
from pathlib import Path

from packages.gpu_control_core.security import sign_agent_request


def load_proxy(monkeypatch):
    monkeypatch.setenv("NODE_AGENT_HMAC_SECRET", "proxy-test-secret")
    path = Path("scripts/wsl-node-agent-proxy.py")
    spec = importlib.util.spec_from_file_location("wsl_node_agent_proxy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_proxy_signature_matches_node_agent_contract(monkeypatch) -> None:
    proxy = load_proxy(monkeypatch)
    assert proxy._signature("GET", "/v1/gpu-metrics", b"", "123", "nonce") == (
        sign_agent_request("GET", "/v1/gpu-metrics", b"", "123", "nonce", "proxy-test-secret")
    )


def test_proxy_parses_wsl_nvidia_metrics(monkeypatch) -> None:
    proxy = load_proxy(monkeypatch)

    def fake_run(*args, **kwargs):
        assert args[0][0] == "/usr/lib/wsl/lib/nvidia-smi"
        assert kwargs["timeout"] == 4
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="95, 4542, 12282, 77, 220.6, 285.0\n"
        )

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)
    assert proxy._gpu_metrics() == {
        "gpu_util_percent": 95,
        "free_vram_mb": 4542,
        "total_vram_mb": 12282,
        "gpu_temperature_c": 77.0,
        "gpu_power_w": 220.6,
        "gpu_power_limit_w": 285.0,
    }
