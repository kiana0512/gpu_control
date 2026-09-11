# 5070 Ti Codex authentication probe deployment, 2026-09-11

The new node initially had no credentials but reported `INVALID / FAILED /
AUTH_INVALID`. The Worker treated a missing authentication file, malformed JSON,
and unrelated local execution errors as the same authentication failure.

At 03:16:31 UTC, only `gpu-control-node-blender-worker-1` on 10.3.34.18 was
recreated with `li3d/blender-worker:1.4.75-uv-overlap-v28-pins1-codex-auth1`.
The controller independently showed `DRAINING`, zero Worker current jobs and zero
CLAIMED/RUNNING Asset jobs immediately before the change. Existing GPU services,
other nodes and the independent device authorization container were not restarted.

## Behavior

| Observation | Authentication | Execution probe | Error |
| --- | --- | --- | --- |
| CLI installed, authentication file absent | MISSING | BLOCKED | AUTH_MISSING |
| Unreadable, malformed, empty or non-object authentication file | INVALID | BLOCKED | AUTH_INVALID |
| Nonempty JSON file and executable version check only | PRESENT | NOT_RUN | — |
| Actual model round trip returns exactly CODEX_HEALTH_OK | AUTHENTICATED | HEALTHY | — |
| CLI reports reused refresh token | EXPIRED | FAILED | AUTH_REFRESH_REUSED |
| CLI reports invalid API key | INVALID | FAILED | AUTH_INVALID |
| Local process/output failure after authentication preflight | PRESENT | FAILED | PROBE_RUNTIME_ERROR |

Missing/invalid files stop before starting a model request. Existing exact output,
timeout/termination, refreshed credential persistence, skill hash/link validation,
and idle-only retry safeguards remain in place. Prior success timestamps are
historical evidence and are not overwritten into a new success after failure.
The existing generic unauthorized/token-expiry error mapping is retained;
`AUTH_UNAUTHORIZED` alone does not identify a specific underlying token defect.

The main control API already requires fresh ONLINE Worker heartbeat, fresh probe,
AUTHENTICATED and HEALTHY together for scheduling eligibility. Its stale-heartbeat
and stale-probe states and the Web UI are managed by the parallel control-plane
deployment. Asset API code and schema did not require changes for these statuses.

## Exact source and image

The packaged module actually imported by the live V28 Worker is
`/usr/local/lib/python3.11/site-packages/gpu_control_blender_worker/main.py`.
It differs from the non-imported `/app/apps/blender_worker/src/.../main.py`
source copy. This patch was extracted from the actual imported module, avoiding
the repository's unrelated pending UV changes.

- Base Docker-reported image ID / OCI index:
  `sha256:425f3e3cb5ad6aa5e41ad6968c0221ab5bcd24bdbdd593d815981ca43d1cae25`.
- Derived Docker-reported image ID / OCI index:
  `sha256:9a871e05c74d6f2bffd4097dae379aea8affc380bc00006a3d0cafb0db36084b`.
- Derived image config digest:
  `sha256:0b35f226fd21c513002b9939b6cba64ce6df32269589859ee0af36433c526b54`.
- Base imported module SHA-256:
  `918da248d79f1e41c572d0148e07c0abff317b8bd587315b9f3f69734c216c87`.
- Patched imported module SHA-256:
  `d4b934334400d9337971dd6058fc4ac228bee9be56eb9a943eeef5ddadf4c9f1`.
- Docker archive SHA-256:
  `dccefcff0ca3c8986330e763e64a6ee6a896201b3dfc3cd23b854ffea9cda905`.

The archive was transferred over verified SSH and checked before Docker load.
These are save/load identities; no registry pull availability is claimed. All
base filesystem layers remain identical, with one added layer replacing only
the imported Worker module. AST comparison proves all top-level code except
`inspect_codex_auth`, `inspect_codex_runtime`, `run_codex_health_probe` and
`classify_codex_error` is unchanged. No business workflow, model, parameter,
embedded resource or approved skill changed.

## Validation

38 tests passed in an isolated, network-disabled container using the live base
dependencies and the patched imported module. The suite retains prior exact
execution, timeout, skill-drift and token-refresh tests and adds missing,
malformed/unreadable authentication, local spawn failure, independent new login,
and public-vs-private CA handling. Pytest was installed only in a disposable test
image, not the deployed production image.

At 03:17:10 UTC, direct inspection and execution of the patched probe as UID/GID
10002 on the new node returned the real CLI version `0.146.0-alpha.3.1` and
`MISSING / BLOCKED / AUTH_MISSING` with no auth file. At 03:17:14 UTC, the stored
Worker heartbeat reported the same classification, ONLINE, zero jobs and
RetopoFlow HEALTHY. All three active skill content validators, managed links,
retopology-v6 and Direct V2 package verification passed on the new runtime.

The Worker retains `SSL_CERT_FILE=/run/certs/lan-ca.crt` for the internal API.
The existing `codex_environment` removes that specific LAN-only override for
Codex, which uses the image's system CA bundle. A new-node public TLS handshake
succeeded, and the independent CLI obtained a real device code from OpenAI.

