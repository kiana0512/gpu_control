from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from packages.gpu_control_core.autodl import (
    APP_HOST,
    REMOTE_START_SCRIPT_SHA256,
    VERIFIED_REMOTE_START_COMMAND,
    AutoDLProvider,
    AutoDLProviderError,
    _safe_access_url,
)
from scripts import autodl_comfy_tunnel as tunnel


def test_provider_and_tunnel_pin_the_same_remote_start_script() -> None:
    payload = Path("scripts/autodl_remote_start_comfyui.sh").read_bytes()
    expected_digest = hashlib.sha256(payload).hexdigest()

    # The provider's power-on command, the tunnel upload verification and the
    # exact script shipped in the image are one restart-recovery contract.
    # Checking the source bytes prevents independently edited copied hashes
    # from drifting while still appearing equal to one another.
    assert REMOTE_START_SCRIPT_SHA256 == expected_digest
    assert tunnel.REMOTE_START_SCRIPT_SHA256 == expected_digest
    assert VERIFIED_REMOTE_START_COMMAND == tunnel.VERIFIED_REMOTE_START_COMMAND


def success(data: object, request: httpx.Request, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"code": "Success", "msg": "", "data": data, "request_id": "req-1"},
        request=request,
    )


@pytest.mark.asyncio
async def test_inventory_paginates_and_never_returns_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/wallet/balance"):
            return success({"assets": 12000, "voucher_balance": 500, "accumulate": 0}, request)
        if request.url.path.endswith("/list"):
            if request.url.host == "www.autodl.art" and body["page_index"] == 1:
                return success(
                    {
                        "list": [
                            {
                                "uuid": "pro-a1",
                                "name": "canary",
                                "status": "running",
                                "gpu_spec_uuid": "5090-p",
                                "req_gpu_amount": 1,
                            }
                        ],
                        "max_page": 2,
                    },
                    request,
                )
            return success(
                {"list": [], "max_page": 2 if request.url.host == "www.autodl.art" else 1}, request
            )
        if request.url.path.endswith("/snapshot"):
            return success(
                {
                    "proxy_host": "connect.westd.seetacloud.com",
                    "ssh_port": 12345,
                    "root_password": "must-not-leak",
                    "jupyter_domain": "example.autodl.com",
                    "usage_info": {
                        "root_fs_total_size": 1073741824,
                        "root_fs_used_size": 536870912,
                    },
                },
                request,
            )
        raise AssertionError(request.url)

    provider = AutoDLProvider(
        "secret-token", transport=httpx.MockTransport(handler), read_retries=0
    )
    try:
        inventory = await provider.inventory()
    finally:
        await provider.aclose()

    assert inventory["summary"] == {
        "total": 1,
        "running": 1,
        "transitioning": 0,
        "stopped": 0,
        "error": 0,
    }
    assert inventory["balance"]["available_yuan"] == 12
    assert inventory["instances"][0]["access"]["ssh"] == {
        "host": "connect.westd.seetacloud.com",
        "port": 12345,
        "username": "root",
    }
    encoded = json.dumps(inventory)
    assert "must-not-leak" not in encoded
    assert "secret-token" not in encoded
    app_list_pages = [
        json.loads(item.content)["page_index"]
        for item in requests
        if item.url.host == "www.autodl.art" and item.url.path.endswith("/list")
    ]
    assert app_list_pages == [1, 2]


@pytest.mark.asyncio
async def test_start_running_instance_is_read_only_idempotent() -> None:
    methods: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        assert request.url.host == APP_HOST.removeprefix("https://")
        assert request.url.path.endswith("/status")
        return success("running", request)

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler))
    try:
        result = await provider.ensure_state(
            "app", "pro-a1", "running", bootstrap_profile="comfyui-6006-v1"
        )
    finally:
        await provider.aclose()

    assert result["accepted"] is False
    assert result["state"] == "running"
    assert methods == [("GET", "/api/v1/adl_dev/dev/instance/pro/status")]


@pytest.mark.asyncio
async def test_unknown_state_refuses_power_mutation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return success("mystery", request)

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AutoDLProviderError, match="refusing power-on") as failure:
            await provider.ensure_state("app", "pro-a1", "running")
    finally:
        await provider.aclose()
    assert failure.value.code == "AUTODL_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_write_timeout_is_uncertain_and_is_never_retried() -> None:
    calls: list[str] = []
    power_on_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/status"):
            return success("shutdown", request)
        power_on_body.update(json.loads(request.content))
        raise httpx.ReadTimeout("ambiguous provider write", request=request)

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler), read_retries=3)
    try:
        with pytest.raises(AutoDLProviderError) as failure:
            await provider.ensure_state(
                "app",
                "pro-a1",
                "running",
                bootstrap_profile="comfyui-6006-v1",
            )
    finally:
        await provider.aclose()

    assert failure.value.uncertain is True
    assert calls.count("/api/v1/adl_dev/dev/instance/pro/power_on") == 1
    assert power_on_body == {
        "instance_uuid": "pro-a1",
        "payload": "gpu",
        "start_command": VERIFIED_REMOTE_START_COMMAND,
    }
    assert "/usr/bin/sha256sum --check --status" in VERIFIED_REMOTE_START_COMMAND


@pytest.mark.asyncio
async def test_stop_never_sends_bootstrap_command() -> None:
    power_off_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            return success("running", request)
        assert request.url.path.endswith("/power_off")
        power_off_body.update(json.loads(request.content))
        return success({}, request)

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler))
    try:
        result = await provider.ensure_state(
            "app", "pro-a1", "stopped", bootstrap_profile="comfyui-6006-v1"
        )
    finally:
        await provider.aclose()

    assert result["accepted"] is True
    assert power_off_body == {"instance_uuid": "pro-a1"}


@pytest.mark.asyncio
async def test_rejects_non_allowlisted_bootstrap_profile_before_provider_io() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError("invalid profile reached provider I/O")

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="not allowlisted"):
            await provider.ensure_state(
                "app",
                "pro-a1",
                "running",
                observed=("stopped", "shutdown"),
                bootstrap_profile="arbitrary-shell",  # type: ignore[arg-type]
            )
    finally:
        await provider.aclose()

    assert calls == []


@pytest.mark.asyncio
async def test_inventory_returns_partial_app_results_when_other_scopes_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wallet/balance") or request.url.host == "api.autodl.com":
            return httpx.Response(503, request=request)
        if request.url.path.endswith("/list"):
            return success(
                {
                    "list": [{"uuid": "pro-a1", "status": "shutdown"}],
                    "max_page": 1,
                },
                request,
            )
        if request.url.path.endswith("/snapshot"):
            return success({}, request)
        raise AssertionError(request.url)

    provider = AutoDLProvider("secret", transport=httpx.MockTransport(handler), read_retries=0)
    try:
        inventory = await provider.inventory()
    finally:
        await provider.aclose()

    assert inventory["summary"]["total"] == 1
    assert inventory["instances"][0]["state"] == "stopped"
    assert {item["scope"] for item in inventory["partial_errors"]} == {
        "balance",
        "pro",
    }


def test_access_urls_reject_embedded_credentials_and_untrusted_hosts() -> None:
    assert _safe_access_url("https://user:pass@example.autodl.com/path") is None
    assert _safe_access_url("https://autodl.com.attacker.invalid/path") is None
    assert _safe_access_url("example.autodl.com:8443") == "https://example.autodl.com:8443"
