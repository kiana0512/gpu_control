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
GPU turn after the current ComfyUI frame finishes. Agent v3 keeps
`gpu-control-node-comfyui-1` running, requests no model eviction, and verifies
the same container ID, `StartedAt`, and `RestartCount` before and after native
SAL + SoRa execution. It never calls ComfyUI's model-release endpoint and never
stops, starts, or restarts that container.

This preserves the opportunity to reuse the previous approved workflow's hot
cache. It does not claim that every model remains in VRAM under Substance
memory pressure; the next real same-workflow GPU task provides the cold/hot
timing evidence. GPU Control's existing warm-workflow affinity remains enabled.

Agent identity is fail closed. Asset API accepts Substance claims only from
`substance-baker-2026.08.03-v3`. A continuity failure leaves 3090-B in recovery
drain until the original Baker reports idle and a newer healthy ComfyUI
heartbeat is observed.

For an upgrade, first verify there are no active Asset bakes or Substance fence
labels. Then run the installer with `-ConfirmNoActiveBakes`; it explicitly stops
the four existing scheduled tasks before replacing/restarting them. Confirm all
four heartbeats report v3 and `ONLINE`. This operation does not restart ComfyUI.

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
