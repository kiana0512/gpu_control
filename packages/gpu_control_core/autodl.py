from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx

AutoDLProduct = Literal["app", "pro"]
AutoDLBootstrapProfile = Literal["comfyui-6006-v1"]

API_HOST = "https://api.autodl.com"
APP_HOST = "https://www.autodl.art"
PRODUCT_HOST: dict[AutoDLProduct, str] = {"app": APP_HOST, "pro": API_HOST}
PRODUCT_PREFIX: dict[AutoDLProduct, str] = {
    "app": "/api/v1/adl_dev/dev/instance/pro",
    "pro": "/api/v1/dev/instance/pro",
}
INSTANCE_ID_PATTERN = re.compile(r"^pro-[A-Za-z0-9]+$")
SAFE_ACCESS_HOSTS = (".autodl.com", ".autodl.art", ".seetacloud.com")
REMOTE_START_SCRIPT_PATH = "/root/autodl-tmp/gpu-control/start-comfyui.sh"
# Keep this pin in lockstep with scripts/autodl_remote_start_comfyui.sh and
# scripts/autodl_comfy_tunnel.py.  The contract test hashes the shipped script
# and compares all three values, so a bootstrap release cannot silently send a
# start command which the tunnel will reject after a future power-on.
REMOTE_START_SCRIPT_SHA256 = "2ce355754406f7cccf271668a11a907821620845774bf97a651f37d876f66d72"
VERIFIED_REMOTE_START_COMMAND = (
    f"echo '{REMOTE_START_SCRIPT_SHA256}  {REMOTE_START_SCRIPT_PATH}' | "
    "/usr/bin/sha256sum --check --status - && "
    f"/bin/bash {REMOTE_START_SCRIPT_PATH}"
)
BOOTSTRAP_START_COMMANDS: dict[AutoDLBootstrapProfile, str] = {
    "comfyui-6006-v1": VERIFIED_REMOTE_START_COMMAND,
}

STATE_MAP = {
    "running": "running",
    "shutdown": "stopped",
    "stopped": "stopped",
    "off": "stopped",
    "starting": "starting",
    "booting": "starting",
    "scheduling": "starting",
    "pending": "starting",
    "creating": "creating",
    "stopping": "stopping",
    "error": "error",
    "failed": "error",
}


class AutoDLProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        request_id: str = "",
        uncertain: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.uncertain = uncertain
        self.status_code = status_code


