from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.gpu_control_core.admission import (
    active_production_work_exists,
    client_is_load_test,
)
from packages.gpu_control_core.models import ApiClient, AssetJob, Base, Job, JobBatch


async def test_cross_plane_production_detection_is_fail_closed(tmp_path: Path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'admission.db').as_posix()}"
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            db.add_all(
                [
                    ApiClient(id="production", name="Production", role="client"),
                    ApiClient(
                        id="load-test",
                        name="Load Test",
                        role="client",
                        client_kind="test",
                    ),
                ]
            )
            db.add(
                Job(
                    id="test-gpu-job",
                    tenant_id="load-test",
                    workflow_key="test",
                    workflow_version="1",
                    status="QUEUED",
                    parameters={},
                    request_hash="a" * 64,
                    request_id="test-request",
                    trace_id="test-trace",
                    job_dir=str(tmp_path / "test-gpu-job"),
                )
            )
            await db.commit()
            assert await client_is_load_test(db, "load-test") is True
            assert await client_is_load_test(db, "production") is False
            assert await client_is_load_test(db, "missing-client") is False
            assert await active_production_work_exists(db) is False

            asset = AssetJob(
                id="production-asset",
                client_id="production",
                external_asset_id="production-asset",
                job_type="UV_PROCESS_V2",
                status="FUTURE_ACTIVE_STATE",
                source_filename="asset.blend",
                input_path=str(tmp_path / "asset.blend"),
                input_sha256="b" * 64,
                input_size_bytes=1,
                options={},
                request_hash="c" * 64,
                request_id="production-asset-request",
            )
            db.add(asset)
            await db.commit()
            assert await active_production_work_exists(db) is True

            asset.status = "SUCCEEDED"
            batch = JobBatch(
                id="production-batch",
                tenant_id="production",
                external_batch_id="production-batch",
                workflow_key="imageclip-rgba",
                workflow_version="1",
                status="FUTURE_ACTIVE_STATE",
                parameters={},
                request_hash="d" * 64,
                request_id="production-batch-request",
                trace_id="production-batch-trace",
                batch_dir=str(tmp_path / "production-batch"),
                manifest_sha256="e" * 64,
                archive_sha256="f" * 64,
                archive_size_bytes=1,
                total_items=1,
            )
            db.add(batch)
            await db.commit()
            assert await active_production_work_exists(db) is True

            batch.status = "SUCCEEDED"
            db.add(
                Job(
                    id="orphan-production-job",
                    tenant_id="missing-client",
                    workflow_key="unknown",
                    workflow_version="1",
                    status="FUTURE_ACTIVE_STATE",
                    parameters={},
                    request_hash="1" * 64,
                    request_id="orphan-request",
                    trace_id="orphan-trace",
                    job_dir=str(tmp_path / "orphan-production-job"),
                )
            )
            await db.commit()
            assert await active_production_work_exists(db) is True
    finally:
        await engine.dispose()
