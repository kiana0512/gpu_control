#!/usr/bin/env python3
"""Run the staged four-image ModelView inpaint workflow on remote ComfyUI."""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, cast

from PIL import Image

ROOT = Path("/root/autodl-tmp/gpu-control/modelview-inpaint-normal-2step-r1")
COMFY = "http://127.0.0.1:6006"


def request_json(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310
        COMFY + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return cast(dict[str, Any], json.load(response))


def main() -> None:
    template = json.loads((ROOT / "workflow/template.api.json").read_text())
    assert template["15"]["inputs"] == {
        "denoise": 1,
        "model": ["78", 0],
        "scheduler": "simple",
        "steps": 2,
    }
    assert template["21"]["inputs"]["strength_model"] == 1
    assert template["78"]["inputs"]["strength_model"] == 0.8
    assert template["77"]["inputs"]["pixels"] == ["79", 0]
    assert template["74"]["inputs"]["latent"] == ["77", 0]
    assert template["29"]["inputs"]["images"] == ["33", 1]

    remote_input = "gpu-control-canary-normal-2step"
    bindings = {
        "73": f"{remote_input}/image-canary.png",
        "5": f"{remote_input}/material_image-canary.png",
        "44": f"{remote_input}/mask-canary.png",
        "79": f"{remote_input}/normal_image-canary.png",
    }
    for node_id, filename in bindings.items():
        template[node_id]["inputs"]["image"] = filename
    template["14"]["inputs"]["noise_seed"] = 4500
    output_prefix = "gpu-control-autodl-5090-normal-" + uuid.uuid4().hex
    template["29"]["inputs"]["filename_prefix"] = output_prefix
    client_id = "gpu-control-autodl-canary-" + uuid.uuid4().hex

    started = time.monotonic()
    submitted = request_json("POST", "/prompt", {"prompt": template, "client_id": client_id})
    if submitted.get("node_errors"):
        raise RuntimeError(json.dumps(submitted["node_errors"], ensure_ascii=False))
    prompt_id = str(submitted["prompt_id"])
    history: dict[str, Any] | None = None
    deadline = time.monotonic() + 2400
    while time.monotonic() < deadline:
        payload = request_json("GET", "/history/" + prompt_id)
        item = payload.get(prompt_id)
        if isinstance(item, dict):
            history = item
            break
        time.sleep(2)
    if history is None:
        raise TimeoutError("ComfyUI canary did not finish in 2400 seconds")
    status = history.get("status")
    if not isinstance(status, dict) or status.get("status_str") != "success":
        raise RuntimeError(json.dumps(status, ensure_ascii=False))
    outputs = history.get("outputs")
    node_output = outputs.get("29") if isinstance(outputs, dict) else None
    images = node_output.get("images") if isinstance(node_output, dict) else None
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise RuntimeError("final output node 29 did not return exactly one image")
    query = urllib.parse.urlencode(images[0])
    with urllib.request.urlopen(COMFY + "/view?" + query, timeout=120) as response:  # noqa: S310
        result = response.read()
    image = Image.open(io.BytesIO(result))
    image.load()
    if image.size != (2048, 2048):
        raise RuntimeError(f"unexpected output dimensions: {image.size}")
    evidence = ROOT / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    output_path = evidence / "autodl-5090-modelview-normal-canary.png"
    output_path.write_bytes(result)
    input_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "input").glob("*.png"))
    }
    report = {
        "schema_version": "gpu-control.autodl-modelview-canary.v1",
        "status": "PASSED",
        "instance_id": "pro-7894be501780",
        "gpu": "RTX 5090",
        "workflow": "modelview-inpaint",
        "workflow_version": "2026.09.18-refcontrol-normal-2step-r1",
        "prompt_id": prompt_id,
        "seed": 4500,
        "normal_input": True,
        "base_lora_strength": 1,
        "normal_lora_strength": 0.8,
        "denoise": 1,
        "steps": 2,
        "input_sha256": input_hashes,
        "output_sha256": hashlib.sha256(result).hexdigest(),
        "output_size": list(image.size),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    (evidence / "autodl-5090-modelview-normal-canary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
