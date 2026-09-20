import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets

TRANSFER_CHUNK_BYTES = 1024 * 1024
CACHE_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CACHE_CONTROL_TIMEOUT_SECONDS = 3.0

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


def _file_identity(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size < 1:
        raise ComfyError("INPUT_INVALID", f"input file is empty: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(TRANSFER_CHUNK_BYTES), b""):
            digest.update(chunk)
    return size, digest.hexdigest()


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
        self.integrity_http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ComfyClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self.integrity_http is not None:
            await self.integrity_http.aclose()
        await self.http.aclose()

    def enable_remote_input_integrity(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Use a node-side digest service instead of downloading inputs again.

        The service is private to the Scheduler/Tunnel network and computes the
        digest on the GPU host.  A service failure falls back to the existing
        byte-for-byte readback, so this optimization cannot weaken the upload
        integrity fence.
        """
        if self.integrity_http is not None:
            raise RuntimeError("remote input integrity service is already configured")
        self.integrity_http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(10, connect=2),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            transport=transport,
        )

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
        expected_size, expected_sha256 = _file_identity(path)
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
                verification_method = "disabled"
                if verify:
                    verification_method = "readback"
                    if self.integrity_http is not None:
                        try:
                            remote_size, remote_sha256 = await self.remote_input_digest(
                                remote_name,
                                remote_subfolder,
                            )
                            verification_method = "remote_digest"
                        except ComfyError as exc:
                            if exc.code != "COMFY_REMOTE_DIGEST_UNAVAILABLE":
                                raise
                            remote_size, remote_sha256 = await self.remote_digest(
                                ComfyOutput(remote_name, remote_subfolder, "input"),
                                max_bytes=expected_size,
                            )
                            verification_method = "readback_fallback"
                    else:
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
                    "verification_method": verification_method,
                }
            except ComfyError as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))
        assert last_error is not None
        raise last_error

    async def upload_many(
        self,
        inputs: list[tuple[Path, bool]],
        *,
        subfolder: str = "",
        verify: bool = True,
        max_attempts: int = 3,
        max_concurrency: int = 4,
        cache_namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        """Upload independent job inputs concurrently and preserve input order.

        Every item still goes through :meth:`upload`, including overwrite-safe
        retries and a complete SHA-256 readback.  Waiting for every item before
        propagating the first input-ordered failure prevents background HTTP
        work from escaping the scheduler's pre-submit fence.
        """
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if cache_namespace is not None and not CACHE_ID_PATTERN.fullmatch(cache_namespace):
            raise ValueError("cache_namespace must be a lowercase SHA-256 digest")
        semaphore = asyncio.Semaphore(max_concurrency)

        async def upload_one(path: Path, mask: bool) -> dict[str, Any]:
            async with semaphore:
                if cache_namespace is not None and verify:
                    return await self.upload_cached(
                        path,
                        mask=mask,
                        subfolder=subfolder,
                        cache_namespace=cache_namespace,
                        max_attempts=max_attempts,
                    )
                return await self.upload(
                    path,
                    mask=mask,
                    subfolder=subfolder,
                    verify=verify,
                    max_attempts=max_attempts,
                )

        results = await asyncio.gather(
            *(upload_one(path, mask) for path, mask in inputs),
            return_exceptions=True,
        )
        uploaded: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            uploaded.append(result)
        return uploaded

    async def upload_cached(
        self,
        path: Path,
        *,
        cache_namespace: str,
        mask: bool = False,
        subfolder: str = "",
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Reuse one tenant-scoped remote input without changing its job path.

        A cache hit is accepted only after the materialized job file itself is
        checked against the local size and SHA-256. Any cache/control failure
        falls back to the ordinary overwrite upload and integrity fence.
        Promotion is best-effort and can never turn a verified upload into a
        failed inference request.
        """
        if not CACHE_ID_PATTERN.fullmatch(cache_namespace):
            raise ValueError("cache_namespace must be a lowercase SHA-256 digest")
        expected_size, expected_sha256 = _file_identity(path)
        materialized = await self._input_cache_request(
            "materialize",
            namespace=cache_namespace,
            digest=expected_sha256,
            size=expected_size,
            filename=path.name,
            subfolder=subfolder,
        )
        if materialized:
            verification_method: str | None = None
            if (
                materialized.get("verified") is True
                and type(materialized.get("size_bytes")) is int
                and materialized.get("size_bytes") == expected_size
                and isinstance(materialized.get("sha256"), str)
                and materialized.get("sha256") == expected_sha256
                and materialized.get("verification_method") == "atomic_target_sha256"
                and materialized.get("receipt_version") == 2
            ):
                # The tunnel hashes the same-directory temporary target and
                # atomically renames those verified bytes into the job path.
                # Its receipt therefore proves the final materialized input
                # without a second SSH command and a second full-file read.
                verification_method = "atomic_materialize_sha256"
            else:
                # Remain compatible with an older tunnel during a rolling
                # update. A legacy hit is not trusted without an independent
                # digest of the materialized job path.
                verification_method = await self._verified_remote_input(
                    path.name,
                    subfolder,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                )
            if verification_method is not None:
                return {
                    "name": path.name,
                    "subfolder": subfolder,
                    "type": "input",
                    "verified": True,
                    "size_bytes": expected_size,
                    "sha256": expected_sha256,
                    "attempt": 0,
                    "verification_method": verification_method,
                    "cache_hit": True,
                    "cache_promoted": False,
                }

        # On a miss, let the atomic promotion be the first integrity fence.
        # The tunnel hashes the copied candidate before publishing it, so a
        # separate remote digest here would read every unique input twice and
        # open an extra SSH command channel per file.
        uploaded = await self.upload(
            path,
            mask=mask,
            subfolder=subfolder,
            verify=False,
            max_attempts=max_attempts,
        )
        promoted = await self._input_cache_request(
            "promote",
            namespace=cache_namespace,
            digest=expected_sha256,
            size=expected_size,
            filename=str(uploaded.get("name") or path.name),
            subfolder=str(uploaded.get("subfolder") or subfolder),
        )
        if (
            promoted is not None
            and promoted.get("verified") is True
            and promoted.get("size_bytes") == expected_size
            and promoted.get("sha256") == expected_sha256
            and promoted.get("verification_method") == "atomic_cache_sha256"
            and promoted.get("receipt_version") == 2
        ):
            return {
                **uploaded,
                "verified": True,
                "verification_method": "atomic_promotion_sha256",
                "cache_hit": False,
                "cache_promoted": True,
            }

        # Rolling-update compatibility and cache-control failures retain the
        # original independent digest/readback fence. A mismatch never reaches
        # prompt submission: it is replaced by the ordinary verified upload.
        verification_method = await self._verified_remote_input(
            str(uploaded.get("name") or path.name),
            str(uploaded.get("subfolder") or subfolder),
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        if verification_method is None:
            uploaded = await self.upload(
                path,
                mask=mask,
                subfolder=subfolder,
                verify=True,
                max_attempts=max_attempts,
            )
        else:
            uploaded = {
                **uploaded,
                "verified": True,
                "verification_method": verification_method,
            }
        return {
            **uploaded,
            "cache_hit": False,
            "cache_promoted": promoted is not None,
        }

    async def _verified_remote_input(
        self,
        filename: str,
        subfolder: str,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> str | None:
        try:
            actual_size, actual_sha256 = await self.remote_input_digest(
                filename,
                subfolder,
            )
            method = "remote_digest_cache_hit"
        except ComfyError as exc:
            if exc.code != "COMFY_REMOTE_DIGEST_UNAVAILABLE":
                return None
            try:
                actual_size, actual_sha256 = await self.remote_digest(
                    ComfyOutput(filename, subfolder, "input"),
                    max_bytes=expected_size,
                )
            except ComfyError:
                return None
            method = "readback_cache_hit"
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            return None
        return method

    async def _input_cache_request(
        self,
        action: str,
        *,
        namespace: str,
        digest: str,
        size: int,
        filename: str,
        subfolder: str,
    ) -> dict[str, Any] | None:
        if self.integrity_http is None or action not in {"materialize", "promote"}:
            return None
        try:
            response = await self.integrity_http.post(
                f"/internal/v1/comfy-input-cache/{action}",
                json={
                    "namespace": namespace,
                    "sha256": digest,
                    "size_bytes": size,
                    "filename": filename,
                    "subfolder": subfolder,
                },
                timeout=CACHE_CONTROL_TIMEOUT_SECONDS,
            )
            if action == "materialize" and response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            expected_status = "hit" if action == "materialize" else "promoted"
            if isinstance(payload, dict) and payload.get("status") == expected_status:
                return payload
            return None
        except (httpx.HTTPError, ValueError, TypeError):
            return None

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
                async for chunk in response.aiter_bytes(chunk_size=TRANSFER_CHUNK_BYTES):
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

    async def remote_input_digest(self, filename: str, subfolder: str) -> tuple[int, str]:
        if self.integrity_http is None:
            raise ComfyError(
                "COMFY_REMOTE_DIGEST_UNAVAILABLE",
                "remote input integrity service is not configured",
            )
        try:
            response = await self.integrity_http.get(
                "/internal/v1/comfy-input-digest",
                params={"filename": filename, "subfolder": subfolder},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid digest response")
            size = payload.get("size_bytes")
            digest = payload.get("sha256")
            if (
                not isinstance(size, int)
                or size < 0
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("invalid digest response")
            return size, digest
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ComfyError(
                "COMFY_REMOTE_DIGEST_UNAVAILABLE",
                "remote input integrity service is unavailable",
                {"error_type": type(exc).__name__},
            ) from exc

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
        self,
        prompt_id: str,
        client_id: str,
        *,
        max_reconnects: int = 3,
        reconnect_deadline: float | None = None,
        history_poll_interval: float = 5,
    ) -> AsyncIterator[dict[str, Any]]:
        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/ws?clientId={client_id}"
        )
        if reconnect_deadline is not None:
            if history_poll_interval <= 0:
                raise ValueError("history_poll_interval must be positive")
            attempt = 0
            last_error: BaseException | None = None
            while True:
                remaining = reconnect_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ComfyError(
                        "COMFY_WS_DISCONNECTED",
                        "ComfyUI execution state remained unreachable until the recovery deadline",
                        {
                            "attempts": attempt,
                            "last_error_type": (
                                type(last_error).__name__ if last_error is not None else None
                            ),
                        },
                    )
                try:
                    async with websockets.connect(
                        ws_url,
                        open_timeout=max(0.1, min(5, remaining)),
                        ping_interval=20,
                    ) as socket:
                        while True:
                            remaining = reconnect_deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                break
                            try:
                                message = await asyncio.wait_for(
                                    socket.recv(),
                                    timeout=min(history_poll_interval, remaining),
                                )
                            except TimeoutError:
                                try:
                                    history = await self.history(prompt_id)
                                except ComfyError as exc:
                                    last_error = exc
                                    continue
                                if prompt_id in history:
                                    yield {
                                        "type": "history_recovered",
                                        "data": {
                                            "prompt_id": prompt_id,
                                            "history": history,
                                        },
                                    }
                                    return
                                continue
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
                    last_error = exc

                # A disconnected WebSocket is ambiguous: the accepted prompt
                # may still be running or may have completed while the tunnel
                # was down. Query only this persisted prompt id; never submit a
                # replacement prompt from the event recovery path.
                try:
                    history = await self.history(prompt_id)
                except ComfyError as exc:
                    last_error = exc
                else:
                    if prompt_id in history:
                        yield {
                            "type": "history_recovered",
                            "data": {
                                "prompt_id": prompt_id,
                                "history": history,
                            },
                        }
                        return

                remaining = reconnect_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    continue
                delay = min(2 ** min(attempt, max(0, max_reconnects)), 8, remaining)
                attempt += 1
                await asyncio.sleep(delay)
            return

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
                    yield {
                        "type": "history_recovered",
                        "data": {"prompt_id": prompt_id, "history": history},
                    }
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
                    async for chunk in response.aiter_bytes(chunk_size=TRANSFER_CHUNK_BYTES):
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
