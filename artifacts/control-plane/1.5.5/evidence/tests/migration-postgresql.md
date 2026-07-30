# PostgreSQL 17 candidate migration evidence

```yaml
schema_version: gpu-control-migration-evidence.v1
result: PASS
started_at_utc: 2026-07-30T09:02:49Z
finished_at_utc: 2026-07-30T09:03:21Z
source_revision: PENDING_SOURCE_COMMIT
validated_base_head: 63deec8f57dede18ee64703ccc2b2726032e2f07
migration: migrations/versions/20260730_0011_assetclaw_v4_alignment.py
migration_sha256: d1c5d78ff1f3b57bfcbd227c8bd8648f044b37a276eb5bf95b2f58ae26c5ceab
database_image: postgres:17.5-bookworm
database_storage: tmpfs
network: isolated_internal_docker_network
production_database_used: false
repository_mounts: read_only_packages_migrations_and_alembic_ini_only
```

## Executed sequence

Every command below returned exit code `0` against the same disposable PostgreSQL database:

1. Fresh database `alembic upgrade 20260729_0010`
2. `alembic current` → `20260729_0010`
3. `alembic upgrade head`
4. `alembic current` → `20260730_0011 (head)`
5. `alembic downgrade 20260729_0010`
6. `alembic current` → `20260729_0010`
7. `alembic upgrade head`
8. `alembic current` → `20260730_0011 (head)`
9. Read-only PostgreSQL catalog verification

The upgrade and downgrade log named the expected edge in both directions:

```text
Running upgrade 20260729_0010 -> 20260730_0011, Persist AssetClaw V4.1 identity, timing, cancellation, and attempt evidence.
Running downgrade 20260730_0011 -> 20260729_0010, Persist AssetClaw V4.1 identity, timing, cancellation, and attempt evidence.
Running upgrade 20260729_0010 -> 20260730_0011, Persist AssetClaw V4.1 identity, timing, cancellation, and attempt evidence.
```

## Final catalog verification

```text
version_num
20260730_0011

artifact_ready_at      timestamp with time zone
assembling_at          timestamp with time zone
execution_finished_at timestamp with time zone
last_progress_at       timestamp with time zone
output_node            character varying
pipeline_commit        character varying
pipeline_sha256        character varying
queued_at              timestamp with time zone
started_at             timestamp with time zone
validated_at           timestamp with time zone

batch_cancel_operations
```

The exact temporary container `gpu-control-v155-migration-proof-20260730` and internal network
`gpu-control-v155-proof-20260730` were removed after verification. Their tmpfs test data is not
recoverable by design. No production container, database, network, job, or queue was read or changed.
