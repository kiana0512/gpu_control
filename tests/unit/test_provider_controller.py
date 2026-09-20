from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from gpu_control_provider_controller import main as controller
from pydantic import SecretStr

from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: set[str] = set()

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def set(self, key: str, _value: str, **_kwargs: Any) -> bool:
        if key in self.values:
            return False
        self.values.add(key)
        return True


def signed_headers(secret: str, method: str, path: str, nonce: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(method, path, b"", timestamp, nonce, secret),
    }


def app_settings(
    token_file: Path, secret: str, *, mutations_enabled: bool = False
) -> Settings:
    return Settings(
        _env_file=None,
        autodl_enabled=True,
        autodl_mutations_enabled=mutations_enabled,
        autodl_token_file=token_file,
        provider_controller_hmac_secret=SecretStr(secret),
    )


def test_hmac_covers_query_and_nonce_replay_is_rejected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)

    async def inventory(_self: Any) -> dict[str, Any]:
        return {"configured": True, "instances": []}

    monkeypatch.setattr(controller.AutoDLProvider, "inventory", inventory)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret))
    path = "/internal/v1/providers/autodl/inventory?force=true"
    headers = signed_headers(secret, "GET", path, "query-covered-nonce-1")
    with TestClient(app) as client:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        replay = client.get(path, headers=headers)
        assert replay.status_code == 409

        tampered = client.get(
            "/internal/v1/providers/autodl/inventory?force=false",
            headers=signed_headers(secret, "GET", path, "query-covered-nonce-2"),
        )
        assert tampered.status_code == 401


