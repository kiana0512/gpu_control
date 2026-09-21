#!/usr/bin/env python3
"""Submit bounded ModelView requests through the public API canary lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt
import requests
import urllib3
from PIL import Image

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CLIENT_ID = "autodl-5090-canary-20260918"


def env_value(path: Path, key: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        current, value = line.split("=", 1)
        if current == key:
            return value.strip().strip('"').strip("'")
    raise RuntimeError(f"missing {key}")


def checked(response: requests.Response, expected: set[int]) -> requests.Response:
    if response.status_code not in expected:
        detail = response.text[:1000]
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    return response


def admin_headers(secret: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",
            "type": "access",
            "iat": now,
            "exp": now + 900,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def ensure_test_client(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
) -> str:
    clients = checked(
        session.get(base_url + "/admin/clients", headers=headers, timeout=20, verify=False),
        {200},
    ).json()
    existing = next((item for item in clients if item.get("id") == CLIENT_ID), None)
    payload: dict[str, Any] = {
        "name": "AutoDL cloud scheduled canary",
        "client_kind": "test",
        "max_queued": 4,
        "max_running": 1,
        "daily_quota": 0,
        "weight": 1,
        "allowed_ips": [],
        "callback_hosts": [],
    }
    if existing is None:
        checked(
            session.post(
                base_url + "/admin/clients",
                headers=headers,
                json={"id": CLIENT_ID, **payload},
                timeout=20,
                verify=False,
            ),
            {200},
        )
    else:
        checked(
            session.put(
                base_url + f"/admin/clients/{CLIENT_ID}",
                headers=headers,
                json={
                    **payload,
                    "enabled": True,
                    "reason": "运行 AutoDL 云节点隔离调度验收",
                    "confirm": True,
                },
                timeout=20,
                verify=False,
            ),
            {200},
        )
    key_result = checked(
        session.post(
            base_url + f"/admin/clients/{CLIENT_ID}/keys",
            headers=headers,
            json={"reason": "AutoDL 云节点调度验收临时密钥", "confirm": True},
            timeout=20,
            verify=False,
        ),
        {200},
    ).json()
    return str(key_result["api_key"])


def disable_test_client(
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
) -> None:
    """Leave the temporary canary principal fenced after every run."""

    checked(
        session.put(
            base_url + f"/admin/clients/{CLIENT_ID}",
            headers=headers,
            json={
                "name": "AutoDL cloud scheduled canary",
                "client_kind": "test",
                "max_queued": 4,
                "max_running": 1,
                "daily_quota": 0,
                "weight": 1,
                "allowed_ips": [],
                "callback_hosts": [],
                "enabled": False,
                "reason": "AutoDL 云节点调度验收结束，重新禁用临时客户端",
                "confirm": True,
            },
            timeout=20,
            verify=False,
        ),
        {200},
    )


def submit(
    session: requests.Session,
    base_url: str,
    api_key: str,
    inputs: Path,
    destination: Path,
    label: str,
) -> dict[str, Any]:
    handles = [
        ("image", ("image.png", (inputs / "image-canary.png").open("rb"), "image/png")),
        (
            "material_image",
            (
                "material.png",
                (inputs / "material_image-canary.png").open("rb"),
                "image/png",
            ),
        ),
        ("mask", ("mask.png", (inputs / "mask-canary.png").open("rb"), "image/png")),
        (
            "normal_image",
            ("normal.png", (inputs / "normal_image-canary.png").open("rb"), "image/png"),
        ),
    ]
    started_wall = datetime.now(timezone.utc)  # noqa: UP017 - host Python is 3.10
    started = time.perf_counter()
    try:
        response = session.post(
            base_url + "/api/v1/services/modelview-inpaint",
            headers={
                "X-API-Key": api_key,
                "Idempotency-Key": f"autodl-5090-{label}-{uuid.uuid4().hex}",
            },
            files=handles,
            data={"prompt": "restore the selected area using the material and normal references"},
            timeout=2700,
            verify=False,
        )
    finally:
        for _, (_, handle, _) in handles:
            handle.close()
    elapsed = time.perf_counter() - started
    checked(response, {200})
    destination.write_bytes(response.content)
    digest = hashlib.sha256(response.content).hexdigest()
    with Image.open(destination) as image:
        size = list(image.size)
        image_format = image.format
    return {
        "label": label,
        "request_started_at": started_wall.isoformat(),
        "request_total_seconds": round(elapsed, 3),
        "status_code": response.status_code,
        "job_id": response.headers.get("X-Job-ID"),
        "artifact_sha256_header": response.headers.get("X-Artifact-SHA256"),
        "artifact_sha256": digest,
        "artifact_bytes": len(response.content),
        "image_size": size,
        "image_format": image_format,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--inputs",
        type=Path,
        default=Path("/tmp/modelview-normal-2step-20260918/input"),  # noqa: S108
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_url = env_value(args.env_file, "PUBLIC_BASE_URL").rstrip("/")
    secret = env_value(args.env_file, "JWT_SECRET")
    session = requests.Session()
    headers = admin_headers(secret)
    api_key = ensure_test_client(session, base_url, headers)
    results: list[dict[str, Any]] = []
    try:
        for index in range(args.runs):
            label = f"run-{index + 1}"
            results.append(
                submit(
                    session,
                    base_url,
                    api_key,
                    args.inputs,
                    args.output_dir / f"scheduled-{label}.png",
                    label,
                )
            )
    finally:
        disable_test_client(session, base_url, headers)
    report = {
        "status": "PASSED",
        "client_id": CLIENT_ID,
        "workflow": "modelview-inpaint",
        "expected_node_id": "autodl-5090-01",
        "results": results,
    }
    report_path = args.output_dir / "scheduled-canary-client-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
