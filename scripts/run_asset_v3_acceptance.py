#!/usr/bin/env python3
"""Run real UV/retopology HTTP acceptance against an isolated Asset API.

The script never fabricates completion payloads or writes the database.  It
submits caller-supplied real model files, waits for real Workers, downloads
every published artifact, and verifies response/body SHA-256 plus SSE history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


TERMINAL = {"SUCCEEDED", "WAITING_REVIEW", "FAILED", "CANCELLED", "REVIEW_REJECTED"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--uv-file", type=Path)
    parser.add_argument("--retopology-project", type=Path)
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--uv-count", type=int, default=2)
    parser.add_argument("--retopology-count", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--expected-workers", nargs="*", default=[])
    args = parser.parse_args()
    if args.uv_count < 0 or args.retopology_count < 0:
        parser.error("job counts cannot be negative")
    if args.submit and args.uv_count and not args.uv_file:
        parser.error("--uv-count requires --uv-file")
    if args.submit and args.retopology_count and not (
        args.retopology_project and args.reference_root
    ):
        parser.error(
            "--retopology-count requires --retopology-project and --reference-root"
        )
    if args.submit and args.uv_count + args.retopology_count == 0:
        parser.error("at least one real job is required")
    if not args.submit and not args.monitor:
        parser.error("choose --submit and/or --monitor")
    return args


def submit(client: httpx.Client, args: argparse.Namespace) -> dict[str, Any]:
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    jobs: list[dict[str, str]] = []
    uv_options = {
        "resolution": 2048,
        "padding_px": 10,
        "hard_edge_angle_degrees": 75,
        "hidden_axis": "y+",
        "texel_density_mode": "uniform",
        "qa_profile": "pbr-v1",
    }
    for index in range(1, args.uv_count + 1):
        external_id = f"acceptance:{run_id}:uv:{index:02d}"
        with args.uv_file.open("rb") as asset:
            response = client.post(
                "/api/v1/assets/uv/process",
                headers={"Idempotency-Key": external_id},
                files={
                    "asset": (
                        args.uv_file.name,
                        asset,
                        "application/octet-stream",
                    ),
                    "metadata": (
                        None,
                        json.dumps(
                            {"external_asset_id": external_id, "options": uv_options}
                        ),
                        "application/json",
                    ),
                },
            )
        response.raise_for_status()
        payload = response.json()
        jobs.append({"job_id": payload["job_id"], "job_type": "UV_PROCESS_V2"})
    reference_files = [
        ("front", args.reference_root / "reference_front.png"),
        ("side", args.reference_root / "reference_side.png"),
        ("top", args.reference_root / "reference_top.png"),
        ("perspective", args.reference_root / "reference_perspective.png"),
    ]
    for view, path in reference_files:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"real reference view is missing: {view}={path}")
    for index in range(1, args.retopology_count + 1):
        external_id = f"acceptance:{run_id}:retopology:{index:02d}"
        metadata = {
            "external_asset_id": external_id,
            "options": {
                "high_object": "bunny_high",
                "reference_object": "bunny_reference_low",
                "low_object": "bunny_current_low",
                "generated_low_object": f"bunny_generated_{index:02d}_v001",
                "algorithm": "agent",
                "target_faces": 3000,
                "preserve_sharp": True,
                "preserve_boundary": True,
                "render_resolution": 256,
                "max_repair_rounds": 0,
                "require_closed": False,
            },
            "reference_views": [
                {"filename": path.name, "view": view, "label": f"真实 Bunny {view}"}
                for view, path in reference_files
            ],
            "user_request": (
                "以高模轮廓为形状权威，以参考低模为布线和面数参考；"
                "保留耳朵、鼻口和腿部轮廓，生成版本化候选并提交四视图人工复核。"
            ),
        }
        handles = [path.open("rb") for _, path in reference_files]
        try:
            files: list[tuple[str, tuple[str | None, Any, str]]] = [
                (
                    "project",
                    (
                        args.retopology_project.name,
                        args.retopology_project.open("rb"),
                        "application/octet-stream",
                    ),
                ),
                ("metadata", (None, json.dumps(metadata, ensure_ascii=False), "application/json")),
            ]
            project_handle = files[0][1][1]
            files.extend(
                (
                    "reference_images",
                    (path.name, handle, "image/png"),
                )
                for (_, path), handle in zip(reference_files, handles, strict=True)
            )
            response = client.post(
                "/api/v1/assets/retopology/process",
                headers={"Idempotency-Key": external_id},
                files=files,
            )
        finally:
            project_handle.close()
            for handle in handles:
                handle.close()
        response.raise_for_status()
        payload = response.json()
        jobs.append(
            {"job_id": payload["job_id"], "job_type": "RETOPOLOGY_PROCESS_V1"}
        )
    state = {"schema_version": "asset-v3-live-acceptance.v1", "run_id": run_id, "jobs": jobs}
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def monitor(client: httpx.Client, args: argparse.Namespace, state: dict[str, Any]) -> None:
    deadline = time.monotonic() + args.timeout_seconds
    previous: dict[str, tuple[str, str, int]] = {}
    final: dict[str, dict[str, Any]] = {}
    while len(final) != len(state["jobs"]):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"asset acceptance exceeded {args.timeout_seconds}s")
        for descriptor in state["jobs"]:
            job_id = descriptor["job_id"]
            if job_id in final:
                continue
            response = client.get(f"/api/v1/assets/jobs/{job_id}")
            response.raise_for_status()
            payload = response.json()
            current = (payload["status"], payload["stage"], round(payload["progress"]))
            if previous.get(job_id) != current:
                print(
                    f"{job_id[:8]} {payload['job_type']} {current[0]} "
                    f"{current[1]} {current[2]}% worker={payload['worker_id']} "
                    f"eta={payload['timing']['estimated_remaining_seconds']}",
                    flush=True,
                )
                previous[job_id] = current
            if payload["status"] in TERMINAL:
                expected = (
                    "SUCCEEDED"
                    if descriptor["job_type"] == "UV_PROCESS_V2"
                    else "WAITING_REVIEW"
                )
                if payload["status"] != expected:
                    raise RuntimeError(
                        f"{job_id} ended as {payload['status']}: {payload.get('error')}"
                    )
                final[job_id] = payload
        time.sleep(3)

    workers = {str(payload["worker_id"]) for payload in final.values()}
    missing_workers = set(args.expected_workers) - workers
    if missing_workers:
        raise RuntimeError(f"expected Workers did not execute a job: {sorted(missing_workers)}")
    verification: dict[str, Any] = {"workers": sorted(workers), "jobs": {}}
    for descriptor in state["jobs"]:
        job_id = descriptor["job_id"]
        payload = final[job_id]
        expected_count = 5 if descriptor["job_type"] == "UV_PROCESS_V2" else 23
        if len(payload["artifacts"]) != expected_count:
            raise RuntimeError(
                f"{job_id} has {len(payload['artifacts'])}/{expected_count} artifacts"
            )
        artifacts = []
        for artifact in payload["artifacts"]:
            response = client.get(artifact["download_url"])
            response.raise_for_status()
            body_sha = sha256(response.content)
            header_sha = response.headers.get("X-Artifact-SHA256")
            if body_sha != artifact["sha256"] or header_sha != artifact["sha256"]:
                raise RuntimeError(f"artifact SHA mismatch: {job_id}/{artifact['kind']}")
            artifacts.append(
                {
                    "kind": artifact["kind"],
                    "filename": artifact["filename"],
                    "size_bytes": len(response.content),
                    "sha256": body_sha,
                }
            )
        event_response = client.get(f"/api/v1/assets/jobs/{job_id}/events")
        event_response.raise_for_status()
        event_ids = [
            int(line.removeprefix("id: "))
            for line in event_response.text.splitlines()
            if line.startswith("id: ")
        ]
        if not event_ids or event_ids != sorted(set(event_ids)):
            raise RuntimeError(f"invalid SSE event sequence for {job_id}: {event_ids}")
        verification["jobs"][job_id] = {
            "job_type": descriptor["job_type"],
            "status": payload["status"],
            "worker_id": payload["worker_id"],
            "attempt_count": payload["attempt_count"],
            "elapsed_seconds": payload["timing"]["elapsed_seconds"],
            "event_count": len(event_ids),
            "artifacts": artifacts,
        }
    result = args.state.with_suffix(".result.json")
    result.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False, indent=2))


def main() -> None:
    args = arguments()
    headers = {"X-API-Key": args.api_key}
    with httpx.Client(base_url=args.api_url.rstrip("/"), headers=headers, timeout=3600) as client:
        state = submit(client, args) if args.submit else json.loads(args.state.read_text("utf-8"))
        if args.monitor:
            monitor(client, args, state)


if __name__ == "__main__":
    main()
