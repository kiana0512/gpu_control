# 3090-B Windows Substance Baker Host Agent

This agent is the only component allowed to invoke the native Windows
`substance3d_baker.exe`. It accepts fixed profiles from GPU Control, never an
arbitrary command line. The control plane clamps execution to one GPU job even
when the agent advertises a deep queue.

Required local files:

- `D:\GPUControl\secrets\GPU_CONTROL_LAN_CA.crt`
- `D:\GPUControl\secrets\asset_worker_hmac_secret.txt` (ACL: the service user only)
- `C:\Program Files\Adobe\Adobe Substance 3D Designer\substance3d_baker.exe`

The Asset API drains `worker-3090-b` and gives production PBR the next physical
GPU turn after the current ComfyUI frame finishes. Agent v6 keeps
`gpu-control-node-comfyui-1` running, requests no model eviction, and verifies
the same container ID, `StartedAt`, and `RestartCount` before and after native
SAL + SoRa execution. It never calls ComfyUI's model-release endpoint and never
stops, starts, or restarts that container. Its result schema preserves an
unavailable PowerShell exit code as unobserved/null and requires every Baker
command's own completion marker; it never fabricates an observed zero exit.

This preserves the opportunity to reuse the previous approved workflow's hot
cache. It does not claim that every model remains in VRAM under Substance
memory pressure; the next real same-workflow GPU task provides the cold/hot
timing evidence. GPU Control's existing warm-workflow affinity remains enabled.

Agent identity is fail closed. Asset API accepts Substance claims only from
`substance-baker-2026.08.03-v6`. Every heartbeat carries a per-process Agent
generation plus a fail-closed, host-wide `Win32_Process` probe for
`substance3d_baker.exe`. Each stable Worker ID also holds a full-lifetime
`Global\` named mutex, so a duplicate scheduled, manual, or reinstall-launched
Agent fails before it can send a heartbeat. V6 additionally treats any failed
`Kill()`, timed-out `WaitForExit(10000)`, failed process refresh, or still-live
`HasExited` observation as
`SUBSTANCE_BAKER_TERMINATION_UNCONFIRMED`. That error is never retryable: Asset
API keeps a durable recovery interlock instead of allowing an orphan Baker to
overlap a new Baker or ComfyUI assignment. A continuity failure, unverified
termination, or ambiguous lease expiry leaves 3090-B in recovery drain until
the current Agent generation reports a healthy host probe with zero native
Baker processes, a newer healthy ComfyUI heartbeat is observed, and a later
zero-process probe closes the two-phase recovery handshake. A heartbeat whose
local `current_jobs` contradicts durable leases, or whose host process count
exceeds all live Substance leases, is `DRAINING`, never `ONLINE`. Restarting an
Agent and resetting its in-memory job counter is not recovery evidence.

For the v5-to-v6 upgrade, first freeze new PBR intake and verify there are no
active Asset bakes, native Baker processes, or Substance fence/recovery labels.
Then run the installer with `-ConfirmNoActiveBakes`; it explicitly stops the
four existing scheduled tasks before replacing/restarting them. While the old
Asset API still requires v5, the four v6 Agents must report `DRAINING`; this is
the intended fail-closed compatibility window. Upgrade Asset API to the build
that requires v6, then confirm all four heartbeats report the exact v6 identity,
a `HEALTHY/0` host-process probe, and `ONLINE` before unfreezing PBR intake.
Linux Workers roll only after this gate. This operation does not restart
ComfyUI.

The scheduled tasks use the current Windows user's interactive token because the
installed Adobe license is user-scoped. The installer hides their PowerShell
windows so an operator cannot accidentally close an Agent console. It also adds
an idempotent one-minute liveness trigger (`MultipleInstances=IgnoreNew`), keeps
the tasks alive across desktop idle/power-source transitions, and verifies that
all four tasks remain `Running` before installation succeeds. A normal running
Agent ignores the recovery trigger; an Agent that exits with Windows console
status `0xC000013A` is offered again within one minute. A Windows logoff still
removes an interactive token, so the agents resume at the next logon; converting
the Adobe runtime to a dedicated non-interactive service identity requires a
separate license and secret-ACL acceptance and must not be inferred here.
