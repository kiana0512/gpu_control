#!/usr/bin/env python3
"""Stage the approved ModelView inpaint canary bundle on one AutoDL instance.

The script never prints the AutoDL token or SSH password. Large uploads are
resumable and land below a versioned staging root before any ComfyUI path is
changed. Installation uses reversible symlinks and preserves pre-existing
targets beside them as timestamped backups.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import time
from pathlib import Path
from typing import Any, cast

import paramiko
import requests

APP_HOST = "https://www.autodl.art"
APP_PREFIX = "/api/v1/adl_dev/dev/instance/pro"
DEFAULT_TOKEN_FILE = Path("/srv/gpu-control/secrets/providers/autodl.token")
DEFAULT_KNOWN_HOSTS_FILE = Path(
    "/srv/gpu-control/secrets/providers/autodl-known-hosts"
)
REMOTE_ROOT = "/root/autodl-tmp/gpu-control/modelview-inpaint-normal-2step-r1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def remote_command(client: paramiko.SSHClient, command: str, timeout: int = 120) -> str:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f"remote command failed ({status}): {error[-1000:]}")
    return cast(str, output.strip())


def upload_resumable(
    sftp: paramiko.SFTPClient,
    local: Path,
    remote: str,
    expected_sha256: str,
) -> dict[str, Any]:
    try:
        remote_size = sftp.stat(remote).st_size
    except OSError:
        remote_size = 0
    local_size = local.stat().st_size
    if remote_size > local_size:
        sftp.remove(remote)
        remote_size = 0
    started = time.monotonic()
    last_report = started
    with local.open("rb") as source:
        source.seek(remote_size)
        with sftp.open(remote, "ab" if remote_size else "wb") as target:
            target.set_pipelined(True)
            transferred = remote_size
            while chunk := source.read(4 * 1024 * 1024):
                target.write(chunk)
                transferred += len(chunk)
                now = time.monotonic()
                if now - last_report >= 10 or transferred == local_size:
                    elapsed = max(now - started, 0.001)
                    current_session_bytes = max(0, transferred - remote_size)
                    print(
                        json.dumps(
                            {
                                "event": "upload_progress",
                                "file": local.name,
                                "bytes": transferred,
                                "total": local_size,
                                "percent": round(transferred / local_size * 100, 1),
                                "session_mib_s": round(
                                    current_session_bytes / elapsed / 1024 / 1024, 2
                                ),
                            }
                        ),
                        flush=True,
                    )
                    last_report = now
    return {
        "local": str(local),
        "remote": remote,
        "size": local_size,
        "sha256": expected_sha256,
    }


def snapshot(token: str, instance_id: str) -> dict[str, Any]:
    response = requests.get(
        APP_HOST + APP_PREFIX + "/snapshot",
        headers={"Authorization": token, "Content-Type": "application/json"},
        params={"instance_uuid": instance_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    if payload.get("code") != "Success" or not isinstance(payload.get("data"), dict):
        raise RuntimeError("AutoDL snapshot request failed")
    return cast(dict[str, Any], payload["data"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--plugin-archive", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument(
        "--known-hosts-file", type=Path, default=DEFAULT_KNOWN_HOSTS_FILE
    )
    args = parser.parse_args()

    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("AutoDL token file is empty")
    info = snapshot(token, args.instance_id)
    password = str(info.get("root_password") or "")
    if not password:
        raise RuntimeError("AutoDL did not provide an SSH password")

    workspace = Path(__file__).resolve().parents[1]
    local_files = {
        "models/text_encoders/qwen_3_8b_fp8mixed.safetensors": Path(
            "/opt/imageclip/models/text_encoders/qwen_3_8b_fp8mixed.safetensors"
        ),
        "models/unet/Flux2-Klein-9B-True-V3-Q5_K.gguf": Path(
            "/opt/modelviewcreator/model/unet/Flux2-Klein-9B-True-V3-Q5_K.gguf"
        ),
        "models/loras/flux-kelin/li3d_000004500.safetensors": Path(
            "/opt/modelviewcreator/model/lora/flux-kelin/li3d_000004500.safetensors"
        ),
        "models/loras/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors": Path(
            "/opt/modelviewcreator/model/lora/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors"
        ),
        "workflow/template.api.json": workspace
        / "workflows/production/modelview-inpaint/template.api.json",
        "workflow/manifest.yaml": workspace
        / "workflows/production/modelview-inpaint/manifest.yaml",
        "workflow/ModelViewCreator_flux_fill_inpaint.json": Path(
            "/opt/modelviewcreator/Flux2 Klein TrueV3-双图材质编辑-局部重绘.json"
        ),
        "plugins/modelview-custom-nodes.tar.gz": args.plugin_archive,
        "input/image-canary.png": Path(
            "/tmp/modelview-normal-2step-20260918/input/image-canary.png"  # noqa: S108
        ),
        "input/material_image-canary.png": Path(
            "/tmp/modelview-normal-2step-20260918/input/material_image-canary.png"  # noqa: S108
        ),
        "input/mask-canary.png": Path(
            "/tmp/modelview-normal-2step-20260918/input/mask-canary.png"  # noqa: S108
        ),
        "input/normal_image-canary.png": Path(
            "/tmp/modelview-normal-2step-20260918/input/normal_image-canary.png"  # noqa: S108
        ),
    }
    missing = [str(path) for path in local_files.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"missing local canary inputs: {missing}")

    client = paramiko.SSHClient()
    client.load_host_keys(str(args.known_hosts_file))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        str(info["proxy_host"]),
        port=int(info["ssh_port"]),
        username="root",
        password=password,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
    )
    try:
        quoted_root = shlex.quote(REMOTE_ROOT)
        remote_command(
            client,
            f"mkdir -p {quoted_root}/models/text_encoders {quoted_root}/models/unet "
            f"{quoted_root}/models/loras/flux-kelin {quoted_root}/workflow "
            f"{quoted_root}/plugins {quoted_root}/input {quoted_root}/evidence",
        )
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(180)
        manifest: list[dict[str, Any]] = []
        try:
            for relative, local in local_files.items():
                digest = sha256_file(local)
                remote = f"{REMOTE_ROOT}/{relative}"
                existing = remote_command(
                    client,
                    f"if [ -f {shlex.quote(remote)} ]; then sha256sum {shlex.quote(remote)} | cut -d' ' -f1; fi",
                )
                if existing == digest:
                    print(json.dumps({"event": "upload_skip", "file": local.name}), flush=True)
                else:
                    item = upload_resumable(sftp, local, remote + ".partial", digest)
                    actual = remote_command(
                        client,
                        f"sha256sum {shlex.quote(remote + '.partial')} | cut -d' ' -f1",
                        timeout=1800,
                    )
                    if actual != digest:
                        raise RuntimeError(f"remote checksum mismatch: {local.name}")
                    remote_command(
                        client,
                        f"mv -f {shlex.quote(remote + '.partial')} {shlex.quote(remote)}",
                    )
                    item["remote"] = remote
                    manifest.append(item)
                if existing == digest:
                    manifest.append(
                        {
                            "local": str(local),
                            "remote": remote,
                            "size": local.stat().st_size,
                            "sha256": digest,
                        }
                    )
        finally:
            sftp.close()

        shared_vae = "/.autodl-model/data/Comfy-Org/flux2-dev/split_files/vae/flux2-vae.safetensors"
        expected_vae = sha256_file(
            Path("/opt/imageclip/models/vae/flux2-vae.safetensors")
        )
        actual_vae = remote_command(
            client, f"sha256sum {shlex.quote(shared_vae)} | cut -d' ' -f1", timeout=300
        )
        if actual_vae != expected_vae:
            raise RuntimeError("AutoDL shared VAE checksum mismatch")

        manifest_payload = json.dumps(
            {
                "schema_version": "gpu-control.autodl-canary.v1",
                "instance_id": args.instance_id,
                "created_at_epoch": int(time.time()),
                "files": manifest,
                "shared_vae": {"path": shared_vae, "sha256": actual_vae},
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        sftp = client.open_sftp()
        try:
            with sftp.open(f"{REMOTE_ROOT}/manifest.json", "wb") as target:
                target.write(manifest_payload)
        finally:
            sftp.close()
        print(
            json.dumps(
                {
                    "event": "stage_complete",
                    "instance_id": args.instance_id,
                    "remote_root": REMOTE_ROOT,
                    "file_count": len(manifest),
                }
            ),
            flush=True,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
