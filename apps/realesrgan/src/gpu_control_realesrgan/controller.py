from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import asyncpg
import httpx
import jwt
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from PIL import Image

from .common import MODEL_NAME, MODEL_SHA256, ImageContractError, sha256_bytes, validate_png

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("gpu-control-realesrgan-controller")
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_QUEUE = int(os.environ.get("REALESRGAN_MAX_QUEUE", "64"))
QUEUE_TIMEOUT = float(os.environ.get("REALESRGAN_QUEUE_TIMEOUT_SECONDS", "120"))
INFERENCE_TIMEOUT = float(os.environ.get("REALESRGAN_INFERENCE_TIMEOUT_SECONDS", "300"))
CACHE_TTL = int(os.environ.get("REALESRGAN_CACHE_TTL_SECONDS", "600"))
INTERNAL_SECRET = os.environ.get("REALESRGAN_INTERNAL_SECRET", "")
API_KEY_PEPPER = os.environ.get("API_KEY_PEPPER", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
RESULT_ROOT = Path(os.environ.get("REALESRGAN_RESULT_ROOT", "/srv/gpu-control/realesrgan"))
TASK_HISTORY_PATH = RESULT_ROOT / "tasks.json"
TASK_HISTORY_LIMIT = 200
NODE_CONFIG = os.environ.get(
    "REALESRGAN_NODES",
    json.dumps(
        [
            {"id": "control-4090", "name": "4090", "url": "http://realesrgan-worker-4090:9301"},
            {"id": "worker-3090-a", "name": "3090-A", "url": "http://10.3.34.12:9301"},
            {"id": "worker-3090-b", "name": "3090-B", "url": "http://10.3.34.14:9301"},
            {
                "id": "worker-4070ti-animation-host-01",
                "name": "4070 Ti",
                "url": "http://10.3.34.238:9301",
            },
        ]
    ),
)


@dataclass
class NodeState:
    id: str
    name: str
    url: str
    ready: bool = False
    active: bool = False
    last_assigned: float = 0.0
    last_checked: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class CacheResult:
    fingerprint: str
    output: bytes
    headers: dict[str, str]


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, retryable: bool = False, headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.headers = headers or {}


def _postgres_dsn(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _request_id(request: Request, explicit: str | None = None) -> str:
    return explicit or request.headers.get("X-Request-ID") or f"realesrgan-{uuid.uuid4()}"


def _error(error: ApiError, request_id: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
                "retryable": error.retryable,
            }
        },
        status_code=error.status,
        headers={"X-Request-ID": request_id, **error.headers},
    )


def _parse_nodes() -> list[NodeState]:
    values = json.loads(NODE_CONFIG)
    return [NodeState(id=row["id"], name=row["name"], url=row["url"].rstrip("/")) for row in values]


