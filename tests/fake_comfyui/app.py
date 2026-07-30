import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response


@dataclass
class Behavior:
    duration_seconds: float = 0.05
    upload_failure: bool = False
    validation_failure: bool = False
    execution_error: bool = False
    output_missing: bool = False
    node_offline: bool = False
    external_queue: bool = False
    disconnect_ws_once: bool = False
    interrupt_success: bool = True
    multiple_outputs: bool = False


@dataclass
class State:
    behavior: Behavior = field(default_factory=Behavior)
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    interrupted: bool = False
    ws_disconnected: bool = False


def create_app(state: State | None = None) -> FastAPI:
    fake = state or State()
    app = FastAPI(title="Fake ComfyUI")
    app.state.fake = fake

    @app.get("/system_stats")
    async def system_stats() -> dict[str, Any]:
        if fake.behavior.node_offline:
            raise HTTPException(503, "offline")
        return {
            "system": {"os": "fake"},
            "devices": [
                {"name": "Fake GPU", "vram_total": 25_000_000_000, "vram_free": 24_000_000_000}
            ],
        }

    @app.get("/object_info")
    async def object_info() -> dict[str, Any]:
        return {"LoadImage": {"input": {}}, "SaveImage": {"input": {}}, "KSampler": {"input": {}}}

    @app.get("/models/{folder}")
    async def models(folder: str) -> list[str]:
        return [f"fake/{folder}/model.safetensors"]

    async def upload(image: Annotated[UploadFile, File()]) -> dict[str, Any]:
        if fake.behavior.upload_failure:
            raise HTTPException(500, "simulated upload failure")
        content = await image.read()
        fake.files[image.filename or "upload.bin"] = content
        return {"name": image.filename, "subfolder": "", "type": "input"}

    app.post("/upload/image")(upload)
    app.post("/upload/mask")(upload)

    @app.post("/prompt")
    async def prompt(body: dict[str, Any]) -> dict[str, Any]:
        if fake.behavior.validation_failure:
            return {
                "error": {"type": "prompt_outputs_failed_validation"},
                "node_errors": {"1": {"errors": ["fake"]}},
            }
        prompt_id = str(uuid.uuid4())
        fake.prompts[prompt_id] = {
            "created": time.monotonic(),
            "prompt": body.get("prompt", {}),
            "client_id": body.get("client_id"),
        }
        return {"prompt_id": prompt_id, "number": len(fake.prompts), "node_errors": {}}

    def completed(prompt_id: str) -> bool:
        return (
            time.monotonic() - float(fake.prompts[prompt_id]["created"])
            >= fake.behavior.duration_seconds
        )

    @app.get("/queue")
    async def queue() -> dict[str, Any]:
        pending = [
            [0, prompt_id, item["prompt"], {"client_id": item["client_id"]}]
            for prompt_id, item in fake.prompts.items()
            if not completed(prompt_id)
        ]
        if fake.behavior.external_queue:
            pending.append([999, "external", {}, {"client_id": "not-gpu-control"}])
        return {"queue_running": pending[:1], "queue_pending": pending[1:]}

    def history_entry(prompt_id: str) -> dict[str, Any]:
        if prompt_id not in fake.prompts or not completed(prompt_id):
            return {}
        item = fake.prompts[prompt_id]
        prompt_record = [
            0,
            prompt_id,
            item["prompt"],
            {"client_id": item["client_id"]},
        ]
        if fake.behavior.execution_error:
            return {
                prompt_id: {
                    "status": {
                        "completed": False,
                        "status_str": "error",
                        "messages": [["execution_error", {"exception_message": "fake"}]],
                    },
                    "outputs": {},
                    "prompt": prompt_record,
                }
            }
        outputs: dict[str, Any] = {}
        if not fake.behavior.output_missing:
            images = [{"filename": f"{prompt_id}-0.png", "subfolder": "", "type": "output"}]
            if fake.behavior.multiple_outputs:
                images.append({"filename": f"{prompt_id}-1.png", "subfolder": "", "type": "output"})
            for item in images:
                fake.files[item["filename"]] = b"\x89PNG\r\n\x1a\nFAKE"
            outputs = {"9": {"images": images}}
        return {
            prompt_id: {
                "status": {"completed": True, "status_str": "success"},
                "outputs": outputs,
                "prompt": prompt_record,
            }
        }

    @app.get("/history/{prompt_id}")
    async def history(prompt_id: str) -> dict[str, Any]:
        return history_entry(prompt_id)

    @app.get("/history")
    async def all_history(max_items: int = 10_000) -> dict[str, Any]:
        completed_ids = [prompt_id for prompt_id in fake.prompts if completed(prompt_id)]
        selected = completed_ids[-max(1, max_items) :]
        return {
            prompt_id: history_entry(prompt_id)[prompt_id]
            for prompt_id in selected
        }

    @app.get("/view")
    async def view(filename: str) -> Response:
        if filename not in fake.files:
            raise HTTPException(404, "missing")
        return Response(fake.files[filename], media_type="image/png")

    @app.post("/interrupt")
    async def interrupt() -> dict[str, Any]:
        if not fake.behavior.interrupt_success:
            raise HTTPException(500, "interrupt failed")
        fake.interrupted = True
        return {"ok": True}

    @app.post("/free")
    async def free() -> dict[str, Any]:
        return {"ok": True, "models_unloaded": True}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        await socket.accept()
        if fake.behavior.disconnect_ws_once and not fake.ws_disconnected:
            fake.ws_disconnected = True
            await socket.close(code=1012)
            return
        try:
            while True:
                await asyncio.sleep(0.01)
                for prompt_id in list(fake.prompts):
                    if completed(prompt_id):
                        event_type = (
                            "execution_error"
                            if fake.behavior.execution_error
                            else "execution_success"
                        )
                        await socket.send_json(
                            {"type": event_type, "data": {"prompt_id": prompt_id}}
                        )
                        return
                    elapsed = time.monotonic() - float(fake.prompts[prompt_id]["created"])
                    progress = min(
                        100, int(elapsed / max(fake.behavior.duration_seconds, 0.001) * 100)
                    )
                    await socket.send_json(
                        {
                            "type": "progress",
                            "data": {"prompt_id": prompt_id, "value": progress, "max": 100},
                        }
                    )
        except WebSocketDisconnect:
            return

    return app


app = create_app()
