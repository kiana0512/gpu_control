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

# ComfyUI reports an operator/API interrupt as ``execution_interrupted``.
# It is a terminal websocket event just like success and execution_error; if
# the iterator keeps waiting after this event, a durably cancelled job holds
# its scheduler lease until the full workflow timeout expires.
TERMINAL_EXECUTION_EVENTS = frozenset(
    {"execution_success", "execution_error", "execution_interrupted"}
)


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
        self,
        method: str,
        path: str,
        *,
        allow_empty: bool = False,
        allow_non_json: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self.http.request(method, path, **kwargs)
            response.raise_for_status()
            if allow_empty and not response.content.strip():
                return {}
            try:
                value = response.json()
            except ValueError as exc:
                # ComfyUI's action endpoints are not consistent across builds:
                # /interrupt may acknowledge a successful request with an empty
                # body or plain text.  The HTTP status is the acknowledgement;
                # queue polling remains the authoritative drain check.
                if allow_non_json:
                    return {}
                raise ComfyError(
                    "COMFY_INVALID_RESPONSE", f"{path} did not return valid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise ComfyError("COMFY_INVALID_RESPONSE", f"{path} did not return an object")
            return value
        except httpx.TimeoutException as exc:
            raise ComfyError("COMFY_TIMEOUT", f"ComfyUI timeout: {path}") from exc
        except httpx.RequestError as exc:
            raise ComfyError(
                "COMFY_CONNECT_ERROR",
                f"ComfyUI request failed: {path}",
                {"error_type": type(exc).__name__},
            ) from exc
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

    async def prompt_ids_for_client(
        self, client_id: str, *, max_history_items: int = 10_000
    ) -> list[str]:
        """Find already accepted prompts for a deterministic submission id.

        This is the recovery half of the scheduler's submit intent protocol.
        Comfy assigns prompt ids, so after an ambiguous HTTP outcome we search
        both the live queue and retained history by the caller-controlled
        ``client_id`` before deciding whether a new submission is safe.
        """
        if not client_id or len(client_id) > 128:
            raise ValueError("client_id must contain 1..128 characters")
        if max_history_items < 1:
            raise ValueError("max_history_items must be positive")

        found: set[str] = set()
        queue = await self.queue()
        for section in ("queue_running", "queue_pending"):
            for item in queue.get(section, []):
                if not isinstance(item, list) or len(item) < 4:
                    continue
                metadata = item[3] if isinstance(item[3], dict) else {}
                if str(metadata.get("client_id", "")) == client_id:
                    found.add(str(item[1]))

        history = await self._json(
            "GET", "/history", params={"max_items": max_history_items}
        )
        for raw_prompt_id, raw_entry in history.items():
            if not isinstance(raw_entry, dict):
                continue
            metadata_candidates: list[dict[str, Any]] = []
            prompt = raw_entry.get("prompt")
            if isinstance(prompt, list) and len(prompt) > 3 and isinstance(prompt[3], dict):
                metadata_candidates.append(prompt[3])
            for key in ("extra_data", "metadata"):
                candidate = raw_entry.get(key)
                if isinstance(candidate, dict):
                    metadata_candidates.append(candidate)
            if any(str(item.get("client_id", "")) == client_id for item in metadata_candidates):
                found.add(str(raw_prompt_id))
        return sorted(found)

    async def upload(
        self,
        path: Path,
        *,
        mask: bool = False,
        subfolder: str = "",
        verify: bool = True,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Upload an input atomically enough for immediate Comfy execution.

        ComfyUI may create the destination before the request body has arrived.  If
        the connection then drops, a zero-byte file is left behind.  Retrying with
        ``overwrite=false`` reports success while preserving that corrupt file.
        Always overwrite the job-scoped destination and read it back before prompt
        submission so transport failures never consume an inference attempt.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        expected_size = path.stat().st_size
        if expected_size < 1:
            raise ComfyError("INPUT_INVALID", f"input file is empty: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        expected_sha256 = digest.hexdigest()
        endpoint = "/upload/mask" if mask else "/upload/image"
        last_error: ComfyError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with path.open("rb") as source:
                    uploaded = await self._json(
                        "POST",
                        endpoint,
                        files={"image": (path.name, source, "application/octet-stream")},
                        data={"subfolder": subfolder, "overwrite": "true"},
                    )
                remote_name = str(uploaded.get("name") or path.name)
                remote_subfolder = str(uploaded.get("subfolder") or subfolder)
                if verify:
                    remote_size, remote_sha256 = await self.remote_digest(
                        ComfyOutput(remote_name, remote_subfolder, "input"),
                        max_bytes=expected_size,
                    )
                    if remote_size != expected_size or remote_sha256 != expected_sha256:
                        raise ComfyError(
                            "COMFY_UPLOAD_INTEGRITY_FAILED",
                            "uploaded input differs from the source",
                            {
                                "filename": remote_name,
                                "subfolder": remote_subfolder,
                                "expected_size": expected_size,
                                "actual_size": remote_size,
                                "expected_sha256": expected_sha256,
                                "actual_sha256": remote_sha256,
                                "attempt": attempt,
                            },
                        )
                return {
                    **uploaded,
                    "verified": verify,
                    "size_bytes": expected_size,
                    "sha256": expected_sha256,
                    "attempt": attempt,
                }
            except ComfyError as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))
        assert last_error is not None
        raise last_error

    async def remote_digest(
        self, output: ComfyOutput, *, max_bytes: int = 2_147_483_648
    ) -> tuple[int, str]:
        query = urlencode(
            {"filename": output.filename, "subfolder": output.subfolder, "type": output.kind}
        )
        total = 0
        digest = hashlib.sha256()
        try:
            async with self.http.stream("GET", f"/view?{query}") as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ComfyError(
                            "COMFY_UPLOAD_INTEGRITY_FAILED",
                            "uploaded input exceeds the source size",
                        )
                    digest.update(chunk)
        except httpx.TimeoutException as exc:
            raise ComfyError("COMFY_TIMEOUT", "ComfyUI input verification timed out") from exc
        except httpx.RequestError as exc:
            raise ComfyError(
                "COMFY_CONNECT_ERROR",
                "ComfyUI input verification failed",
                {"error_type": type(exc).__name__},
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ComfyError(
                "COMFY_UPLOAD_INTEGRITY_FAILED",
                f"ComfyUI input verification returned {exc.response.status_code}",
            ) from exc
        return total, digest.hexdigest()

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
        return await self._json(
            "POST",
            "/interrupt",
            allow_empty=True,
            allow_non_json=True,
        )

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
                        if payload.get("type") in TERMINAL_EXECUTION_EVENTS:
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
