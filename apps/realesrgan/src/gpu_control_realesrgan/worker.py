from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import torch
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .common import MODEL_NAME, sha256_bytes
from .model import RealESRGANRuntime

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("gpu-control-realesrgan-worker")

NODE_ID = os.environ.get("NODE_ID", "unknown-node")
DISPLAY_NAME = os.environ.get("NODE_DISPLAY_NAME", NODE_ID)
INTERNAL_SECRET = os.environ.get("REALESRGAN_INTERNAL_SECRET", "")
MODEL_PATH = os.environ.get(
    "REALESRGAN_MODEL_PATH", "/opt/realesrgan/models/RealESRGAN_x4plus_anime_6B.pth"
)
TILE = int(os.environ.get("REALESRGAN_TILE", "256"))
TILE_PAD = int(os.environ.get("REALESRGAN_TILE_PAD", "16"))


def _authorized(value: str | None) -> bool:
    return bool(INTERNAL_SECRET) and bool(value) and hmac.compare_digest(value, INTERNAL_SECRET)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.lock = asyncio.Lock()
    app.state.runtime = RealESRGANRuntime(MODEL_PATH, TILE, TILE_PAD)
    try:
        await asyncio.to_thread(app.state.runtime.load)
        log.info("worker ready node=%s device=%s", NODE_ID, app.state.runtime.status()["device"])
    except Exception as exc:
        app.state.runtime.last_error = f"{type(exc).__name__}: {exc}"
        log.exception("worker startup probe failed node=%s", NODE_ID)
    yield
    await asyncio.to_thread(app.state.runtime.unload)


app = FastAPI(title="GPU Control Real-ESRGAN Worker", version="1.0.0", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live", "node_id": NODE_ID}


@app.get("/internal/v1/ready")
async def ready(
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> JSONResponse:
    if not _authorized(x_internal_secret):
        raise HTTPException(401, detail="unauthorized")
    status = app.state.runtime.status()
    status.update({"node_id": NODE_ID, "display_name": DISPLAY_NAME, "active": app.state.lock.locked()})
    return JSONResponse(status, status_code=200 if status["ready"] else 503)


@app.post("/internal/v1/enhance")
async def enhance(
    request: Request,
    strength: float = 1.0,
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> Response:
    if not _authorized(x_internal_secret):
        raise HTTPException(401, detail="unauthorized")
    runtime: RealESRGANRuntime = app.state.runtime
    if not runtime.ready:
        return JSONResponse({"error": "CUDA worker is unready"}, status_code=503)
    if not 0.0 <= strength <= 1.0:
        return JSONResponse({"error": "invalid strength"}, status_code=422)
    payload = await request.body()
    async with app.state.lock:
        attempts = 0
        while True:
            attempts += 1
            try:
                output, metrics = await asyncio.to_thread(runtime.enhance_png, payload, strength)
                break
            except torch.cuda.OutOfMemoryError as exc:
                runtime.last_error = f"CUDA OOM attempt {attempts}: {exc}"
                torch.cuda.empty_cache()
                if attempts >= 2:
                    runtime.ready = False
                    log.exception("worker CUDA OOM node=%s request_id=%s", NODE_ID, x_request_id)
                    return JSONResponse({"error": "CUDA OOM"}, status_code=500)
            except Exception as exc:
                runtime.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("worker inference failed node=%s request_id=%s", NODE_ID, x_request_id)
                return JSONResponse({"error": "inference failed"}, status_code=500)
    headers = {
        "X-Request-ID": x_request_id or "",
        "X-Compute-Node": DISPLAY_NAME,
        "X-Compute-Node-ID": NODE_ID,
        "X-Model": MODEL_NAME,
        "X-Processing-Ms": str(metrics["processing_ms"]),
        "X-Peak-Additional-VRAM-MB": str(metrics["peak_additional_mb"]),
        "X-VRAM-Free-After-MB": str(metrics["vram_free_after_mb"]),
        "X-Output-SHA256": sha256_bytes(output),
        "Content-Length": str(len(output)),
    }
    return Response(output, media_type="image/png", headers=headers)
