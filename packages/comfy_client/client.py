import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets


class ComfyError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ComfyOutput:
    filename: str
    subfolder: str
    kind: str


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = 5,
        read_timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        )

    async def __aenter__(self) -> "ComfyClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.aclose()

    async def _json(
        self, method: str, path: str, *, allow_empty: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            response = await self.http.request(method, path, **kwargs)
            response.raise_for_status()
            if allow_empty and not response.content:
                return {}
            try:
                value = response.json()
            except ValueError as exc:
                raise ComfyError(
                    "COMFY_INVALID_RESPONSE", f"{path} did not return valid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise ComfyError("COMFY_INVALID_RESPONSE", f"{path} did not return an object")
            return value
        except httpx.TimeoutException as exc:
            raise ComfyError("COMFY_TIMEOUT", f"ComfyUI timeout: {path}") from exc
        except httpx.HTTPStatusError as exc:
            details: dict[str, Any] = {"status": exc.response.status_code}
            try:
                details["response"] = exc.response.json()
            except ValueError:
                details["response"] = exc.response.text[:1000]
            raise ComfyError(
                "COMFY_HTTP_ERROR", f"ComfyUI returned {exc.response.status_code}", details
            ) from exc

    async def system_stats(self) -> dict[str, Any]:
        return await self._json("GET", "/system_stats")

    async def object_info(self) -> dict[str, Any]:
        return await self._json("GET", "/object_info")

    async def models(self, folder: str) -> list[str]:
        if not folder.replace("_", "").isalnum():
            raise ComfyError("INPUT_INVALID", "invalid model folder")
        value = await self.http.get(f"/models/{folder}")
        value.raise_for_status()
        payload = value.json()
        if not isinstance(payload, list):
            raise ComfyError("COMFY_INVALID_RESPONSE", "models response is not a list")
        return [str(item) for item in payload]

    async def queue(self) -> dict[str, Any]:
        return await self._json("GET", "/queue")

    async def history(self, prompt_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/history/{prompt_id}")

    async def upload(
        self, path: Path, *, mask: bool = False, subfolder: str = ""
    ) -> dict[str, Any]:
        endpoint = "/upload/mask" if mask else "/upload/image"
        with path.open("rb") as source:
            return await self._json(
                "POST",
                endpoint,
                files={"image": (path.name, source, "application/octet-stream")},
                data={"subfolder": subfolder, "overwrite": "false"},
            )

    async def submit(self, prompt: dict[str, Any], client_id: str) -> str:
        payload = await self._json(
            "POST", "/prompt", json={"prompt": prompt, "client_id": client_id}
        )
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str):
            raise ComfyError(
                "COMFY_VALIDATION_FAILED",
                "prompt_id missing",
                {"node_errors": payload.get("node_errors", {})},
            )
        return prompt_id

    async def interrupt(self) -> dict[str, Any]:
        return await self._json("POST", "/interrupt")

    async def free(self) -> dict[str, Any]:
        return await self._json(
            "POST",
            "/free",
            allow_empty=True,
            json={"unload_models": True, "free_memory": True},
        )

    async def events(
        self, prompt_id: str, client_id: str, *, max_reconnects: int = 3
    ) -> AsyncIterator[dict[str, Any]]:
        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/ws?clientId={client_id}"
        )
        for attempt in range(max_reconnects + 1):
            try:
                async with websockets.connect(ws_url, open_timeout=5, ping_interval=20) as socket:
                    async for message in socket:
                        if isinstance(message, bytes):
                            continue
                        payload = json.loads(message)
                        data = payload.get("data", {})
                        if data.get("prompt_id") not in {None, prompt_id}:
                            continue
                        yield payload
                        if payload.get("type") in {"execution_success", "execution_error"}:
                            return
            except (TimeoutError, OSError, websockets.WebSocketException) as exc:
                history = await self.history(prompt_id)
                if prompt_id in history:
                    yield {"type": "history_recovered", "data": {"prompt_id": prompt_id}}
                    return
                if attempt >= max_reconnects:
                    raise ComfyError(
                        "COMFY_WS_DISCONNECTED", "WebSocket reconnect limit exceeded"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 8))

    @staticmethod
    def outputs(
        history: dict[str, Any], prompt_id: str, output_nodes: set[str] | None = None
    ) -> list[ComfyOutput]:
        entry = history.get(prompt_id, {})
        result: list[ComfyOutput] = []
        for node_id, node in entry.get("outputs", {}).items():
            if output_nodes is not None and str(node_id) not in output_nodes:
                continue
            for key in ("images", "gifs", "audio"):
                for item in node.get(key, []):
                    result.append(
                        ComfyOutput(
                            filename=str(item["filename"]),
                            subfolder=str(item.get("subfolder", "")),
                            kind=str(item.get("type", "output")),
                        )
                    )
        return result

    async def download(
        self, output: ComfyOutput, destination: Path, max_bytes: int = 2_147_483_648
    ) -> tuple[int, str]:
        query = urlencode(
            {"filename": output.filename, "subfolder": output.subfolder, "type": output.kind}
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        total = 0
        digest = hashlib.sha256()
        try:
            async with self.http.stream("GET", f"/view?{query}") as response:
                response.raise_for_status()
                with os.fdopen(descriptor, "wb") as target:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ComfyError("OUTPUT_DOWNLOAD_FAILED", "output exceeds limit")
                        target.write(chunk)
                        digest.update(chunk)
                    target.flush()
                    os.fsync(target.fileno())
            os.replace(temporary, destination)
            return total, digest.hexdigest()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
