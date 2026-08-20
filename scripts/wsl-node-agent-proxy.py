#!/usr/bin/env python3
"""Expose signed WSL GPU telemetry and proxy the remaining Node Agent API."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_HOST = os.getenv("WSL_NODE_PROXY_HOST", "0.0.0.0")  # noqa: S104 - LAN agent endpoint
LISTEN_PORT = int(os.getenv("WSL_NODE_PROXY_PORT", "9201"))
BACKEND = os.getenv("WSL_NODE_AGENT_BACKEND", "http://127.0.0.1:9202")
SECRET = os.environ["NODE_AGENT_HMAC_SECRET"]
NVIDIA_SMI = "/usr/lib/wsl/lib/nvidia-smi"
_nonces: dict[str, int] = {}
_nonce_lock = threading.Lock()


def _signature(method: str, path: str, body: bytes, stamp: str, nonce: str) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((method.upper(), path, stamp, nonce, digest))
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def _authorized(handler: BaseHTTPRequestHandler, body: bytes) -> bool:
    stamp = handler.headers.get("X-GPU-Timestamp", "")
    nonce = handler.headers.get("X-GPU-Nonce", "")
    supplied = handler.headers.get("X-GPU-Signature", "")
    try:
        then = int(stamp)
    except ValueError:
        return False
    now = int(time.time())
    if abs(now - then) > 30 or not nonce or len(nonce) > 128:
        return False
    expected = _signature(handler.command, handler.path.split("?", 1)[0], body, stamp, nonce)
    if not hmac.compare_digest(supplied, expected):
        return False
    with _nonce_lock:
        for key, seen in list(_nonces.items()):
            if now - seen > 60:
                del _nonces[key]
        if nonce in _nonces:
            return False
        _nonces[nonce] = now
    return True


def _optional_metric(value: str) -> float | None:
    try:
        return round(float(value.strip()), 1)
    except ValueError:
        return None


def _gpu_metrics() -> dict[str, int | float | None]:
    result = subprocess.run(  # noqa: S603 - argv[0] is a fixed trusted absolute path
        [
            NVIDIA_SMI,
            "--query-gpu=utilization.gpu,memory.free,memory.total,temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=4,
    )
    fields = [value.strip() for value in result.stdout.splitlines()[0].split(",")]
    if len(fields) != 6:
        raise RuntimeError("invalid nvidia-smi metrics response")
    return {
        "gpu_util_percent": max(0, min(100, int(float(fields[0])))),
        "free_vram_mb": max(0, int(float(fields[1]))),
        "total_vram_mb": max(0, int(float(fields[2]))),
        "gpu_temperature_c": _optional_metric(fields[3]),
        "gpu_power_w": _optional_metric(fields[4]),
        "gpu_power_limit_w": _optional_metric(fields[5]),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 16_384:
            self._send(413, b'{"detail":"request body too large"}')
            return
        body = self.rfile.read(length) if length else b""
        if self.path.split("?", 1)[0] == "/v1/gpu-metrics":
            if not _authorized(self, body):
                self._send(401, b'{"detail":"invalid signed request"}')
                return
            try:
                payload = json.dumps(_gpu_metrics(), separators=(",", ":")).encode()
            except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
                payload = json.dumps({"detail": type(exc).__name__}).encode()
                self._send(503, payload)
                return
            self._send(200, payload)
            return
        request = urllib.request.Request(  # noqa: S310 - fixed loopback backend
            BACKEND + self.path,
            data=body if body else None,
            method=self.command,
            headers={key: value for key, value in self.headers.items() if key.lower() != "host"},
        )
        try:
            with urllib.request.urlopen(request, timeout=65) as response:  # noqa: S310
                self._send(
                    response.status,
                    response.read(),
                    response.headers.get_content_type(),
                )
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get_content_type())
        except (OSError, urllib.error.URLError):
            self._send(502, b'{"detail":"node agent backend unavailable"}')

    do_GET = _handle
    do_POST = _handle

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
