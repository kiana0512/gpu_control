# Native Windows MOF UV Worker

This Worker binds the licensed MinistryOfFlat runtime installed in the 4070 Ti
Windows Blender profile to GPU Control's `UV_PROCESS_V2` queue. The control
agent, job storage, Blender process, MOF process and scheduled-task recovery all
run directly on Windows. WSL2 is not part of the MOF control or execution path.

It advertises only `mof_low_seam`.  Asset API must independently identify the
fixed Worker runtime and restrict its claims to MOF UV jobs whose
`asset_profile` is explicitly `complex_non_hardsurface` or
`complex_multi_mesh`. The Worker repeats that check before touching the input.
For `complex_multi_mesh`, every face-bearing Mesh in the FBX is processed by
MOF in one globally packed UV layout, then restored to its original object
boundary. General and simple hard-surface assets remain on `legacy_pbr`;
neither side silently switches algorithms.

When a `UV_PROCESS_V2` caller omits `options.algorithm`, a capability-versioned
Linux Worker first extracts topology and edge-angle evidence. Asset API repeats
the deterministic classification and requeues confirmed complex soft-surface
or complex multi-Mesh jobs to this native Agent. Rolling Linux Workers that do
not advertise the `auto_v2` capability cannot claim the classification
preflight.

Required Windows paths:

- Blender: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`
- Runtime root: `C:\ProgramData\Li3D\MOFWorker`
- Native Agent: `C:\ProgramData\Li3D\MOFWorker\Invoke-GPUControlMofAgent.ps1`
- CA/secret: `C:\ProgramData\Li3D\MOFWorker\secrets`
- Installed add-on/runtime preferences: the licensed Windows account
  (`LILITHGAMES\zhangqichao` on the production 4070 Ti host)

Operational identities are intentionally separate: native OpenSSH management
uses the local `gpucontrol_mof` administrator, while the scheduled Agent and
licensed Blender/MOF process run interactively as `LILITHGAMES\zhangqichao`.
The Agent connects to `https://lilithgames2` with strict CA verification and a
curl `--resolve` pin to `10.3.34.11`; it never disables TLS verification.

The Worker refuses to heartbeat unless the add-on, licensed runtime, pinned
scripts, Windows Blender version and live MOF preflight all pass. It uses the
same signed heartbeat, claim, lease, progress and five-artifact completion
protocol as the existing native Windows Substance Agent.
