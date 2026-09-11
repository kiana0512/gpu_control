"""Write private Docker Engine create specs from live containers; never deploy."""

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_IMAGES = {
    "api": "sha256:4fbb7361a1ff809e3387cb9adcc8f180f789f1d7676182cdbaf71087ff7f29c0",
    "scheduler": "sha256:f4849efaffd48e020ff86d277014d38a0c12e5b6fcd80c5a2bb0c232a27189a4",
}
NODE_ID = "worker-5070ti-01"


def inspect(kind: str, reference: str) -> dict:
    return json.loads(subprocess.check_output(["/usr/bin/docker", kind, "inspect", "--", reference]))[0]  # noqa: S603 - fixed argv, operator-selected image


def private_json(path: Path, value: dict) -> str:
    data = (json.dumps(value, indent=2) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-secret-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--review-file", type=Path, required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--scheduler-image", required=True)
    args = parser.parse_args()
    secret_path = args.node_secret_file
    if secret_path.is_symlink() or stat.S_IMODE(secret_path.stat().st_mode) & 0o077:
        raise SystemExit("secret source must be a private regular file")
    secret = secret_path.read_text().strip()
    if len(secret) < 32 or any(char.isspace() for char in secret) or secret.startswith("CHANGE_ME"):
        raise SystemExit("new node secret is invalid")
    args.out_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    review = {
        "status": "PREPARED_NOT_DEPLOYED",
        "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - host Python 3.10
        "node_id": NODE_ID,
        "services": [],
        "network_note": "Assigned IPv4 addresses are retained explicitly. Stop and detach the old endpoint before creation; never start two schedulers. Reverse detach/reconnect for rollback.",
        "operation_note": "This tool made no Docker or database mutations. Each private spec contains existing production environment variables and must remain mode 0600.",
    }
    for component in BASE_IMAGES:
        name = f"gpu-control-{component}-1"
        current = inspect("container", name)
        if current["Image"] != BASE_IMAGES[component] or not current["State"]["Running"]:
            raise SystemExit("running control service identity changed; re-review before preparing")
        candidate = inspect("image", getattr(args, f"{component}_image"))
        labels = candidate["Config"].get("Labels") or {}
        if labels.get("io.gpu-control.adapter.base-image-id") != BASE_IMAGES[component]:
            raise SystemExit("candidate image does not descend from the reviewed component")
        if labels.get("org.opencontainers.image.version") != "1.5.23.post1":
            raise SystemExit("unexpected candidate version")
        spec = copy.deepcopy(current["Config"])
        original_env = dict(item.split("=", 1) for item in spec["Env"])
        updated_env = dict(original_env)
        existing_map = json.loads(updated_env.get("NODE_AGENT_HMAC_SECRETS", "{}"))
        if not isinstance(existing_map, dict) or any(
            not isinstance(v, str) for v in existing_map.values()
        ):
            raise SystemExit("existing node secret map needs review")
        if NODE_ID in existing_map and existing_map[NODE_ID] != secret:
            raise SystemExit("new node already has a different registered secret")
        legacy_secrets = [
            value
            for key, value in original_env.items()
            if key.startswith("NODE_AGENT_HMAC_SECRET") and key != "NODE_AGENT_HMAC_SECRETS"
        ]
        if secret in legacy_secrets or any(
            value == secret for key, value in existing_map.items() if key != NODE_ID
        ):
            raise SystemExit("new node secret must be independent from existing nodes")
        existing_map[NODE_ID] = secret
        updated_env["NODE_AGENT_HMAC_SECRETS"] = json.dumps(
            existing_map, separators=(",", ":"), sort_keys=True
        )
        for key in (
            "GPU_CONTROL_BUILD_VERSION",
            "GPU_CONTROL_BUILD_REVISION",
            "GPU_CONTROL_ADAPTER_BASE_IMAGE_ID",
        ):
            candidate_env = dict(item.split("=", 1) for item in candidate["Config"]["Env"])
            updated_env[key] = candidate_env[key]
        spec["Env"] = [f"{key}={value}" for key, value in updated_env.items()]
        spec["Image"] = candidate["Id"]
        spec["Labels"] = {
            **spec.get("Labels", {}),
            **labels,
            "com.docker.compose.image": candidate["Id"],
            "io.gpu-control.adapter.prepared-from-container": current["Id"],
        }
        spec["HostConfig"] = copy.deepcopy(current["HostConfig"])
        endpoints = {}
        for network, endpoint in current["NetworkSettings"]["Networks"].items():
            retained = {
                key: copy.deepcopy(endpoint[key])
                for key in ("IPAMConfig", "Links", "Aliases", "DriverOpts", "GwPriority")
                if key in endpoint
            }
            if endpoint.get("IPAddress"):
                retained["IPAMConfig"] = {
                    **(retained.get("IPAMConfig") or {}),
                    "IPv4Address": endpoint["IPAddress"],
                }
            endpoints[network] = retained
        spec["NetworkingConfig"] = {"EndpointsConfig": endpoints}
        snapshot_sha = private_json(args.out_dir / f"{component}.before.inspect.json", current)
        spec_path = args.out_dir / f"{component}.create.json"
        spec_sha = private_json(spec_path, spec)
        review["services"].append(
            {
                "component": component,
                "container_name": name,
                "expected_old_container_id": current["Id"],
                "old_image_id": current["Image"],
                "candidate_image_id": candidate["Id"],
                "source_revision": labels["org.opencontainers.image.revision"],
                "version": labels["org.opencontainers.image.version"],
                "private_spec_path": str(spec_path),
                "private_spec_sha256": spec_sha,
                "private_snapshot_sha256": snapshot_sha,
                "changed_environment_names": sorted(
                    key for key in updated_env if original_env.get(key) != updated_env[key]
                ),
                "environment_values_omitted": True,
                "host_config_preserved": spec["HostConfig"] == current["HostConfig"],
                "command_preserved": spec["Cmd"] == current["Config"]["Cmd"],
                "healthcheck_preserved": spec.get("Healthcheck")
                == current["Config"].get("Healthcheck"),
                "mounts": current["HostConfig"]["Binds"],
                "network_endpoints": endpoints,
                "compose_note": "Existing compose config-hash is inherited for inventory continuity; these specs, not the mutable workspace compose/.env, define this replacement.",
            }
        )
    args.review_file.write_text(json.dumps(review, indent=2) + "\n")
    print("PREPARED_NOT_DEPLOYED", args.review_file)


if __name__ == "__main__":
    main()