def _normalize_strength(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def _cache_name(client_id: str, key: str) -> str:
    return hashlib.sha256(f"{client_id}\0{key}".encode()).hexdigest()


def _read_cache(client_id: str, key: str) -> CacheResult | None:
    name = _cache_name(client_id, key)
    metadata_path = RESULT_ROOT / f"{name}.json"
    output_path = RESULT_ROOT / f"{name}.png"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if time.time() - float(metadata["created_at"]) > CACHE_TTL:
            metadata_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            return None
        return CacheResult(
            fingerprint=str(metadata["fingerprint"]),
            output=output_path.read_bytes(),
            headers={str(k): str(v) for k, v in metadata["headers"].items()},
        )
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(client_id: str, key: str, result: CacheResult) -> None:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    name = _cache_name(client_id, key)
    output_path = RESULT_ROOT / f"{name}.png"
    metadata_path = RESULT_ROOT / f"{name}.json"
    temp_output = RESULT_ROOT / f".{name}.{uuid.uuid4().hex}.png.tmp"
    temp_meta = RESULT_ROOT / f".{name}.{uuid.uuid4().hex}.json.tmp"
    temp_output.write_bytes(result.output)
    temp_meta.write_text(
        json.dumps(
            {
                "fingerprint": result.fingerprint,
                "created_at": time.time(),
                "headers": result.headers,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temp_output.replace(output_path)
    temp_meta.replace(metadata_path)


def _load_task_history() -> deque[dict[str, Any]]:
    try:
        rows = json.loads(TASK_HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("task history must be a list")
        return deque((row for row in rows if isinstance(row, dict)), maxlen=TASK_HISTORY_LIMIT)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return deque(maxlen=TASK_HISTORY_LIMIT)


def _persist_task_history(rows: list[dict[str, Any]]) -> None:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = TASK_HISTORY_PATH.with_name(f".{TASK_HISTORY_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(TASK_HISTORY_PATH)


def _cleanup_expired_cache() -> None:
    now = time.time()
    for metadata_path in RESULT_ROOT.glob("*.json"):
        if metadata_path == TASK_HISTORY_PATH:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if now - float(metadata["created_at"]) <= CACHE_TTL:
                continue
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        metadata_path.unlink(missing_ok=True)
        metadata_path.with_suffix(".png").unlink(missing_ok=True)


async def _cache_cleanup_loop() -> None:
    while True:
        await asyncio.to_thread(_cleanup_expired_cache)
        await asyncio.sleep(min(max(CACHE_TTL / 2, 30), 300))


async def _create_task(app: FastAPI, **values: Any) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    row = {
        "request_id": values.pop("request_id"),
        "status": "QUEUED",
        "created_at": now,
        "finished_at": None,
        "node": None,
        "queue_ms": None,
        "processing_ms": None,
        "output_sha256": None,
        "cache": "MISS",
        "error_code": None,
        **values,
    }
    async with app.state.task_lock:
        app.state.tasks.append(row)
        snapshot = [dict(task) for task in app.state.tasks]
    await asyncio.to_thread(_persist_task_history, snapshot)
    return row


async def _update_task(app: FastAPI, row: dict[str, Any], **values: Any) -> None:
    async with app.state.task_lock:
        row.update(values)
        if row.get("status") in {"SUCCEEDED", "FAILED"} and not row.get("finished_at"):
            row["finished_at"] = datetime.now(UTC).isoformat()
        snapshot = [dict(task) for task in app.state.tasks]
    await asyncio.to_thread(_persist_task_history, snapshot)


async def _authenticate_api_key(app: FastAPI, value: str | None) -> str:
    if not value or not value.startswith("gpc_"):
        raise ApiError(401, "UNAUTHORIZED", "X-API-Key is missing or invalid")
    parts = value.split("_", 2)
    if len(parts) != 3:
        raise ApiError(401, "UNAUTHORIZED", "X-API-Key is missing or invalid")
    row = await app.state.db.fetchrow(
        """
        SELECT k.secret_hash, k.enabled AS key_enabled, k.expires_at,
               c.id AS client_id, c.enabled AS client_enabled, c.role
        FROM api_keys k JOIN api_clients c ON c.id = k.client_id
        WHERE k.prefix = $1
        """,
        parts[1],
    )
    expected = hmac.new(API_KEY_PEPPER.encode(), parts[2].encode(), hashlib.sha256).hexdigest()
    now = datetime.now(UTC)
    if (
        row is None
        or not row["key_enabled"]
        or not row["client_enabled"]
        or row["role"] != "client"
        or (row["expires_at"] is not None and row["expires_at"] <= now)
        or not hmac.compare_digest(str(row["secret_hash"] if row else ""), expected)
    ):
        raise ApiError(401, "UNAUTHORIZED", "X-API-Key is missing or invalid")
    await app.state.db.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE prefix = $1", parts[1]
    )
    return str(row["client_id"])


def _authenticate_admin(value: str | None) -> None:
    if not value or not value.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "Administrator token is required")
    try:
        payload = jwt.decode(value[7:], JWT_SECRET, algorithms=["HS256"])
        if payload.get("type", "access") != "access" or payload.get("role") not in {
            "admin",
            "operator",
            "viewer",
        }:
            raise ValueError("invalid role")
    except (jwt.PyJWTError, ValueError) as exc:
        raise ApiError(401, "UNAUTHORIZED", "Administrator token is invalid") from exc


async def _probe_loop(app: FastAPI) -> None:
    while True:
        for node in app.state.nodes:
            try:
                response = await app.state.http.get(
                    f"{node.url}/internal/v1/ready",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=5,
                )
                detail = response.json()
                node.detail = detail
                node.ready = response.status_code == 200 and bool(detail.get("ready"))
            except Exception as exc:
                node.ready = False
                node.detail = {"last_error": f"{type(exc).__name__}: {exc}"}
            node.last_checked = time.time()
        async with app.state.condition:
            app.state.condition.notify_all()
        await asyncio.sleep(5)


async def _acquire_node(app: FastAPI, target: str | None) -> NodeState:
    deadline = time.monotonic() + QUEUE_TIMEOUT
    async with app.state.condition:
        if app.state.waiting >= MAX_QUEUE:
            raise ApiError(
                429,
                "QUEUE_FULL",
                "Real-ESRGAN queue is full",
                True,
                {"Retry-After": "5"},
            )
        app.state.waiting += 1
        try:
            while True:
                candidates = [
                    node
                    for node in app.state.nodes
                    if node.ready and not node.active and (target is None or node.id == target)
                ]
                if candidates:
                    selected = min(candidates, key=lambda node: node.last_assigned)
                    selected.active = True
                    selected.last_assigned = time.monotonic()
                    return selected
                if target is not None and not any(node.id == target for node in app.state.nodes):
                    raise ApiError(422, "INVALID_NODE", "Requested compute node does not exist")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if not any(node.ready for node in app.state.nodes):
                        raise ApiError(503, "NO_GPU_AVAILABLE", "No ready CUDA node", True)
                    raise ApiError(504, "INFERENCE_TIMEOUT", "Queue wait exceeded 120 seconds", True)
                try:
                    await asyncio.wait_for(app.state.condition.wait(), timeout=min(5, remaining))
                except TimeoutError:
                    pass
        finally:
            app.state.waiting -= 1


async def _release_node(app: FastAPI, node: NodeState) -> None:
    async with app.state.condition:
        node.active = False
        app.state.condition.notify_all()


def _public_status(app: FastAPI) -> dict[str, Any]:
    nodes = []
    for node in app.state.nodes:
        detail = node.detail
        nodes.append(
            {
                "id": node.id,
                "name": node.name,
                "ready": node.ready,
                "active": 1 if node.active else 0,
                "capacity": 1,
                "device": detail.get("device"),
                "pytorch": detail.get("pytorch"),
                "cuda_runtime": detail.get("cuda_runtime"),
                "python": detail.get("python"),
                "precision": detail.get("precision"),
                "tile": detail.get("tile"),
                "tile_pad": detail.get("tile_pad"),
                "vram_free_mb": detail.get("vram_free_mb"),
                "vram_total_mb": detail.get("vram_total_mb"),
                "last_metrics": detail.get("last_metrics", {}),
                "last_error": detail.get("last_error"),
            }
        )
    ready_count = sum(bool(node["ready"]) for node in nodes)
    return {
        "status": "ready" if ready_count else "unready",
        "model": MODEL_NAME,
        "model_sha256": MODEL_SHA256,
        "image_version": "realesrgan-worker-1.0.0",
        "ready_nodes": ready_count,
        "total_nodes": len(nodes),
        "queue_depth": app.state.waiting,
        "max_queue": MAX_QUEUE,
        "capacity": ready_count,
        "nodes": nodes,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not all((INTERNAL_SECRET, API_KEY_PEPPER, JWT_SECRET, DATABASE_URL)):
        raise RuntimeError("Real-ESRGAN controller secrets/database are not configured")
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    app.state.nodes = _parse_nodes()
    app.state.waiting = 0
    app.state.condition = asyncio.Condition()
    app.state.inflight: dict[str, tuple[str, asyncio.Future[CacheResult]]] = {}
    app.state.idempotency_lock = asyncio.Lock()
    app.state.tasks = _load_task_history()
    app.state.task_lock = asyncio.Lock()
    app.state.db = await asyncpg.create_pool(_postgres_dsn(DATABASE_URL), min_size=1, max_size=4)
    app.state.http = httpx.AsyncClient(limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
    probe = asyncio.create_task(_probe_loop(app))
    cleanup = asyncio.create_task(_cache_cleanup_loop())
    await asyncio.sleep(0.1)
    yield
    probe.cancel()
    cleanup.cancel()
    await app.state.http.aclose()
    await app.state.db.close()


app = FastAPI(title="GPU Control Real-ESRGAN API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
    return _error(
        ApiError(422, "INVALID_STRENGTH", "strength must be a number between 0 and 1"),
        _request_id(request),
    )


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/api/v1/realesrgan/ready")
async def ready(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    request_id = _request_id(request)
    try:
        await _authenticate_api_key(app, x_api_key)
        payload = _public_status(app)
        status = 200 if payload["ready_nodes"] else 503
        if status == 503:
            return _error(ApiError(503, "NO_GPU_AVAILABLE", "No ready CUDA node", True), request_id)
        return JSONResponse(payload, headers={"X-Request-ID": request_id})
    except ApiError as exc:
        return _error(exc, request_id)


@app.get("/api/v1/realesrgan/capacity")
async def capacity(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Response:
    request_id = _request_id(request)
    try:
        await _authenticate_api_key(app, x_api_key)
        return JSONResponse(_public_status(app), headers={"X-Request-ID": request_id})
    except ApiError as exc:
        return _error(exc, request_id)


@app.get("/admin/realesrgan")
async def admin_status(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Response:
    request_id = _request_id(request)
    try:
        _authenticate_admin(authorization)
        payload = _public_status(app)
        payload["api"] = {
            "enhance": "/api/v1/realesrgan/enhance?strength=0.7",
            "ready": "/api/v1/realesrgan/ready",
            "capacity": "/api/v1/realesrgan/capacity",
            "content_type": "image/png",
            "authentication": "X-API-Key",
            "idempotency": "Idempotency-Key + X-Input-SHA256",
        }
        payload["tasks"] = [dict(task) for task in reversed(app.state.tasks)]
        return JSONResponse(payload, headers={"X-Request-ID": request_id})
    except ApiError as exc:
        return _error(exc, request_id)


@app.post("/api/v1/realesrgan/enhance")
async def enhance(
    request: Request,
    strength: float = 1.0,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    x_input_sha256: Annotated[str | None, Header(alias="X-Input-SHA256")] = None,
    x_target_node: Annotated[str | None, Header(alias="X-Target-Node")] = None,
) -> Response:
    request_id = _request_id(request, x_request_id)
    inflight_name: str | None = None
    owner = False
    task_record: dict[str, Any] | None = None
    try:
        client_id = await _authenticate_api_key(app, x_api_key)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "image/png":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be image/png")
        if not 0.0 <= strength <= 1.0:
            raise ApiError(422, "INVALID_STRENGTH", "strength must be between 0 and 1")
        if not idempotency_key:
            raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
        if len(idempotency_key) > 192:
            raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is too long")
        payload = await request.body()
        try:
            dimensions = validate_png(payload)
        except ImageContractError as exc:
            raise ApiError(exc.status_code, exc.code, exc.message) from exc
        input_sha = sha256_bytes(payload)
        if not x_input_sha256 or not hmac.compare_digest(x_input_sha256.lower(), input_sha):
            raise ApiError(400, "CHECKSUM_MISMATCH", "X-Input-SHA256 does not match request body", True)
        task_record = await _create_task(
            app,
            request_id=request_id,
            strength=float(_normalize_strength(strength)),
            width=dimensions.width,
            height=dimensions.height,
            input_sha256=input_sha,
        )
        fingerprint = sha256_bytes(
            f"{MODEL_NAME}\0{_normalize_strength(strength)}\0{input_sha}".encode()
        )
        cached = await asyncio.to_thread(_read_cache, client_id, idempotency_key)
        if cached:
            if cached.fingerprint != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "Idempotency key was used with different input or strength")
            headers = {**cached.headers, "X-Request-ID": request_id, "X-Idempotency-Cache": "HIT"}
            await _update_task(
                app,
                task_record,
                status="SUCCEEDED",
                node=headers.get("X-Compute-Node"),
                queue_ms=int(headers.get("X-Queue-Ms", "0")),
                processing_ms=int(headers.get("X-Processing-Ms", "0")),
                output_sha256=headers.get("X-Output-SHA256"),
                cache="HIT",
            )
            return Response(cached.output, media_type="image/png", headers=headers)

        inflight_name = _cache_name(client_id, idempotency_key)
        async with app.state.idempotency_lock:
            existing = app.state.inflight.get(inflight_name)
            if existing:
                if existing[0] != fingerprint:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "Idempotency key is processing different input or strength")
                future = existing[1]
            else:
                future = asyncio.get_running_loop().create_future()
                app.state.inflight[inflight_name] = (fingerprint, future)
                owner = True
        if not owner:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=QUEUE_TIMEOUT + INFERENCE_TIMEOUT)
            headers = {**result.headers, "X-Request-ID": request_id, "X-Idempotency-Cache": "COALESCED"}
            await _update_task(
                app,
                task_record,
                status="SUCCEEDED",
                node=headers.get("X-Compute-Node"),
                queue_ms=int(headers.get("X-Queue-Ms", "0")),
                processing_ms=int(headers.get("X-Processing-Ms", "0")),
                output_sha256=headers.get("X-Output-SHA256"),
                cache="COALESCED",
            )
            return Response(result.output, media_type="image/png", headers=headers)

        if strength == 0.0:
            await _update_task(app, task_record, status="RUNNING", node="bypass-strength-0", queue_ms=0)
            with Image.open(io.BytesIO(payload)) as image:
                rgba = image.convert("RGBA")
                buffer = io.BytesIO()
                rgba.save(buffer, format="PNG", compress_level=6)
                output = buffer.getvalue()
            processing_ms = 0
            queue_ms = 0
            node_name = "bypass-strength-0"
            worker_headers: dict[str, str] = {}
        else:
            queue_started = time.perf_counter()
            node = await _acquire_node(app, x_target_node)
            queue_ms = int(round((time.perf_counter() - queue_started) * 1000))
            await _update_task(app, task_record, status="RUNNING", node=node.name, queue_ms=queue_ms)
            try:
                response = await app.state.http.post(
                    f"{node.url}/internal/v1/enhance",
                    params={"strength": _normalize_strength(strength)},
                    headers={
                        "X-Internal-Secret": INTERNAL_SECRET,
                        "X-Request-ID": request_id,
                        "Content-Type": "image/png",
                    },
                    content=payload,
                    timeout=httpx.Timeout(INFERENCE_TIMEOUT),
                )
                if response.status_code != 200 or response.headers.get("content-type", "").split(";", 1)[0] != "image/png":
                    if response.status_code == 503:
                        node.ready = False
                        raise ApiError(503, "NO_GPU_AVAILABLE", "Selected CUDA worker became unavailable", True)
                    raise ApiError(500, "INFERENCE_FAILED", "CUDA worker inference failed", True)
                output = response.content
                expected = response.headers.get("X-Output-SHA256")
                if not expected or not hmac.compare_digest(expected, sha256_bytes(output)):
                    raise ApiError(500, "INFERENCE_FAILED", "Worker output checksum validation failed", True)
                out_dimensions = validate_png(output)
                if (out_dimensions.width, out_dimensions.height) != (dimensions.width, dimensions.height):
                    raise ApiError(500, "INFERENCE_FAILED", "Worker changed output dimensions")
                with Image.open(io.BytesIO(payload)) as source, Image.open(io.BytesIO(output)) as result_image:
                    source_alpha = source.convert("RGBA").getchannel("A").tobytes()
                    result_alpha = result_image.convert("RGBA").getchannel("A").tobytes()
                    if source_alpha != result_alpha:
                        raise ApiError(500, "INFERENCE_FAILED", "Worker changed alpha pixels")
                processing_ms = int(response.headers.get("X-Processing-Ms", "0"))
                node_name = node.name
                worker_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in {"x-peak-additional-vram-mb", "x-vram-free-after-mb"}
                }
            except httpx.TimeoutException as exc:
                raise ApiError(504, "INFERENCE_TIMEOUT", "GPU inference exceeded 300 seconds", True) from exc
            finally:
                await _release_node(app, node)
        output_sha = sha256_bytes(output)
        headers = {
            "X-Request-ID": request_id,
            "X-Compute-Node": node_name,
            "X-Model": MODEL_NAME,
            "X-Queue-Ms": str(queue_ms),
            "X-Processing-Ms": str(processing_ms),
            "X-Input-SHA256": input_sha,
            "X-Output-SHA256": output_sha,
            "X-Idempotency-Cache": "MISS",
            "Content-Length": str(len(output)),
            **worker_headers,
        }
        result = CacheResult(fingerprint=fingerprint, output=output, headers=headers)
        await asyncio.to_thread(_write_cache, client_id, idempotency_key, result)
        await _update_task(
            app,
            task_record,
            status="SUCCEEDED",
            node=node_name,
            queue_ms=queue_ms,
            processing_ms=processing_ms,
            output_sha256=output_sha,
            cache="MISS",
        )
        if inflight_name:
            async with app.state.idempotency_lock:
                row = app.state.inflight.pop(inflight_name, None)
                if row and not row[1].done():
                    row[1].set_result(result)
        log.info(
            "request complete request_id=%s input_sha256=%s output_sha256=%s node=%s queue_ms=%s processing_ms=%s",
            request_id,
            input_sha,
            output_sha,
            node_name,
            queue_ms,
            processing_ms,
        )
        return Response(output, media_type="image/png", headers=headers)
    except ApiError as exc:
        if task_record is not None:
            await _update_task(app, task_record, status="FAILED", error_code=exc.code)
        if owner and inflight_name:
            async with app.state.idempotency_lock:
                row = app.state.inflight.pop(inflight_name, None)
                if row and not row[1].done():
                    row[1].set_exception(exc)
                    row[1].add_done_callback(lambda future: future.exception())
        return _error(exc, request_id)
    except Exception:
        log.exception("unexpected request failure request_id=%s", request_id)
        if task_record is not None:
            await _update_task(app, task_record, status="FAILED", error_code="INFERENCE_FAILED")
        if owner and inflight_name:
            async with app.state.idempotency_lock:
                row = app.state.inflight.pop(inflight_name, None)
                if row and not row[1].done():
                    error = ApiError(500, "INFERENCE_FAILED", "Unexpected inference failure", True)
                    row[1].set_exception(error)
                    row[1].add_done_callback(lambda future: future.exception())
        return _error(ApiError(500, "INFERENCE_FAILED", "Unexpected inference failure", True), request_id)
