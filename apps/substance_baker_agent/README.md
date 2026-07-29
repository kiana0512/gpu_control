# 3090-B Windows Substance Baker Host Agent

This agent is the only component allowed to invoke the native Windows
`substance3d_baker.exe`. It accepts fixed profiles from GPU Control, never an
arbitrary command line. The control plane clamps execution to one GPU job even
when the agent advertises a deep queue.

Required local files:

- `D:\GPUControl\secrets\GPU_CONTROL_LAN_CA.crt`
- `D:\GPUControl\secrets\asset_worker_hmac_secret.txt` (ACL: the service user only)
- `C:\Program Files\Adobe\Adobe Substance 3D Designer\substance3d_baker.exe`

The agent drains `worker-3090-b`, stops only
`gpu-control-node-comfyui-1`, runs SAL + SoRa, validates the approved output,
restores ComfyUI to `healthy`, uploads the result, and only then lets the
control plane release the GPU fence.
