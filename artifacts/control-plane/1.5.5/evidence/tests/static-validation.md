# GPU Control 1.5.5 final static validation

```yaml
schema_version: gpu-control-static-evidence.v1
result: PASS
source_revision: PENDING_SOURCE_COMMIT
validated_base_head: 63deec8f57dede18ee64703ccc2b2726032e2f07
started_at_utc: 2026-07-30T09:03:58Z
finished_at_utc: 2026-07-30T09:04:23Z
production_access: false
```

| Gate | Scope | Isolation | Exit | Result |
|---|---|---|---:|---|
| Ruff 0.12.5 | `packages apps scripts tests migrations` | host process, cache under `/tmp` | 0 | `All checks passed!` |
| mypy 1.17.0 strict | packages plus API, Scheduler, Node Agent, Asset API and Blender Worker | `--network none`, source read-only | 0 | `34 source files / 0 issues` |
| Python compileall | `packages apps scripts migrations tests` | pycache redirected to `/tmp` | 0 | PASS |
| Control-plane Compose | `deploy/control-plane/compose.yaml` with `.env.example` | config render only | 0 | PASS |
| GPU-node Compose | `deploy/gpu-node/compose.yaml`, `NODE_ID=verify` | config render only | 0 | PASS |
| Backup/restore security | `tests/scripts/test_backup_restore_security.sh` | synthetic `/tmp` fixtures and fake Docker only | 0 | `23/23 PASS` |
| Git whitespace | full worktree diff | read-only | 0 | PASS |

The Compose commands used `config --quiet`; no `up`, `run`, `restart`, image replacement,
database migration, job mutation, or production API call was made.