## Independent authorization

A separate temporary container named `gpucontrol-5070ti-codex-device-auth` runs
the approved CLI as UID/GID 10002 with the new node's dedicated persistent home
`/opt/gpu-control/runtime/codex/worker-5070ti-01-retopology-v6-home`.
It explicitly uses file credential storage and `codex login --device-auth`.
No other node's credentials or home were copied. Device codes and auth contents
are not recorded in repository files. At the validation snapshot above,
authorization was still waiting for the user; no authenticated execution success
is claimed by that snapshot. The first independent device flow subsequently
reported a 15-minute timeout and exited with status 1. Its temporary container
was removed automatically. No new code was requested automatically; the node
remains truthfully MISSING until a later authorization succeeds.

The device-code method and CODEX_HOME credential storage follow
[official Codex authentication documentation](https://developers.openai.com/codex/auth).
After the user authorizes, require the normal idle Worker model probe and fresh
stored heartbeat to report AUTHENTICATED / HEALTHY before treating Codex as usable.

## Reproduction and rollback

Isolated baseline, patch, Dockerfiles, tests, archive, deployment script and JSON
receipts are under `output/5070ti-deployment-20260911/probe-staging/`.
`patch-proof.json` records code/layer checks; `new-worker-runtime-acceptance.json`
and before/after heartbeat JSON hold sanitized evidence. The reviewed compose
file is `deploy/gpu-node/5070ti/compose.yaml`.

For rollback, first drain the node and confirm Asset Worker current jobs and
CLAIMED/RUNNING jobs are zero. Restore only the blender-worker image tag to
`li3d/blender-worker:1.4.75-uv-overlap-v28-pins1` and recreate only that service
using `/opt/gpu-control/.env` and the 5070-specific compose. A target-side
`compose.yaml.before-codex-auth1` copy records the pre-change configuration.
Preserve the dedicated authentication home and do not stop the separate login
container during an active authorization session.

## Release metadata and credential audit

The final archive audit scanned 457 textual deployment artifacts against
the known runtime secrets and recognizable private-key, JWT and OpenAI key
formats, and recursively checked sensitive JSON credential fields, without
recording matching values. No matches were found. The retained
125,320,171-byte `asset-runtime-no-credentials.tar.gz` also passed its full archive
SHA-256, all 465 member hashes and exact allowlist membership. There were no
credential-like paths, private-key material or known runtime secrets in its
members. `release-secret-audit.json` records the result. The node-specific compose
contains environment references for the two required service secrets and no
inline secret values or seed auth mount.

Auditing installed Python distribution records found an existing base-image
problem: `gpu_control-1.5.23.dist-info/RECORD` had obsolete entries for both
`gpu_control_blender_worker/bootstrap.py` and `main.py`. This was present in the
V28 image before the Codex patch. The auth1 patch did not repair that record.

`li3d/blender-worker:1.4.75-uv-overlap-v28-pins1-codex-auth2` is prepared to repair
only those RECORD hash/size entries. Its image tag and Worker build metadata use
auth2; the installed core distribution version remains 1.5.23. The actual new
filesystem layer contains only the RECORD file, all previous layers are identical,
and complete distribution-record verification now reports zero mismatches.
The deployed, already-tested module and every UV/skill file remain byte-identical.
After the controller drained the node, an independent check confirmed zero Asset
current jobs and zero CLAIMED/RUNNING jobs. Auth2 replaced only the new node's
CPU Worker at 03:34:59 UTC. Both repository and target-specific compose files now
refer to auth2. At 03:35:37 UTC the target's real imported module retained its
auth1 hash, every recorded installed-package file matched RECORD, all skill/link
and embedded-package validators passed, and the probe remained MISSING / BLOCKED
/ AUTH_MISSING. The 03:35:36 UTC heartbeat was ONLINE with zero current jobs and
RetopoFlow HEALTHY. The parent controller was notified to restore ACTIVE through
the normal management API after this acceptance. A final read-only database check
at 03:37 UTC confirmed ACTIVE, a fresh ONLINE Worker heartbeat, zero current jobs,
RetopoFlow HEALTHY and the correct MISSING authentication state. This metadata-only
rollout did not restart GPU containers or change business files.

- Image ID / OCI index:
  `sha256:0c6ea65709566114544710dcac3255b94ac71f53138f158e4a678c6abcacb992`.
- Image config digest:
  `sha256:a56f3503de9a620e4446fc0c180c258ab0cc11fb188d3d13a2971f21c93ad41f`.
- `probe-staging/blender-worker-codex-auth2.docker.tar`: 693,899,264 bytes,
  SHA-256 `3e80a821254e1bf4b1a773fb74ac1ed520a76b8e9e4a52383ac61429ad949c14`.
- `Dockerfile.metadata`, `auth2-proof.json` and `auth2-metadata-audit.json`
  contain the reproducible change and verification.
