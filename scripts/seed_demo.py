#!/usr/bin/env python3
"""Create a local-only dataset for UI review without a GPU or Redis server."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    AuditLog,
    Base,
    Job,
    Node,
    SystemSetting,
    Workflow,
    WorkflowVersion,
)
from packages.gpu_control_core.security import hash_password


async def seed(database_url: str, password: str) -> None:
    if not database_url.startswith("sqlite+aiosqlite:///"):
        raise SystemExit("演示数据脚本只允许写入显式指定的 SQLite 数据库")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    async with factory() as session:
        if await session.scalar(select(ApiClient.id).where(ApiClient.id == "admin")):
            print("演示数据已经存在，未重复写入")
            await engine.dispose()
            return

        session.add_all(
            [
                ApiClient(
                    id="admin",
                    name="administrator",
                    role="admin",
                    password_hash=hash_password(password),
                    max_queued=200,
                    max_running=3,
                ),
                ApiClient(
                    id="studio-a",
                    name="美术工作室 A",
                    role="client",
                    max_queued=40,
                    max_running=2,
                    daily_quota=1200,
                    weight=2,
                ),
                ApiClient(
                    id="studio-b",
                    name="外包团队 B",
                    role="client",
                    max_queued=20,
                    max_running=1,
                    daily_quota=500,
                    weight=1,
                ),
            ]
        )
        session.add_all(
            [
                Node(
                    id="gpu-3090-a",
                    display_name="3090 主节点 A",
                    base_url="http://10.20.0.11:8188",
                    agent_url="http://10.20.0.11:9109",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    current_jobs=1,
                    gpu_util_percent=87,
                    free_vram_mb=6840,
                    total_vram_mb=24576,
                    last_heartbeat_at=now,
                ),
                Node(
                    id="gpu-3090-b",
                    display_name="3090 主节点 B",
                    base_url="http://10.20.0.12:8188",
                    agent_url="http://10.20.0.12:9109",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    current_jobs=1,
                    gpu_util_percent=73,
                    free_vram_mb=9216,
                    total_vram_mb=24576,
                    last_heartbeat_at=now,
                ),
                Node(
                    id="gpu-4090-control",
                    display_name="4090 控制节点",
                    base_url="http://10.20.0.10:8188",
                    agent_url="http://10.20.0.10:9109",
                    pool="OVERFLOW",
                    mode="RESERVED",
                    health="ONLINE",
                    manual_reserved=True,
                    gpu_util_percent=3,
                    free_vram_mb=22120,
                    total_vram_mb=24576,
                    last_heartbeat_at=now,
                ),
            ]
        )
        session.add(
            Workflow(
                key="inpaint-demo", display_name="局部重绘演示", description="仅用于无 GPU UI 预览"
            )
        )
        session.add(
            WorkflowVersion(
                workflow_key="inpaint-demo",
                version="2026.07-demo",
                template={"1": {"class_type": "KSampler", "inputs": {}}},
                parameter_schema={"type": "object", "additionalProperties": False},
                bindings={},
                allowed_class_types=["KSampler"],
                required_models=["demo-checkpoint.safetensors"],
                required_custom_nodes=[],
                min_vram_mb=12288,
                timeout_seconds=900,
                output_nodes=["1"],
                enabled=True,
                template_sha256="demo-only-not-a-production-workflow",
            )
        )

        statuses = [
            "RUNNING",
            "RUNNING",
            "QUEUED",
            "QUEUED",
            "QUEUED",
            "QUEUED",
            "QUEUED",
            "SUCCEEDED",
            "SUCCEEDED",
            "SUCCEEDED",
            "FAILED",
            "TIMED_OUT",
        ]
        for index, status in enumerate(statuses, start=1):
            node_id = None
            progress = 0.0
            started_at = None
            finished_at = None
            error_code = None
            error_message = None
            if status == "RUNNING":
                node_id = "gpu-3090-a" if index == 1 else "gpu-3090-b"
                progress = 68 if index == 1 else 34
                started_at = now - timedelta(minutes=index * 2)
            elif status == "SUCCEEDED":
                node_id = "gpu-3090-a"
                progress = 100
                started_at = now - timedelta(minutes=index * 4)
                finished_at = started_at + timedelta(minutes=3)
            elif status in {"FAILED", "TIMED_OUT"}:
                node_id = "gpu-3090-b"
                started_at = now - timedelta(minutes=index * 4)
                finished_at = started_at + timedelta(minutes=15)
                error_code = (
                    "COMFY_TIMEOUT" if status == "TIMED_OUT" else "OUTPUT_VALIDATION_FAILED"
                )
                error_message = "演示错误：可从管理台安全重试"
            session.add(
                Job(
                    id=f"demo-job-{index:04d}",
                    tenant_id="studio-a" if index % 2 else "studio-b",
                    workflow_key="inpaint-demo",
                    workflow_version="2026.07-demo",
                    status=status,
                    priority="HIGH" if index in {1, 3} else "NORMAL",
                    parameters={},
                    request_hash=f"{index:064x}",
                    request_id=f"demo-request-{index:04d}",
                    trace_id=f"demo-trace-{index:04d}",
                    job_dir=f"storage/jobs/demo-job-{index:04d}",
                    node_id=node_id,
                    prompt_id=f"prompt-demo-{index:04d}" if node_id else None,
                    progress=progress,
                    attempt_count=1 if node_id else 0,
                    started_at=started_at,
                    finished_at=finished_at,
                    error_code=error_code,
                    error_message=error_message,
                    created_at=now - timedelta(minutes=index * 3),
                    updated_at=now - timedelta(minutes=index),
                )
            )

        session.add_all(
            [
                SystemSetting(
                    key="overflow_queue_threshold",
                    value={"value": 20},
                    updated_by="admin",
                ),
                SystemSetting(
                    key="overflow_wait_threshold_seconds",
                    value={"value": 120},
                    updated_by="admin",
                ),
                Alert(
                    id="demo-alert-resolved",
                    fingerprint="demo-node-heartbeat",
                    status="resolved",
                    severity="warning",
                    labels={"node_id": "gpu-3090-b", "alertname": "NodeHeartbeatLate"},
                    annotations={"summary": "演示告警已恢复"},
                    starts_at=now - timedelta(hours=2),
                    ends_at=now - timedelta(hours=1, minutes=40),
                ),
                AuditLog(
                    actor_id="admin",
                    action="node.mode.change",
                    target_type="node",
                    target_id="gpu-4090-control",
                    before={"mode": "OVERFLOW"},
                    after={"mode": "RESERVED", "reason": "控制面预留"},
                    source_ip="127.0.0.1",
                    request_id="demo-audit-request",
                    result="success",
                    created_at=now - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()
    await engine.dispose()
    print("演示数据写入完成；它不包含真实模型、密钥或生产工作流")


def main() -> None:
    parser = argparse.ArgumentParser(description="为本地管理台创建无 GPU 演示数据")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--password", required=True, help="本地演示管理员密码（至少 12 位）")
    args = parser.parse_args()
    if len(args.password) < 12:
        raise SystemExit("演示管理员密码至少 12 位")
    asyncio.run(seed(args.database_url, args.password))


if __name__ == "__main__":
    main()