def read_autodl_token(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("AutoDL token file is unreadable") from exc
    if not token or any(character.isspace() for character in token):
        raise ValueError("AutoDL token file is invalid")
    return token


def normalize_state(provider_status: str) -> str:
    return STATE_MAP.get(provider_status.strip().lower(), "unknown")


def validate_ref(product: str, instance_id: str) -> tuple[AutoDLProduct, str]:
    if product not in PRODUCT_HOST:
        raise ValueError("AutoDL product must be app or pro")
    if INSTANCE_ID_PATTERN.fullmatch(instance_id) is None:
        raise ValueError("AutoDL instance ID is invalid")
    return product, instance_id  # type: ignore[return-value]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _bytes_to_gib(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return round(number / (1024**3), 2)


def _milliyuan_to_yuan(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else round(number / 1000, 3)


def _safe_access_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        return None
    if parsed.scheme != "https" or not any(
        hostname == suffix[1:] or hostname.endswith(suffix) for suffix in SAFE_ACCESS_HOSTS
    ):
        return None
    return candidate


def _safe_ssh(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    host = str(snapshot.get("proxy_host") or "").strip().lower()
    port = _number(snapshot.get("ssh_port"))
    if (
        not host
        or port is None
        or not 1 <= port <= 65535
        or not any(host == suffix[1:] or host.endswith(suffix) for suffix in SAFE_ACCESS_HOSTS)
    ):
        return None
    return {"host": host, "port": int(port), "username": "root"}


class AutoDLProvider:
    """Async AutoDL inventory/executor used by the authenticated admin API.

    This class deliberately owns no leases, scheduling policy, or background
    reconciliation. Mutations are fail-closed for unknown and transitional
    states; the GPU Control API supplies serialization and audit records.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 20,
        read_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("AutoDL token must not be empty")
        self._token = token.strip()
        self._read_retries = max(0, read_retries)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        host: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        attempts = 1 + (self._read_retries if read_only else 0)
        last_error: AutoDLProviderError | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    host.rstrip("/") + path,
                    headers={"Authorization": self._token, "Content-Type": "application/json"},
                    json=body,
                    params={key: value for key, value in (query or {}).items() if value != ""},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = AutoDLProviderError(
                    "AutoDL network request failed",
                    uncertain=not read_only,
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                raise last_error from exc

            payload: dict[str, Any]
            try:
                decoded = response.json()
                payload = decoded if isinstance(decoded, dict) else {}
            except ValueError:
                payload = {}
            request_id = str(payload.get("request_id") or "")
            code = str(payload.get("code") or "")
            if response.is_success and code == "Success":
                return payload

            message = str(payload.get("msg") or f"AutoDL HTTP {response.status_code}")
            retryable_status = read_only and (
                response.status_code == 429 or response.status_code >= 500
            )
            last_error = AutoDLProviderError(
                message[:300],
                code=code,
                request_id=request_id,
                uncertain=not read_only and response.status_code >= 500,
                status_code=response.status_code,
            )
            if retryable_status and attempt + 1 < attempts:
                await asyncio.sleep(min(2**attempt, 4))
                continue
            raise last_error
        assert last_error is not None
        raise last_error

    async def _balance(self) -> dict[str, float]:
        payload = await self._request(
            "POST",
            API_HOST,
            "/api/v1/dev/wallet/balance",
            body={},
            read_only=True,
        )
        raw_data = payload.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        return {
            "available_yuan": _milliyuan_to_yuan(data.get("assets")) or 0,
            "voucher_yuan": _milliyuan_to_yuan(data.get("voucher_balance")) or 0,
            "accumulated_yuan": _milliyuan_to_yuan(data.get("accumulate")) or 0,
        }

    async def _list_product(self, product: AutoDLProduct) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._request(
                "POST",
                PRODUCT_HOST[product],
                PRODUCT_PREFIX[product] + "/list",
                body={"page_index": page, "page_size": 100},
                read_only=True,
            )
            raw_data = payload.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            raw_rows = data.get("list")
            rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
            items.extend(row for row in rows if isinstance(row, dict))
            max_page = int(_number(data.get("max_page")) or 1)
            if page >= max_page or not rows:
                return items
            page += 1
            if page > 1000:
                raise AutoDLProviderError("AutoDL inventory pagination exceeded safety limit")

    async def _snapshot(self, product: AutoDLProduct, instance_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            PRODUCT_HOST[product],
            PRODUCT_PREFIX[product] + "/snapshot",
            query={"instance_uuid": instance_id},
            read_only=True,
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def state(self, product: AutoDLProduct, instance_id: str) -> tuple[str, str]:
        product, instance_id = validate_ref(product, instance_id)
        payload = await self._request(
            "GET",
            PRODUCT_HOST[product],
            PRODUCT_PREFIX[product] + "/status",
            query={"instance_uuid": instance_id},
            read_only=True,
        )
        provider_status = str(payload.get("data") or "unknown")
        return normalize_state(provider_status), provider_status

    async def ssh_credentials(self, product: AutoDLProduct, instance_id: str) -> dict[str, Any]:
        product, instance_id = validate_ref(product, instance_id)
        state, provider_status = await self.state(product, instance_id)
        if state != "running":
            raise AutoDLProviderError(
                f"SSH is unavailable while provider state is {provider_status}",
                code="AUTODL_SSH_UNAVAILABLE",
                status_code=409,
            )
        snapshot = await self._snapshot(product, instance_id)
        ssh = _safe_ssh(snapshot)
        password = str(snapshot.get("root_password") or "")
        if ssh is None or not password:
            raise AutoDLProviderError(
                "AutoDL did not return complete SSH credentials",
                code="AUTODL_SSH_UNAVAILABLE",
                status_code=409,
            )
        return {
            **ssh,
            "password": password,
            "instance_id": instance_id,
            "product": product,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def inventory(self) -> dict[str, Any]:
        outcomes = await asyncio.gather(
            self._balance(),
            self._list_product("app"),
            self._list_product("pro"),
            return_exceptions=True,
        )
        errors: list[dict[str, str]] = []
        balance: dict[str, float] = {
            "available_yuan": 0,
            "voucher_yuan": 0,
            "accumulated_yuan": 0,
        }
        app_rows: list[dict[str, Any]] = []
        pro_rows: list[dict[str, Any]] = []
        labels = ("balance", "app", "pro")
        for index, (label, outcome) in enumerate(zip(labels, outcomes, strict=True)):
            if isinstance(outcome, BaseException):
                code = (
                    outcome.code
                    if isinstance(outcome, AutoDLProviderError) and outcome.code
                    else "AUTODL_READ_FAILED"
                )
                errors.append({"scope": label, "code": code})
            elif label == "balance":
                balance = cast(dict[str, float], outcomes[index])
            elif label == "app":
                app_rows = cast(list[dict[str, Any]], outcomes[index])
            else:
                pro_rows = cast(list[dict[str, Any]], outcomes[index])
        if errors and not app_rows and not pro_rows:
            raise AutoDLProviderError(
                "AutoDL inventory is unavailable",
                code="AUTODL_INVENTORY_UNAVAILABLE",
                status_code=503,
            )
        rows: list[tuple[AutoDLProduct, dict[str, Any]]] = [("app", item) for item in app_rows] + [
            ("pro", item) for item in pro_rows
        ]
        semaphore = asyncio.Semaphore(6)

        async def render(product: AutoDLProduct, row: dict[str, Any]) -> dict[str, Any]:
            instance_id = str(row.get("uuid") or row.get("instance_uuid") or "")
            snapshot: dict[str, Any] = {}
            snapshot_error: str | None = None
            if INSTANCE_ID_PATTERN.fullmatch(instance_id):
                async with semaphore:
                    try:
                        snapshot = await self._snapshot(product, instance_id)
                    except AutoDLProviderError as exc:
                        snapshot_error = exc.code or "SNAPSHOT_UNAVAILABLE"
            provider_status = str(row.get("status") or row.get("sub_status") or "unknown")
            raw_usage = snapshot.get("usage_info")
            usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
            application = (
                row.get("cg_application_info") or snapshot.get("cg_application_info") or {}
            )
            if not isinstance(application, dict):
                application = {}
            disk_total = _bytes_to_gib(
                usage.get("root_fs_total_size") or snapshot.get("expand_system_disk_size")
            )
            disk_used = _bytes_to_gib(usage.get("root_fs_used_size"))
            disk_percent = (
                round(disk_used / disk_total * 100, 1)
                if disk_used is not None and disk_total is not None and disk_total != 0
                else None
            )
            return {
                "ref": f"{product}:{instance_id}",
                "product": product,
                "instance_id": instance_id,
                "name": str(row.get("name") or row.get("instance_name") or ""),
                "state": normalize_state(provider_status),
                "provider_status": provider_status,
                "region": str(row.get("region_name") or row.get("region_sign") or ""),
                "gpu_spec": str(
                    row.get("gpu_spec_uuid") or snapshot.get("snapshot_gpu_alias_name") or ""
                ),
                "gpu_count": int(_number(row.get("req_gpu_amount")) or 0),
                "charge_type": str(row.get("charge_type") or ""),
                "price_yuan_per_hour": _milliyuan_to_yuan(
                    snapshot.get("payg_price") or row.get("payg_price")
                ),
                "disk_total_gib": disk_total,
                "disk_used_gib": disk_used,
                "disk_used_percent": disk_percent,
                "cpu_percent": _number(usage.get("cpu_usage_percent")),
                "memory_percent": _number(usage.get("mem_usage_percent")),
                "application": {
                    "name": str(application.get("application_name") or ""),
                    "version": str(
                        application.get("current_version")
                        or application.get("application_version")
                        or ""
                    ),
                },
                "access": {
                    "jupyter": _safe_access_url(snapshot.get("jupyter_domain")),
                    "service_6006": _safe_access_url(snapshot.get("service_6006_domain")),
                    "service_6008": _safe_access_url(snapshot.get("service_6008_domain")),
                    "ssh": _safe_ssh(snapshot),
                },
                "created_at": str(row.get("created_at") or ""),
                "started_at": row.get("started_at"),
                "timed_shutdown_at": row.get("timed_shutdown_at"),
                "snapshot_error": snapshot_error,
            }

        instances = await asyncio.gather(*(render(product, row) for product, row in rows))
        order = {"running": 0, "starting": 1, "creating": 2, "stopping": 3, "stopped": 4}
        instances.sort(key=lambda item: (order.get(item["state"], 9), item["instance_id"]))
        states = [item["state"] for item in instances]
        return {
            "configured": True,
            "provider": "AutoDL",
            "synced_at": datetime.now(UTC).isoformat(),
            "balance": balance,
            "summary": {
                "total": len(instances),
                "running": states.count("running"),
                "transitioning": sum(
                    state in {"starting", "creating", "stopping"} for state in states
                ),
                "stopped": states.count("stopped"),
                "error": sum(state in {"error", "unknown"} for state in states),
            },
            "partial_errors": errors,
            "instances": instances,
        }

    async def ensure_state(
        self,
        product: AutoDLProduct,
        instance_id: str,
        desired: Literal["running", "stopped"],
        *,
        observed: tuple[str, str] | None = None,
        bootstrap_profile: AutoDLBootstrapProfile | None = None,
    ) -> dict[str, Any]:
        product, instance_id = validate_ref(product, instance_id)
        if bootstrap_profile is not None and bootstrap_profile not in BOOTSTRAP_START_COMMANDS:
            raise ValueError("AutoDL bootstrap profile is not allowlisted")
        current, provider_status = observed or await self.state(product, instance_id)
        if desired == "running":
            if current == "running":
                return {"accepted": False, "state": current, "provider_status": provider_status}
            if current in {"starting", "creating"}:
                return {"accepted": False, "state": current, "provider_status": provider_status}
            if current != "stopped":
                raise AutoDLProviderError(
                    f"refusing power-on from provider state {provider_status}",
                    code="AUTODL_STATE_CONFLICT",
                    status_code=409,
                )
            path = "/power_on"
            body = {"instance_uuid": instance_id, "payload": "gpu"}
            if bootstrap_profile is not None:
                body["start_command"] = BOOTSTRAP_START_COMMANDS[bootstrap_profile]
        else:
            if current == "stopped":
                return {"accepted": False, "state": current, "provider_status": provider_status}
            if current == "stopping":
                return {"accepted": False, "state": current, "provider_status": provider_status}
            if current != "running":
                raise AutoDLProviderError(
                    f"refusing power-off from provider state {provider_status}",
                    code="AUTODL_STATE_CONFLICT",
                    status_code=409,
                )
            path = "/power_off"
            body = {"instance_uuid": instance_id}
        payload = await self._request(
            "POST",
            PRODUCT_HOST[product],
            PRODUCT_PREFIX[product] + path,
            body=body,
        )
        return {
            "accepted": True,
            "state": current,
            "provider_status": provider_status,
            "request_id": str(payload.get("request_id") or ""),
        }
