# Windows MOF UV Worker

This Worker binds the licensed MinistryOfFlat runtime installed in the 4070 Ti
Windows Blender profile to GPU Control's `UV_PROCESS_V2` queue.  The control
agent runs under the existing `gpucontrol` WSL account and launches native
Windows Blender through WSL interop.

It advertises only `mof_low_seam`.  Asset API must independently identify the
fixed Worker runtime and restrict its claims to MOF UV jobs whose
`asset_profile` is explicitly `complex_non_hardsurface`.  The Worker repeats
that semantic check before touching the input.  Hard-surface and general
assets remain on `legacy_pbr`; neither side silently switches algorithms.
Existing Linux Workers continue to advertise only `legacy_pbr`.

Required Windows paths:

- Blender: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`
- Runtime root: `C:\ProgramData\Li3D\MOFWorker`
- Installed add-on/runtime preferences: the same Windows account used by WSL
  interop (`zhangqichao` on the production 4070 Ti host)

The Worker refuses to heartbeat unless the add-on, licensed runtime, pinned
scripts, Windows Blender version and live MOF preflight all pass.
