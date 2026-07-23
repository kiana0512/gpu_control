#!/usr/bin/env python3
"""Run a small, traceable multi-tenant smoke test against all three GPU nodes."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import mimetypes
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://10.3.34.11")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument(
        "--admin-password-file",
        type=Path,
        default=Path("output/deploy/INITIAL_ADMIN_PASSWORD.txt"),
    )
    parser.add_argument("--input", type=Path, default=Path("build/modelview-fresh.png"))
    parser.add_argument(
        "--ca", type=Path, default=Path("deploy/control-plane/nginx/certs/lan-ca.crt")
    )
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def checked(response: requests.Response, expected: set[int]) -> requests.Response:
    if response.status_code not in expected:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
    return response


def main() -> int:
    args = parse_args()
    if args.clients != 10:
        raise SystemExit("This acceptance test intentionally requires exactly 10 clients")
    password = args.admin_password_file.read_text(encoding="utf-8").strip()
    if not password:
        raise SystemExit("Admin password file is empty")
    if not args.input.is_file():
        raise SystemExit(f"Input image does not exist: {args.input}")
    verify: str | bool = str(args.ca) if args.ca.is_file() else True
    base_url = args.base_url.rstrip("/")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix=f"gpu-control-smoke10-{run_id}-"))
    if args.output_dir:
        output_dir.mkdir(parents=True, exist_ok=False)

    login = checked(
        requests.post(
            f"{base_url}/admin/auth/login",
            json={"username": args.admin_user, "password": password},
            timeout=20,
            verify=verify,
        ),
        {200},
    ).json()
    admin_headers = {"Authorization": f"Bearer {login['access_token']}"}

    def admin_request(method: str, path: str, **kwargs: Any) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(8):
            response = requests.request(
                method,
                f"{base_url}{path}",
                headers=admin_headers,
                timeout=20,
                verify=verify,
                **kwargs,
            )
            if response.status_code != 429:
                return response
            time.sleep(0.5 * (attempt + 1))
        assert response is not None
        return response

    for node_id in ("control-4090", "worker-3090-a", "worker-3090-b"):
        checked(
            admin_request(
                "PUT",
                f"/admin/nodes/{node_id}/mode",
                json={
                    "mode": "ACTIVE",
                    "reason": f"three-node ten-client smoke test {run_id}",
                    "confirm": True,
                },
            ),
            {200},
        )

    clients: list[dict[str, str]] = []
    for index in range(1, args.clients + 1):
        client_id = f"smoke10-{run_id.lower()}-{index:02d}"
        checked(
            admin_request(
                "POST",
                "/admin/clients",
                json={
                    "id": client_id,
                    "name": f"三节点轻量测试用户 {index:02d} ({run_id})",
                    "max_queued": 20,
                    "max_running": 1,
                    "daily_quota": 100,
                    "weight": 1,
                    "allowed_ips": [],
                    "callback_hosts": [],
                },
            ),
            {200},
        )
        key = checked(
            admin_request(
                "POST",
                f"/admin/clients/{client_id}/keys",
                json={"reason": f"three-node smoke test {run_id}", "confirm": True},
            ),
            {200},
        ).json()["api_key"]
        clients.append({"id": client_id, "key": key})

    image_bytes = args.input.read_bytes()
    mime = mimetypes.guess_type(args.input.name)[0] or "image/png"

    def submit(index: int, client: dict[str, str]) -> dict[str, Any]:
        # Alternating workflows makes the smoke test exercise both project stacks
        # and the scheduler's warm-cache preference/fallback behavior.
        workflow = "imageclip-rgba" if index % 2 == 0 else "modelview-inpaint"
        started = time.monotonic()
        response = requests.post(
            f"{base_url}/api/v1/services/{workflow}",
            headers={
                "X-API-Key": client["key"],
                "Idempotency-Key": f"smoke10-{run_id}-{index + 1:02d}-{uuid.uuid4().hex[:8]}",
            },
            files={"image": (args.input.name, image_bytes, mime)},
            timeout=args.timeout,
            verify=verify,
        )
        elapsed = round(time.monotonic() - started, 3)
        job_id = response.headers.get("X-Job-ID")
        content_type = response.headers.get("Content-Type", "")
        result: dict[str, Any] = {
            "client_id": client["id"],
            "workflow": workflow,
            "http_status": response.status_code,
            "elapsed_seconds": elapsed,
            "job_id": job_id,
            "content_type": content_type,
            "bytes": len(response.content),
        }
        if response.status_code == 200 and content_type.startswith("image/"):
            suffix = ".png" if "png" in content_type else ".bin"
            (output_dir / f"{index + 1:02d}-{workflow}-{job_id}{suffix}").write_bytes(
                response.content
            )
        else:
            result["error"] = response.text[:1000]
        return result

    started_all = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = [executor.submit(submit, i, client) for i, client in enumerate(clients)]
        results = [future.result() for future in futures]
    wall_seconds = round(time.monotonic() - started_all, 3)

    jobs = checked(
        requests.get(
            f"{base_url}/admin/jobs",
            headers=admin_headers,
            params={"limit": 100},
            timeout=20,
            verify=verify,
        ),
        {200},
    ).json()
    jobs_by_id = {job["job_id"]: job for job in jobs}
    for result in results:
        job = jobs_by_id.get(result["job_id"], {})
        result["status"] = job.get("status")
        result["node_id"] = job.get("node_id")
        result["progress"] = job.get("progress")
        result["error_code"] = (job.get("error") or {}).get("code")

    summary = {
        "run_id": run_id,
        "started_with_nodes": ["control-4090", "worker-3090-a", "worker-3090-b"],
        "client_count": len(clients),
        "wall_seconds": wall_seconds,
        "success_count": sum(
            result["http_status"] == 200 and result["status"] == "SUCCEEDED"
            for result in results
        ),
        "node_counts": {
            node_id: sum(result.get("node_id") == node_id for result in results)
            for node_id in ("control-4090", "worker-3090-a", "worker-3090-b")
        },
        "results": results,
        "output_dir": str(output_dir),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["success_count"] == len(clients) else 1


if __name__ == "__main__":
    raise SystemExit(main())
