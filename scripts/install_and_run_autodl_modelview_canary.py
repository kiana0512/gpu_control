#!/usr/bin/env python3
"""Reversibly install the staged bundle and run one real AutoDL canary."""

from __future__ import annotations

import argparse
import json
import shlex
import time
from pathlib import Path
from typing import Any, cast

import paramiko
import requests

APP_HOST = "https://www.autodl.art"
APP_PREFIX = "/api/v1/adl_dev/dev/instance/pro"
REMOTE_ROOT = "/root/autodl-tmp/gpu-control/modelview-inpaint-normal-2step-r1"
TOKEN_FILE = Path("/srv/gpu-control/secrets/providers/autodl.token")
KNOWN_HOSTS_FILE = Path("/srv/gpu-control/secrets/providers/autodl-known-hosts")


def snapshot(token: str, instance_id: str) -> dict[str, Any]:
    response = requests.get(
        APP_HOST + APP_PREFIX + "/snapshot",
        headers={"Authorization": token, "Content-Type": "application/json"},
        params={"instance_uuid": instance_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    data = payload.get("data")
    if payload.get("code") != "Success" or not isinstance(data, dict):
        raise RuntimeError("AutoDL snapshot request failed")
    return data


def remote(client: paramiko.SSHClient, command: str, timeout: int = 180) -> str:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f"remote command failed ({status}): {error[-2000:]}")
    return cast(str, output.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("/srv/gpu-control/jobs/deployment-autodl-modelview-normal"),
    )
    args = parser.parse_args()
    info = snapshot(TOKEN_FILE.read_text().strip(), args.instance_id)
    password = str(info.get("root_password") or "")
    if not password:
        raise RuntimeError("AutoDL did not provide SSH credentials")
    client = paramiko.SSHClient()
    client.load_host_keys(str(KNOWN_HOSTS_FILE))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        str(info["proxy_host"]),
        port=int(info["ssh_port"]),
        username="root",
        password=password,
        timeout=20,
    )
    try:
        local_runner = Path(__file__).with_name("autodl_remote_modelview_canary.py")
        remote_runner = REMOTE_ROOT + "/workflow/run_canary.py"
        sftp = client.open_sftp()
        try:
            sftp.put(str(local_runner), remote_runner)
        finally:
            sftp.close()

        install_script = f"""
set -eu
root={shlex.quote(REMOTE_ROOT)}
comfy=/root/ComfyUI
backup=/root/autodl-tmp/gpu-control/backups/modelview-inpaint-normal-2step-20260918
mkdir -p "$root/plugins/extracted" "$backup" "$comfy/models/text_encoders" "$comfy/models/unet" "$comfy/models/vae" "$comfy/models/loras/flux-kelin" "$comfy/custom_nodes" "$comfy/user/default/workflows" "$comfy/input/gpu-control-canary-normal-2step"
tar -xzf "$root/plugins/modelview-custom-nodes.tar.gz" -C "$root/plugins/extracted"
test -f "$root/models/text_encoders/qwen_3_8b_fp8mixed.safetensors"
test -f "$root/models/unet/Flux2-Klein-9B-True-V3-Q5_K.gguf"
test -f "$root/models/loras/flux-kelin/li3d_000004500.safetensors"
test -f "$root/models/loras/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors"
test -f "$root/workflow/template.api.json"
test -f "$root/workflow/ModelViewCreator_flux_fill_inpaint.json"
test -d "$root/plugins/extracted/ComfyUI_tinyterraNodes"
test -d "$root/plugins/extracted/Cherry_KleinWorkflowTools"
link_with_backup() {{
  source=$1
  target=$2
  name=$3
  if [ -L "$target" ] && [ "$(readlink -f "$target")" = "$(readlink -f "$source")" ]; then
    return 0
  fi
  if [ -e "$target" ] || [ -L "$target" ]; then
    destination="$backup/$name"
    if [ -e "$destination" ] || [ -L "$destination" ]; then
      destination="$destination.$(date +%s)"
    fi
    mv "$target" "$destination"
  fi
  ln -s "$source" "$target"
}}
link_with_backup "$root/models/text_encoders/qwen_3_8b_fp8mixed.safetensors" "$comfy/models/text_encoders/qwen_3_8b_fp8mixed.safetensors" qwen_3_8b_fp8mixed.safetensors
link_with_backup "$root/models/unet/Flux2-Klein-9B-True-V3-Q5_K.gguf" "$comfy/models/unet/Flux2-Klein-9B-True-V3-Q5_K.gguf" Flux2-Klein-9B-True-V3-Q5_K.gguf
link_with_backup "/.autodl-model/data/Comfy-Org/flux2-dev/split_files/vae/flux2-vae.safetensors" "$comfy/models/vae/flux2-vae.safetensors" flux2-vae.safetensors
link_with_backup "$root/models/loras/flux-kelin/li3d_000004500.safetensors" "$comfy/models/loras/flux-kelin/li3d_000004500.safetensors" li3d_000004500.safetensors
link_with_backup "$root/models/loras/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors" "$comfy/models/loras/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors" flux2_klein_9b_refcontrol_normal.safetensors
link_with_backup "$root/plugins/extracted/ComfyUI_tinyterraNodes" "$comfy/custom_nodes/ComfyUI_tinyterraNodes" ComfyUI_tinyterraNodes
link_with_backup "$root/plugins/extracted/Cherry_KleinWorkflowTools" "$comfy/custom_nodes/Cherry_KleinWorkflowTools" Cherry_KleinWorkflowTools
link_with_backup "$root/workflow/ModelViewCreator_flux_fill_inpaint.json" "$comfy/user/default/workflows/ModelViewCreator_flux_fill_inpaint.json" ModelViewCreator_flux_fill_inpaint.json
for name in image-canary.png material_image-canary.png mask-canary.png normal_image-canary.png; do
  cp -p "$root/input/$name" "$comfy/input/gpu-control-canary-normal-2step/$name"
done
"""
        remote(client, install_script, timeout=300)

        health = remote(
            client,
            "if curl -fsS --max-time 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then echo running; else echo stopped; fi",
        )
        if health == "running":
            # A healthy process that was already running cannot see newly linked
            # custom nodes.  Refuse to interrupt any live or queued prompt, then
            # restart only the exact ComfyUI process (never the instance).
            remote(
                client,
                "curl -fsS --max-time 5 http://127.0.0.1:6006/queue | "
                "/root/miniconda3/bin/python -c 'import json,sys; q=json.load(sys.stdin); "
                "raise SystemExit(0 if not q.get(\"queue_running\") and "
                "not q.get(\"queue_pending\") else 42)'",
            )
            stop_script = """
set -eu
pid="$(pgrep -f '(^|/)(python|python3|python3\\.11) main\\.py --port 6006([[:space:]]|$)' | head -1 || true)"
if [ -n "$pid" ]; then
  kill -TERM "$pid"
  deadline=$((SECONDS + 30))
  while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$deadline" ]; do sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "ComfyUI did not stop after SIGTERM" >&2
    exit 1
  fi
fi
"""
            remote(client, stop_script, timeout=45)
            health = "stopped"
        if health != "running":
            start_script = f"""
set -eu
cd /root/ComfyUI
nohup /root/miniconda3/bin/python main.py --port 6006 --listen 127.0.0.1 --reserve-vram 3.0 --disable-async-offload --disable-pinned-memory > {shlex.quote(REMOTE_ROOT)}/evidence/comfyui.log 2>&1 &
echo $! > {shlex.quote(REMOTE_ROOT)}/evidence/comfyui.pid
"""
            remote(client, start_script)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            health = remote(
                client,
                "if curl -fsS --max-time 3 http://127.0.0.1:6006/system_stats >/dev/null 2>&1; then echo ready; else echo wait; fi",
            )
            if health == "ready":
                break
            time.sleep(3)
        else:
            log_tail = remote(
                client,
                f"tail -120 {shlex.quote(REMOTE_ROOT)}/evidence/comfyui.log || true",
            )
            raise RuntimeError("ComfyUI did not become ready:\n" + log_tail)

        output = remote(
            client,
            f"/root/miniconda3/bin/python {shlex.quote(remote_runner)} "
            f"--instance-id {shlex.quote(args.instance_id)}",
            timeout=2700,
        )
        report = json.loads(output.splitlines()[-1])
        if report.get("status") != "PASSED":
            raise RuntimeError("remote canary did not pass")
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        sftp = client.open_sftp()
        try:
            sftp.get(
                REMOTE_ROOT + "/evidence/autodl-modelview-normal-canary.json",
                str(args.evidence_dir / "autodl-modelview-normal-canary.json"),
            )
            sftp.get(
                REMOTE_ROOT + "/evidence/autodl-modelview-normal-canary.png",
                str(args.evidence_dir / "autodl-modelview-normal-canary.png"),
            )
            sftp.get(
                REMOTE_ROOT + "/manifest.json",
                str(args.evidence_dir / "staging-manifest.json"),
            )
        finally:
            sftp.close()
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