def test_disabled_mutations_still_allow_idempotent_running_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)

    async def state(_self: Any, _product: str, _instance_id: str) -> tuple[str, str]:
        return "running", "running"

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    body = b'{"desired_state":"running","correlation_id":"test-correlation"}'
    timestamp = str(int(time.time()))
    nonce = "idempotent-state-nonce"
    headers = {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(
            "POST", path, body, timestamp, nonce, secret
        ),
    }
    with TestClient(app) as client:
        response = client.post(path, content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["state"] == "running"


def test_read_state_never_dispatches_power_mutation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    calls = {"state": 0, "mutation": 0}

    async def state(_self: Any, product: str, instance_id: str) -> tuple[str, str]:
        calls["state"] += 1
        assert product == "app"
        assert instance_id == "pro-a1"
        return "starting", "booting"

    async def ensure_state(_self: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["mutation"] += 1
        raise AssertionError("read-only state endpoint dispatched a power mutation")

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    monkeypatch.setattr(controller.AutoDLProvider, "ensure_state", ensure_state)
    secret = "s" * 48
    app = controller.create_app(
        app_settings(token_file, secret, mutations_enabled=True)
    )
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    headers = signed_headers(secret, "GET", path, "read-only-state-nonce")
    with TestClient(app) as client:
        response = client.get(path, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "accepted": False,
        "state": "starting",
        "provider_status": "booting",
    }
    assert calls == {"state": 1, "mutation": 0}


def test_read_state_uses_provider_error_mapping(tmp_path: Path, monkeypatch: Any) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)

    async def state(_self: Any, _product: str, _instance_id: str) -> tuple[str, str]:
        raise controller.AutoDLProviderError(
            "provider throttled the state read",
            code="AUTODL_RATE_LIMITED",
            request_id="provider-request-1",
            status_code=429,
        )

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    headers = signed_headers(secret, "GET", path, "read-state-error-nonce")
    with TestClient(app) as client:
        response = client.get(path, headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "AUTODL_RATE_LIMITED",
        "message": "provider throttled the state read",
        "provider_request_id": "provider-request-1",
        "uncertain": False,
    }


def test_disabled_mutations_reject_a_real_power_transition(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)

    async def state(_self: Any, _product: str, _instance_id: str) -> tuple[str, str]:
        return "stopped", "shutdown"

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    body = b'{"desired_state":"running","correlation_id":"blocked-transition"}'
    timestamp = str(int(time.time()))
    nonce = "disabled-mutation-nonce"
    headers = {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(
            "POST", path, body, timestamp, nonce, secret
        ),
    }
    with TestClient(app) as client:
        response = client.post(path, content=body, headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTODL_MUTATIONS_DISABLED"


def test_ssh_credentials_are_marked_no_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)

    async def credentials(
        _self: Any, _product: str, _instance_id: str
    ) -> dict[str, Any]:
        return {
            "host": "connect.westd.seetacloud.com",
            "port": 2222,
            "username": "root",
            "password": "ephemeral",
        }

    monkeypatch.setattr(controller.AutoDLProvider, "ssh_credentials", credentials)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/ssh-credentials"
    headers = signed_headers(secret, "POST", path, "credential-no-store-nonce")
    with TestClient(app) as client:
        response = client.post(path, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_running_mutation_accepts_only_allowlisted_bootstrap_profile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    captured: dict[str, Any] = {}

    async def state(_self: Any, _product: str, _instance_id: str) -> tuple[str, str]:
        return "stopped", "shutdown"

    async def ensure_state(
        _self: Any, _product: str, _instance_id: str, _desired: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured.update(kwargs)
        return {"accepted": True, "state": "stopped", "provider_status": "shutdown"}

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    monkeypatch.setattr(controller.AutoDLProvider, "ensure_state", ensure_state)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret, mutations_enabled=True))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    payload = {
        "desired_state": "running",
        "correlation_id": "allowlisted-profile",
        "bootstrap_profile": "comfyui-6006-v1",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = "allowlisted-profile-nonce"
    headers = {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(
            "POST", path, body, timestamp, nonce, secret
        ),
    }
    with TestClient(app) as client:
        response = client.post(path, content=body, headers=headers)

    assert response.status_code == 200
    assert captured == {
        "observed": ("stopped", "shutdown"),
        "bootstrap_profile": "comfyui-6006-v1",
    }
    assert "start_command" not in body.decode()
    assert "start_command" not in response.text


def test_non_allowlisted_bootstrap_profile_is_rejected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret, mutations_enabled=True))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    payload = {
        "desired_state": "running",
        "correlation_id": "rejected-profile",
        "bootstrap_profile": "arbitrary-shell",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = "rejected-profile-nonce"
    headers = {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(
            "POST", path, body, timestamp, nonce, secret
        ),
    }
    with TestClient(app) as client:
        response = client.post(path, content=body, headers=headers)

    assert response.status_code == 422


def test_stop_mutation_never_passes_a_bootstrap_profile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    token_file = tmp_path / "autodl.token"
    token_file.write_text("test-token")
    fake_redis = FakeRedis()
    monkeypatch.setattr(controller.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    captured: dict[str, Any] = {}

    async def state(_self: Any, _product: str, _instance_id: str) -> tuple[str, str]:
        return "running", "running"

    async def ensure_state(
        _self: Any, _product: str, _instance_id: str, _desired: str, **kwargs: Any
    ) -> dict[str, Any]:
        captured.update(kwargs)
        return {"accepted": True, "state": "running", "provider_status": "running"}

    monkeypatch.setattr(controller.AutoDLProvider, "state", state)
    monkeypatch.setattr(controller.AutoDLProvider, "ensure_state", ensure_state)
    secret = "s" * 48
    app = controller.create_app(app_settings(token_file, secret, mutations_enabled=True))
    path = "/internal/v1/providers/autodl/instances/app/pro-a1/state"
    body = b'{"desired_state":"stopped","correlation_id":"profile-free-stop"}'
    timestamp = str(int(time.time()))
    nonce = "profile-free-stop-nonce"
    headers = {
        "Content-Type": "application/json",
        "X-GPU-Timestamp": timestamp,
        "X-GPU-Nonce": nonce,
        "X-GPU-Signature": sign_agent_request(
            "POST", path, body, timestamp, nonce, secret
        ),
    }
    with TestClient(app) as client:
        response = client.post(path, content=body, headers=headers)

    assert response.status_code == 200
    assert captured == {
        "observed": ("running", "running"),
        "bootstrap_profile": None,
    }
