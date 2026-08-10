import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import gpu_node_agent.main as node_agent
import httpx
import pytest
from gpu_node_agent.main import (
    NodeAgentSettings,
    _current_ip,
    _imageclip_pipeline_state,
    _mac_address,
    _parse_pressure_avg10,
    _system_metrics,
    _validated_gpu_model,
    create_app,
)

from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings


def test_worker_agent_does_not_require_control_plane_secrets() -> None:
    settings = NodeAgentSettings(
        environment="production",
        node_agent_hmac_secret="separate-worker-agent-secret-at-least-32-characters",
    )
    assert settings.environment == "production"
    assert settings.node_agent_hmac_secret.endswith("32-characters")


def test_hybrid_node_uses_physical_host_identity_over_wsl_nat() -> None:
    assert _current_ip("10.3.34.11", "10.3.34.14") == "10.3.34.14"
    assert _mac_address("3C-7C-3F-A5-B0-4F") == "3c:7c:3f:a5:b0:4f"


async def test_gpu_model_comes_from_nvidia_smi_and_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: tuple[str, ...] = ()

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"  NVIDIA   GeForce RTX 4090  \n", b""

    async def fake_subprocess(*args: str, **_: object) -> Process:
        nonlocal called
        called = args
        return Process()

    monkeypatch.setattr(node_agent, "_nvidia_smi_path", lambda: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    assert await node_agent._gpu_model() == "NVIDIA GeForce RTX 4090"
    assert called[:3] == (
        "/usr/bin/nvidia-smi",
        "--query-gpu=name",
        "--format=csv,noheader",
    )
    with pytest.raises(RuntimeError, match="invalid GPU product name"):
        _validated_gpu_model("NVIDIA RTX 4090 <script>")


async def test_gpu_metrics_include_temperature_and_power(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: tuple[str, ...] = ()

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"73, 12000, 24576, 64, 287.4, 370.0\n", b""

    async def fake_subprocess(*args: str, **_: object) -> Process:
        nonlocal called
        called = args
        return Process()

    monkeypatch.setattr(node_agent, "_nvidia_smi_path", lambda: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    assert await node_agent._gpu_metrics() == {
        "gpu_util_percent": 73,
        "free_vram_mb": 12000,
        "total_vram_mb": 24576,
        "gpu_temperature_c": 64.0,
        "gpu_power_w": 287.4,
        "gpu_power_limit_w": 370.0,
    }
    assert called[:3] == (
        "/usr/bin/nvidia-smi",
        "--query-gpu=utilization.gpu,memory.free,memory.total,temperature.gpu,power.draw,power.limit",
        "--format=csv,noheader,nounits",
    )
    assert node_agent._optional_gpu_metric("[N/A]", minimum=0, maximum=150) is None


def test_system_metrics_identify_wsl_and_report_pressure(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    (proc_root / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc_root / "pressure").mkdir()
    (proc_root / "sys" / "kernel" / "osrelease").write_text(
        "6.6.87.2-microsoft-standard-WSL2\n", encoding="utf-8"
    )
    (proc_root / "sys" / "kernel" / "random" / "boot_id").write_text(
        "12345678-1234-4abc-8def-1234567890ab\n", encoding="utf-8"
    )
    (proc_root / "uptime").write_text("125.50 400.00\n", encoding="utf-8")
    (proc_root / "loadavg").write_text("4.00 2.00 1.00 1/100 42\n", encoding="utf-8")
    (proc_root / "meminfo").write_text(
        "MemTotal:       16777216 kB\n"
        "MemAvailable:    4194304 kB\n"
        "SwapTotal:       8388608 kB\n"
        "SwapFree:        6291456 kB\n",
        encoding="utf-8",
    )
    (proc_root / "pressure" / "cpu").write_text(
        "some avg10=2.50 avg60=1.00 avg300=0.50 total=1\n", encoding="utf-8"
    )
    (proc_root / "pressure" / "memory").write_text(
        "some avg10=3.00 avg60=2.00 avg300=1.00 total=2\n"
        "full avg10=1.25 avg60=1.00 avg300=0.50 total=1\n",
        encoding="utf-8",
    )
    (proc_root / "pressure" / "io").write_text(
        "some avg10=4.00 avg60=2.00 avg300=1.00 total=2\n"
        "full avg10=0.75 avg60=0.50 avg300=0.25 total=1\n",
        encoding="utf-8",
    )

    snapshot = _system_metrics(proc_root, cpu_count=8)
    assert snapshot == {
        "platform": "wsl2",
        "boot_id": "12345678-1234-4abc-8def-1234567890ab",
        "uptime_seconds": 125.5,
        "cpu_count": 8,
        "load_1m_per_cpu": 0.5,
        "load_5m_per_cpu": 0.25,
        "load_15m_per_cpu": 0.125,
        "memory_total_mb": 16384,
        "memory_available_mb": 4096,
        "memory_available_ratio": 0.25,
        "swap_total_mb": 8192,
        "swap_used_mb": 2048,
        "swap_used_ratio": 0.25,
        "cpu_pressure_some_avg10": 2.5,
        "memory_pressure_some_avg10": 3.0,
        "memory_pressure_full_avg10": 1.25,
        "io_pressure_some_avg10": 4.0,
        "io_pressure_full_avg10": 0.75,
    }
    assert _parse_pressure_avg10("some avg10=nan total=0", "some") is None


async def test_heartbeat_payload_includes_cached_gpu_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_gpu_uuid() -> str:
        return "GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c"

    async def fake_gpu_model() -> str:
        return "NVIDIA GeForce RTX 3090"

    async def fake_codex(_: Path) -> dict[str, object]:
        return {}

    def capture_heartbeat(_: NodeAgentSettings, identity: dict[str, object]) -> None:
        captured.update(identity)
        raise asyncio.CancelledError

    monkeypatch.setattr(node_agent, "_gpu_uuid", fake_gpu_uuid)
    monkeypatch.setattr(node_agent, "_gpu_model", fake_gpu_model)
    monkeypatch.setattr(node_agent, "_mac_address", lambda _: "18:c0:4d:9f:13:13")
    monkeypatch.setattr(
        node_agent,
        "_imageclip_pipeline_state",
        lambda _: ("7" * 40, "8" * 64),
    )
    monkeypatch.setattr(node_agent, "_current_ip", lambda *_: "10.0.0.12")
    monkeypatch.setattr(node_agent, "_codex_cli_runtime", fake_codex)
    monkeypatch.setattr(node_agent, "_post_heartbeat", capture_heartbeat)
    monkeypatch.setattr(node_agent, "_runtime_identity", lambda: ("1.5.5", "9" * 40))
    settings = NodeAgentSettings(
        environment="test",
        node_agent_hmac_secret="test-secret",
        node_id="worker-3090-a",
        control_host="10.0.0.1",
        node_advertise_ip="10.0.0.12",
        imageclip_root=tmp_path,
    )
    app = SimpleNamespace(state=SimpleNamespace(node_identity={}))
    with pytest.raises(asyncio.CancelledError):
        await node_agent._heartbeat_loop(app, settings)
    assert captured["gpu_model"] == "NVIDIA GeForce RTX 3090"


async def test_heartbeat_retries_and_omits_permanently_unavailable_gpu_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = 0.0
    model_queries = 0
    heartbeats: list[dict[str, object]] = []

    async def fake_gpu_uuid() -> str:
        return "GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c"

    async def failing_gpu_model() -> str:
        nonlocal model_queries
        model_queries += 1
        raise RuntimeError("unsupported product-name query")

    async def fake_codex(_: Path) -> dict[str, object]:
        return {}

    async def advance_clock(_: float) -> None:
        nonlocal clock
        clock += 61

    def capture_heartbeat(_: NodeAgentSettings, identity: dict[str, object]) -> dict[str, object]:
        heartbeats.append(dict(identity))
        if len(heartbeats) == 2:
            raise asyncio.CancelledError
        return {"base_url": "https://10.0.0.12:9444"}

    monkeypatch.setattr(node_agent, "_gpu_uuid", fake_gpu_uuid)
    monkeypatch.setattr(node_agent, "_gpu_model", failing_gpu_model)
    monkeypatch.setattr(node_agent, "_mac_address", lambda _: "18:c0:4d:9f:13:13")
    monkeypatch.setattr(
        node_agent,
        "_imageclip_pipeline_state",
        lambda _: ("7" * 40, "8" * 64),
    )
    monkeypatch.setattr(node_agent, "_current_ip", lambda *_: "10.0.0.12")
    monkeypatch.setattr(node_agent, "_codex_cli_runtime", fake_codex)
    monkeypatch.setattr(node_agent, "_post_heartbeat", capture_heartbeat)
    monkeypatch.setattr(node_agent, "_runtime_identity", lambda: ("1.5.5", "9" * 40))
    monkeypatch.setattr(node_agent.time, "monotonic", lambda: clock)
    monkeypatch.setattr(node_agent.asyncio, "sleep", advance_clock)
    settings = NodeAgentSettings(
        environment="test",
        node_agent_hmac_secret="test-secret",
        node_id="worker-3090-a",
        control_host="10.0.0.1",
        node_advertise_ip="10.0.0.12",
        imageclip_root=tmp_path,
    )
    app = SimpleNamespace(state=SimpleNamespace(node_identity={}))

    with pytest.raises(asyncio.CancelledError):
        await node_agent._heartbeat_loop(app, settings)

    assert model_queries == 2
    assert len(heartbeats) == 2
    assert all("gpu_model" not in identity for identity in heartbeats)
    assert all(
        identity["gpu_uuid"] == "GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c"
        for identity in heartbeats
    )
    assert all(identity["ip"] == "10.0.0.12" for identity in heartbeats)
    assert all(identity["imageclip_pipeline_sha256"] == "8" * 64 for identity in heartbeats)


def test_imageclip_pipeline_state_is_deterministic_and_tracks_content(tmp_path: Path) -> None:
    repository = tmp_path / "imageclip"
    (repository / ".git" / "refs" / "heads").mkdir(parents=True)
    (repository / "Cherry_lizi" / "nested").mkdir(parents=True)
    commit = "7" * 40
    (repository / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repository / ".git" / "refs" / "heads" / "main").write_text(f"{commit}\n", encoding="utf-8")
    (repository / "ImageClip.json").write_text('{"workflow":"latest"}\n', encoding="utf-8")
    custom_node = repository / "Cherry_lizi" / "nested" / "node.py"
    custom_node.write_text("VALUE = 1\n", encoding="utf-8")
    cache = repository / "Cherry_lizi" / "__pycache__"
    cache.mkdir()
    (cache / "node.pyc").write_bytes(b"ignored")

    first = _imageclip_pipeline_state(repository)
    second = _imageclip_pipeline_state(repository)
    assert first == second
    assert first[0] == commit

    custom_node.write_text("VALUE = 2\n", encoding="utf-8")
    assert _imageclip_pipeline_state(repository)[1] != first[1]


async def test_node_agent_requires_signature_and_rejects_replay() -> None:
    secret = "node-agent-test-secret"
    app = create_app(Settings(environment="test", node_agent_hmac_secret=secret))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            assert (await client.get("/health/live")).status_code == 200
            assert (
                await client.post("/v1/operations", json={"action": "status"})
            ).status_code == 401

            body = b'{"action":"forbidden","lines":200}'
            timestamp = str(int(time.time()))
            nonce = "one-time-nonce"
            signature = sign_agent_request("POST", "/v1/operations", body, timestamp, nonce, secret)
            headers = {
                "content-type": "application/json",
                "x-gpu-timestamp": timestamp,
                "x-gpu-nonce": nonce,
                "x-gpu-signature": signature,
            }
            first = await client.post("/v1/operations", content=body, headers=headers)
            assert first.status_code == 400
            replay = await client.post("/v1/operations", content=body, headers=headers)
            assert replay.status_code == 409


async def test_node_agent_health_exposes_package_and_source_identity(monkeypatch) -> None:
    monkeypatch.setattr(node_agent, "_runtime_identity", lambda: ("1.5.5", "a" * 40))
    app = create_app(Settings(environment="test", node_agent_hmac_secret="test-secret"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            response = await client.get("/health/live")
            assert response.status_code == 200
            assert response.json() == {
                "status": "live",
                "package_version": "1.5.5",
                "source_revision": "a" * 40,
            }


async def test_gpu_metrics_are_signed_and_return_live_values(monkeypatch) -> None:
    secret = "node-agent-test-secret"

    async def fake_gpu_metrics() -> dict[str, int | float | None]:
        return {
            "gpu_util_percent": 73,
            "free_vram_mb": 12000,
            "total_vram_mb": 24576,
            "gpu_temperature_c": 64.0,
            "gpu_power_w": 287.4,
            "gpu_power_limit_w": 370.0,
        }

    monkeypatch.setattr(node_agent, "_gpu_metrics", fake_gpu_metrics)
    app = create_app(Settings(environment="test", node_agent_hmac_secret=secret))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            assert (await client.get("/v1/gpu-metrics")).status_code == 401
            timestamp = str(int(time.time()))
            nonce = "gpu-metrics-nonce"
            signature = sign_agent_request("GET", "/v1/gpu-metrics", b"", timestamp, nonce, secret)
            response = await client.get(
                "/v1/gpu-metrics",
                headers={
                    "x-gpu-timestamp": timestamp,
                    "x-gpu-nonce": nonce,
                    "x-gpu-signature": signature,
                },
            )
            assert response.status_code == 200
            assert response.json() == {
                "gpu_util_percent": 73,
                "free_vram_mb": 12000,
                "total_vram_mb": 24576,
                "gpu_temperature_c": 64.0,
                "gpu_power_w": 287.4,
                "gpu_power_limit_w": 370.0,
            }


async def test_system_metrics_are_signed_and_bounded(monkeypatch) -> None:
    secret = "node-agent-test-secret"

    def fake_system_metrics() -> dict[str, str | int | float | None]:
        return {
            "platform": "wsl2",
            "boot_id": "12345678-1234-4abc-8def-1234567890ab",
            "uptime_seconds": 125.5,
            "cpu_count": 8,
            "load_1m_per_cpu": 0.5,
            "memory_available_ratio": 0.25,
            "swap_used_ratio": 0.1,
            "memory_pressure_full_avg10": 0.0,
        }

    monkeypatch.setattr(node_agent, "_system_metrics", fake_system_metrics)
    app = create_app(Settings(environment="test", node_agent_hmac_secret=secret))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            assert (await client.get("/v1/system-metrics")).status_code == 401
            timestamp = str(int(time.time()))
            nonce = "system-metrics-nonce"
            signature = sign_agent_request(
                "GET", "/v1/system-metrics", b"", timestamp, nonce, secret
            )
            response = await client.get(
                "/v1/system-metrics",
                headers={
                    "x-gpu-timestamp": timestamp,
                    "x-gpu-nonce": nonce,
                    "x-gpu-signature": signature,
                },
            )
            assert response.status_code == 200
            assert response.json()["platform"] == "wsl2"
            assert response.json()["memory_available_ratio"] == 0.25
