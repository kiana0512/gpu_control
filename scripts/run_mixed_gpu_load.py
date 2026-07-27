#!/usr/bin/env python3
"""Run randomized real ImageClip/ModelView jobs and produce a capacity report."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
from PIL import Image

ACTIVE = {
    "RECEIVED",
    "VALIDATING",
    "QUEUED",
    "CLAIMED",
    "UPLOADING",
    "SUBMITTED",
    "RUNNING",
    "DOWNLOADING",
    "CANCELLING",
    "RETRY_WAIT",
}


@dataclass(frozen=True)
class WorkItem:
    ordinal: int
    workflow_key: str
    workflow_version: str
    client_id: str
    api_key: str


@dataclass
class Submission:
    ordinal: int
    workflow_key: str
    client_id: str
    job_id: str | None
    status_code: int
    latency_seconds: float
    queue_position: int | None
    retries: int
    error: str | None


class Pacer:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1 / requests_per_second
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            scheduled = max(now, self.next_at)
            self.next_at = scheduled + self.interval
        delay = scheduled - now
        if delay > 0:
            await asyncio.sleep(delay)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://10.3.34.11")
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--total", type=int, default=60)
    parser.add_argument("--imageclip-ratio", type=float, default=0.7)
    parser.add_argument("--submit-concurrency", type=int, default=12)
    parser.add_argument("--target-submit-rps", type=float, default=8)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=21_600)
    parser.add_argument("--seed", type=int, default=20260727)
    return parser.parse_args()


def database_url() -> str:
    value = os.environ["DATABASE_URL"]
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1)
    return round(ordered[position], 3)


async def discover_workflows(
    client: httpx.AsyncClient, api_key: str
) -> dict[str, str]:
    response = await client.get(
        "/api/v1/workflows", headers={"X-API-Key": api_key}
    )
    response.raise_for_status()
    versions = {
        str(row["workflow_key"]): str(row["version"]) for row in response.json()
    }
    required = {"imageclip-rgba", "modelview-inpaint"}
    if not required <= versions.keys():
        raise RuntimeError(f"required workflows unavailable: {required - versions.keys()}")
    return versions


def build_items(
    args: argparse.Namespace,
    clients: list[dict[str, str]],
    versions: dict[str, str],
) -> list[WorkItem]:
    imageclip_count = round(args.total * args.imageclip_ratio)
    workflows = ["imageclip-rgba"] * imageclip_count + [
        "modelview-inpaint"
    ] * (args.total - imageclip_count)
    generator = random.Random(args.seed)  # noqa: S311 - reproducible load order
    generator.shuffle(workflows)
    return [
        WorkItem(
            ordinal=index,
            workflow_key=workflow,
            workflow_version=versions[workflow],
            client_id=str(clients[index % len(clients)]["client_id"]),
            api_key=str(clients[index % len(clients)]["api_key"]),
        )
        for index, workflow in enumerate(workflows)
    ]


async def submit_one(
    client: httpx.AsyncClient,
    pacer: Pacer,
    item: WorkItem,
    fixture: bytes,
    run_id: str,
) -> Submission:
    key = f"load:{run_id}:{item.ordinal:06d}"
    request_id = f"lt-{run_id}-{item.ordinal:06d}"[:64]
    retries = 0
    started = time.monotonic()
    while True:
        await pacer.wait()
        try:
            response = await client.post(
                "/api/v1/jobs",
                headers={
                    "X-API-Key": item.api_key,
                    "Idempotency-Key": key,
                    "X-Request-ID": request_id,
                },
                files={
                    "workflow_key": (None, item.workflow_key),
                    "workflow_version": (None, item.workflow_version),
                    "parameters": (None, "{}"),
                    "input_image": ("load-fixture.png", fixture, "image/png"),
                },
            )
        except httpx.HTTPError as exc:
            if retries >= 8:
                return Submission(
                    item.ordinal,
                    item.workflow_key,
                    item.client_id,
                    None,
                    0,
                    time.monotonic() - started,
                    None,
                    retries,
                    f"{type(exc).__name__}: {exc}",
                )
            retries += 1
            await asyncio.sleep(min(10, 0.5 * 2**retries))
            continue
        if response.status_code == 429 and retries < 12:
            retries += 1
            await asyncio.sleep(min(10, 0.25 * 2 ** min(retries, 5)))
            continue
        payload: dict[str, Any]
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return Submission(
            item.ordinal,
            item.workflow_key,
            item.client_id,
            str(payload["job_id"]) if payload.get("job_id") else None,
            response.status_code,
            time.monotonic() - started,
            int(payload["queue_position"])
            if payload.get("queue_position") is not None
            else None,
            retries,
            None if response.status_code in {200, 202} else response.text[:500],
        )


async def submit_all(
    args: argparse.Namespace,
    client: httpx.AsyncClient,
    items: list[WorkItem],
    fixture: bytes,
) -> list[Submission]:
    queue: asyncio.Queue[WorkItem] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)
    pacer = Pacer(args.target_submit_rps)
    submissions: list[Submission] = []

    async def worker() -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            result = await submit_one(client, pacer, item, fixture, args.run_id)
            submissions.append(result)
            queue.task_done()

    await asyncio.gather(
        *(worker() for _ in range(min(args.submit_concurrency, len(items))))
    )
    submissions.sort(key=lambda item: item.ordinal)
    return submissions


async def status_snapshot(
    connection: asyncpg.Connection, job_ids: list[str]
) -> dict[str, Any]:
    status_rows = await connection.fetch(
        """
        SELECT status, count(*) AS count
        FROM jobs WHERE id = ANY($1::varchar[]) GROUP BY status ORDER BY status
        """,
        job_ids,
    )
    node_rows = await connection.fetch(
        """
        SELECT id, health, mode, current_jobs, gpu_util_percent,
               free_vram_mb, total_vram_mb
        FROM nodes ORDER BY id
        """
    )
    production = await connection.fetchrow(
        """
        SELECT count(*) FILTER (WHERE j.status = 'QUEUED') AS queued,
               count(*) FILTER (WHERE j.status = ANY($1::varchar[])) AS active,
               COALESCE(EXTRACT(EPOCH FROM (
                 now() - min(j.created_at) FILTER (WHERE j.status = 'QUEUED')
               )), 0) AS oldest_wait
        FROM jobs j JOIN api_clients c ON c.id = j.tenant_id
        WHERE c.client_kind = 'production'
        """,
        list(ACTIVE),
    )
    return {
        "statuses": {str(row["status"]): int(row["count"]) for row in status_rows},
        "nodes": [dict(row) for row in node_rows],
        "production": dict(production) if production else {},
    }


async def wait_for_completion(
    args: argparse.Namespace,
    connection: asyncpg.Connection,
    job_ids: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    deadline = time.monotonic() + args.timeout_seconds
    samples: list[dict[str, Any]] = []
    completed = False
    while time.monotonic() < deadline:
        snapshot = await status_snapshot(connection, job_ids)
        snapshot["captured_at"] = datetime.now(UTC).isoformat()
        samples.append(snapshot)
        statuses = snapshot["statuses"]
        observed = sum(statuses.values())
        active = sum(statuses.get(status, 0) for status in ACTIVE)
        nodes = ", ".join(
            f"{node['id']}:{node['current_jobs']}/{node['gpu_util_percent']:.0f}%"
            for node in snapshot["nodes"]
        )
        production = snapshot["production"]
        print(
            f"observed={observed}/{len(job_ids)} active={active} statuses={statuses} "
            f"nodes=[{nodes}] production_queued={production.get('queued', 0)}"
        )
        if observed == len(job_ids) and active == 0:
            completed = True
            break
        await asyncio.sleep(args.poll_seconds)
    return samples, completed


async def database_report(
    connection: asyncpg.Connection, job_ids: list[str]
) -> dict[str, Any]:
    rows = await connection.fetch(
        """
        SELECT id, workflow_key, status, node_id, attempt_count, error_code,
               EXTRACT(EPOCH FROM (started_at - created_at)) AS queue_seconds,
               EXTRACT(EPOCH FROM (finished_at - started_at)) AS execute_seconds,
               EXTRACT(EPOCH FROM (finished_at - created_at)) AS total_seconds,
               job_dir
        FROM jobs WHERE id = ANY($1::varchar[]) ORDER BY created_at
        """,
        job_ids,
    )
    switch_rows = await connection.fetch(
        """
        WITH ordered AS (
          SELECT node_id, workflow_key, started_at, finished_at,
                 lag(workflow_key) OVER (PARTITION BY node_id ORDER BY started_at) AS previous
          FROM jobs
          WHERE id = ANY($1::varchar[]) AND status = 'SUCCEEDED' AND node_id IS NOT NULL
        )
        SELECT node_id, (previous IS NOT NULL AND previous <> workflow_key) AS switched,
               count(*) AS jobs,
               avg(EXTRACT(EPOCH FROM (finished_at - started_at))) AS avg_seconds
        FROM ordered GROUP BY node_id, switched ORDER BY node_id, switched
        """,
        job_ids,
    )
    def durations(field: str) -> list[float]:
        return [float(row[field]) for row in rows if row[field] is not None]

    grouped: dict[str, dict[str, Any]] = {}
    for workflow in ("imageclip-rgba", "modelview-inpaint"):
        selected = [row for row in rows if row["workflow_key"] == workflow]
        values = [float(row["execute_seconds"]) for row in selected if row["execute_seconds"]]
        grouped[workflow] = {
            "count": len(selected),
            "statuses": dict(Counter(str(row["status"]) for row in selected)),
            "execute_p50": percentile(values, 0.5),
            "execute_p95": percentile(values, 0.95),
            "execute_max": round(max(values), 3) if values else None,
        }
    return {
        "observed": len(rows),
        "statuses": dict(Counter(str(row["status"]) for row in rows)),
        "nodes": dict(Counter(str(row["node_id"]) for row in rows if row["node_id"])),
        "attempts": dict(Counter(int(row["attempt_count"]) for row in rows)),
        "errors": dict(Counter(str(row["error_code"]) for row in rows if row["error_code"])),
        "queue_p50": percentile(durations("queue_seconds"), 0.5),
        "queue_p95": percentile(durations("queue_seconds"), 0.95),
        "queue_max": round(max(durations("queue_seconds")), 3)
        if durations("queue_seconds")
        else None,
        "total_p50": percentile(durations("total_seconds"), 0.5),
        "total_p95": percentile(durations("total_seconds"), 0.95),
        "total_max": round(max(durations("total_seconds")), 3)
        if durations("total_seconds")
        else None,
        "workflows": grouped,
        "switch_cost": [
            {
                "node_id": str(row["node_id"]),
                "switched": bool(row["switched"]),
                "jobs": int(row["jobs"]),
                "avg_seconds": round(float(row["avg_seconds"]), 3),
            }
            for row in switch_rows
        ],
        "jobs": [dict(row) for row in rows],
    }


async def validate_outputs(
    connection: asyncpg.Connection, job_ids: list[str]
) -> dict[str, Any]:
    rows = await connection.fetch(
        """
        SELECT j.id, j.workflow_key, j.job_dir, a.relative_path, a.sha256, a.size_bytes
        FROM jobs j JOIN job_artifacts a ON a.job_id = j.id AND a.kind = 'output'
        WHERE j.id = ANY($1::varchar[]) AND j.status = 'SUCCEEDED'
        ORDER BY j.id
        """,
        job_ids,
    )
    failures: list[dict[str, str]] = []
    for row in rows:
        path = (Path(str(row["job_dir"])) / str(row["relative_path"])).resolve()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row["sha256"] or path.stat().st_size != row["size_bytes"]:
                raise ValueError("artifact size or SHA mismatch")
            with Image.open(path) as image:
                image.load()
                if row["workflow_key"] == "imageclip-rgba" and "A" not in image.getbands():
                    raise ValueError("ImageClip output has no alpha channel")
        except Exception as exc:
            failures.append({"job_id": str(row["id"]), "error": str(exc)})
    return {"validated": len(rows), "failures": failures}


async def main() -> None:
    args = arguments()
    if args.total < 1 or not 0 <= args.imageclip_ratio <= 1:
        raise ValueError("invalid total or imageclip ratio")
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    clients = list(credentials["clients"])
    if not clients:
        raise ValueError("credential file contains no clients")
    fixture = args.fixture.read_bytes()
    limits = httpx.Limits(
        max_connections=max(20, args.submit_concurrency * 2),
        max_keepalive_connections=max(10, args.submit_concurrency),
    )
    timeout = httpx.Timeout(120, connect=15)
    started_at = datetime.now(UTC)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        verify=str(args.ca),
        limits=limits,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        versions = await discover_workflows(client, str(clients[0]["api_key"]))
        items = build_items(args, clients, versions)
        submissions = await submit_all(args, client, items, fixture)
    accepted = [item for item in submissions if item.status_code in {200, 202} and item.job_id]
    accepted_job_ids = [str(item.job_id) for item in accepted if item.job_id]
    connection = await asyncpg.connect(database_url())
    try:
        samples, completed = await wait_for_completion(args, connection, accepted_job_ids)
        db_report = await database_report(connection, accepted_job_ids)
        validation = await validate_outputs(connection, accepted_job_ids)
    finally:
        await connection.close()
    submit_latencies = [item.latency_seconds for item in submissions]
    queue_positions = [item.queue_position for item in submissions if item.queue_position]
    report = {
        "run_id": args.run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "total": args.total,
            "imageclip_ratio": args.imageclip_ratio,
            "clients": len(clients),
            "submit_concurrency": args.submit_concurrency,
            "target_submit_rps": args.target_submit_rps,
            "seed": args.seed,
        },
        "submission": {
            "accepted": len(accepted),
            "failed": len(submissions) - len(accepted),
            "http_statuses": dict(Counter(item.status_code for item in submissions)),
            "throttled_retries": sum(item.retries for item in submissions),
            "latency_p50": percentile(submit_latencies, 0.5),
            "latency_p95": percentile(submit_latencies, 0.95),
            "max_queue_position": max(queue_positions) if queue_positions else None,
            "items": [asdict(item) for item in submissions],
        },
        "completed_before_timeout": completed,
        "database": db_report,
        "output_validation": validation,
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "accepted": len(accepted),
                "completed": completed,
                "statuses": db_report["statuses"],
                "nodes": db_report["nodes"],
                "validation_failures": len(validation["failures"]),
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    all_succeeded = db_report["statuses"] == {"SUCCEEDED": args.total}
    if (
        len(accepted) != args.total
        or not completed
        or not all_succeeded
        or validation["validated"] != args.total
        or validation["failures"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
