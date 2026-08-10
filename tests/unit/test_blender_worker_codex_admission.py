from __future__ import annotations

import asyncio
from typing import Any

from gpu_control_blender_worker.main import (
    job_requires_codex,
    worker_accepts_codex_jobs,
    worker_can_claim_another_job,
)


async def _idle() -> None:
    await asyncio.sleep(0)


def _task() -> asyncio.Task[None]:
    return asyncio.create_task(_idle())


def test_only_one_codex_backed_job_can_be_admitted_per_worker() -> None:
    async def scenario() -> None:
        first = _task()
        running: dict[asyncio.Task[None], dict[str, Any]] = {
            first: {"job_type": "RETOPOLOGY_PROCESS_V2"}
        }
        try:
            assert job_requires_codex(running[first])
            assert worker_can_claim_another_job(running, max_concurrency=4)
            assert not worker_accepts_codex_jobs(running)
        finally:
            await first

    asyncio.run(scenario())


def test_blender_only_jobs_keep_using_configured_parallel_capacity() -> None:
    async def scenario() -> None:
        first = _task()
        second = _task()
        running: dict[asyncio.Task[None], dict[str, Any]] = {
            first: {"job_type": "UV_PROCESS_V2"},
            second: {"job_type": "RETOPOLOGY_AUDIT"},
        }
        try:
            assert not job_requires_codex(running[first])
            assert worker_can_claim_another_job(running, max_concurrency=3)
            assert worker_accepts_codex_jobs(running)
            assert not worker_can_claim_another_job(running, max_concurrency=2)
        finally:
            await asyncio.gather(first, second)

    asyncio.run(scenario())


def test_claim_loop_stops_immediately_after_codex_job_is_added() -> None:
    async def scenario() -> None:
        blender = _task()
        codex = _task()
        running: dict[asyncio.Task[None], dict[str, Any]] = {
            blender: {"job_type": "UV_PROCESS_V2"}
        }
        try:
            assert worker_can_claim_another_job(running, max_concurrency=3)
            running[codex] = {"job_type": "RETOPOLOGY_PROCESS_V1"}
            assert worker_can_claim_another_job(running, max_concurrency=3)
            assert not worker_accepts_codex_jobs(running)
        finally:
            await asyncio.gather(blender, codex)

    asyncio.run(scenario())
