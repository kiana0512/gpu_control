# RTX 5070 Ti native Node Agent deployment, 2026-09-11

The new Windows/WSL node `worker-5070ti-01` at `10.3.34.18` now runs the native
Node Agent as the unprivileged `gpuagent` systemd service on port 9201. Restricted
sudo operations use the new node's own Compose file and explicit environment
file. No other node, business repository or production application container was
changed in this subtask.

## Runtime and source identity

- Exact GPU Control source: `d04d2f7bfa582444d4f65deb243b8d42fab6cc56`;
  13 selected source files independently hash-checked before installation.
- CPython 3.11.13 built with `make -j4`, without PGO, under
  `/opt/gpu-control/runtime/python-3.11.13`; installed with `make altinstall`.
- Official source XZ SHA-256:
  `8fb5f9fbc7609fa822cb31549884575db7fd9657cbffb89510b5d7975963a83a`.
  The hash was verified on both control and target hosts and matches the
  established Python 3.11.13 image pin.
- `/opt/gpu-control/.venv` uses this interpreter. `/usr/bin/python3` remains
  Python 3.10.12. Only required Ubuntu build dependencies were installed; no
  Linux NVIDIA driver or persistence-mode service was installed.
- Native Agent dependencies follow the existing installer's pinned direct
  dependencies; the complete resolved environment is saved in
  `output/5070ti-deployment-20260911/node-agent-staging/installed-requirements.freeze.txt`.
- Installed Agent main.py SHA-256:
  `010a321f03a6ab13d7c3012341a83d28f63fbdcab912d733277f0d41b6af8922`.

Python 3.10 can import the Agent but does not handle its current timeout paths
correctly: `asyncio.TimeoutError` and built-in `TimeoutError` differ in 3.10.
A local simulated Codex version timeout reproduced the uncaught exception.
Python 3.11 unifies these identities, as documented by
[Python](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.wait_for).
The new interpreter passed the corresponding handled-timeout probe.

## Restricted operations

The installed `/usr/local/sbin/gpu-node-ctl` is a documented deployment derivative
using `/opt/gpu-control/deploy/gpu-node/5070ti/compose.yaml` and
`--env-file /opt/gpu-control/.env`. It retains fixed allowed actions and log-count
validation. It does not edit repository business workflows.

Ubuntu 22.04/systemd 249 applied kernel NoNewPrivs=1 to the existing unit because
its address-family seccomp restriction ran under `User=gpuagent`. This prevented
all sudo operations even though the unit specified `NoNewPrivileges=false`.
A new-node-only `10-restricted-sudo.conf` drop-in clears `RestrictAddressFamilies`.
`User=gpuagent`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true` and
the command-limited sudoers rule remain. Kernel NoNewPrivs was then verified as 0
and effective capabilities remained zero. The source repository unit is unchanged.

The HMAC was read from the new node's existing private `.env`, never logged, and
written to root-owned `/etc/gpu-control/node-agent.env` mode 0600. No Codex
credentials, user profile or another worker's runtime home were copied.

## Verification

- Python SSL, ctypes, bz2, lzma, sqlite, venv and asyncio checks passed.
- Agent import, 13-file source check and `visudo -cf` passed.
- Service is `enabled`, `active/running`.
- `/health/live`, `/health/ready`, signed GPU metrics and signed system metrics:
  HTTP 200. Unauthenticated GPU metrics: HTTP 401.
- Running `status`, `nvidia-smi` and `system` through sudo as `gpuagent`:
  exit code 0 for all three.
- The same three actions through authenticated `/v1/operations`: HTTP 200.
- At this subtask's acceptance, `/v1/identity` returned 503 because the new node
  had not completed control-plane registration. Registration and subsequent
  recovery acceptance are owned by the main deployment task.

Evidence and reviewable deployment scripts are under
`output/5070ti-deployment-20260911/node-agent-staging/`. The acceptance JSON,
installed file hashes and pre-fix failing operation evidence are preserved.
No start/stop/restart of ComfyUI was performed by this subtask's tests.
