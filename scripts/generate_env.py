#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return lines, values


def write_env(template: Path, destination: Path, updates: dict[str, str], remove: set[str]) -> None:
    lines, _ = read_env(template)
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in remove:
                continue
            if key in updates:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        output.append(line)
    for key, value in updates.items():
        if key not in seen and key not in remove:
            output.append(f"{key}={value}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        destination.chmod(0o600)
    except OSError:
        pass


def random_secret(bytes_count: int = 32) -> str:
    return secrets.token_hex(bytes_count)


def worker_values(
    node_id: str,
    node_ip: str,
    control_ip: str,
    agent_secret: str,
    asset_secret: str,
    asset_concurrency: int,
    node_mac_address: str = "",
) -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "GPU_CONTROL_ROLE": "node",
        "CONTROL_HOST": control_ip,
        "NODE_ID": node_id,
        "NODE_BIND_IP": "0.0.0.0",  # noqa: S104 - UFW restricts worker ports to control.
        "NODE_ADVERTISE_IP": node_ip,
        "NODE_MAC_ADDRESS": node_mac_address,
        "NODE_AGENT_HMAC_SECRET": agent_secret,
        "ASSET_WORKER_HMAC_SECRET": asset_secret,
        "ASSET_WORKER_MAX_CONCURRENCY": str(asset_concurrency),
    }


def control(args: argparse.Namespace) -> None:
    for value in (args.control_ip, args.worker_a_ip, args.worker_b_ip):
        ipaddress.ip_address(value)
    if len({args.control_ip, args.worker_a_ip, args.worker_b_ip}) != 3:
        raise ValueError("control and worker addresses must be three distinct IPs")
    postgres_password = random_secret(24)
    redis_password = random_secret(24)
    agent_a = random_secret()
    agent_b = random_secret()
    agent_control = random_secret()
    asset_worker_secret = random_secret()
    updates = {
        "ENVIRONMENT": "production",
        "CONTROL_HOST": args.control_ip,
        "WORKER_3090_A_HOST": args.worker_a_ip,
        "WORKER_3090_B_HOST": args.worker_b_ip,
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            f"postgresql+asyncpg://gpu_control:{postgres_password}@postgres:5432/gpu_control"
        ),
        "REDIS_PASSWORD": redis_password,
        "REDIS_URL": f"redis://:{redis_password}@redis:6379/0",
        "JWT_SECRET": random_secret(),
        "API_KEY_PEPPER": random_secret(),
        "NODE_AGENT_HMAC_SECRET": agent_control,
        "NODE_AGENT_HMAC_SECRET_WORKER_3090_A": agent_a,
        "NODE_AGENT_HMAC_SECRET_WORKER_3090_B": agent_b,
        "NODE_AGENT_HMAC_SECRET_CONTROL_4090": agent_control,
        "ALERTMANAGER_WEBHOOK_TOKEN": random_secret(),
        "ASSET_WORKER_HMAC_SECRET": asset_worker_secret,
        "GRAFANA_ADMIN_PASSWORD": random_secret(18),
        "PUBLIC_BASE_URL": f"https://{args.control_ip}",
        "GRAFANA_BASE_URL": f"https://{args.control_ip}/grafana",
    }
    destination = Path(args.output).resolve()
    write_env(ROOT / ".env.example", destination, updates, {"NODE_ID", "NODE_BIND_IP"})

    bundle = Path(args.bundle_dir).resolve()
    write_env(
        ROOT / ".env.node.example",
        bundle / "worker-3090-a.env",
        worker_values(
            "worker-3090-a",
            args.worker_a_ip,
            args.control_ip,
            agent_a,
            asset_worker_secret,
            3,
        ),
        set(),
    )
    admin_password = bundle / "INITIAL_ADMIN_PASSWORD.txt"
    admin_password.write_text(random_secret(12) + "\n", encoding="utf-8")
    try:
        admin_password.chmod(0o600)
    except OSError:
        pass
    write_env(
        ROOT / ".env.node.example",
        bundle / "worker-3090-b.env",
        worker_values(
            "worker-3090-b",
            args.worker_b_ip,
            args.control_ip,
            agent_b,
            asset_worker_secret,
            8,
        ),
        set(),
    )
    write_env(
        ROOT / ".env.node.example",
        bundle / "control-4090.env",
        worker_values(
            "control-4090",
            args.control_ip,
            args.control_ip,
            agent_control,
            asset_worker_secret,
            2,
        ),
        set(),
    )
    inventory = Path(args.inventory).resolve()
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(
        "nodes:\n"
        f"  - id: worker-3090-a\n    display_name: 3090-A\n    host: {args.worker_a_ip}\n"
        f"    base_url: http://{args.worker_a_ip}:8188\n    agent_url: http://{args.worker_a_ip}:9201\n"
        "    pool: PRIMARY\n    mode: ACTIVE\n    gpu: RTX3090\n    max_concurrency: 1\n"
        f"  - id: worker-3090-b\n    display_name: 3090-B\n    host: {args.worker_b_ip}\n"
        f"    base_url: http://{args.worker_b_ip}:8188\n    agent_url: http://{args.worker_b_ip}:9201\n"
        "    pool: PRIMARY\n    mode: ACTIVE\n    gpu: RTX3090\n    max_concurrency: 1\n"
        f"  - id: control-4090\n    display_name: 4090 控制中心\n    host: {args.control_ip}\n"
        "    base_url: http://comfyui-4090:8188\n"
        f"    agent_url: http://{args.control_ip}:9201\n    pool: OVERFLOW\n    mode: OVERFLOW\n"
        "    gpu: RTX4090\n    max_concurrency: 1\n",
        encoding="utf-8",
    )
    prometheus_template = ROOT / "deploy" / "control-plane" / "prometheus" / "prometheus.yml"
    prometheus_output = Path(args.prometheus_output).resolve()
    prometheus_text = prometheus_template.read_text(encoding="utf-8")
    prometheus_text = re.sub(
        r'targets: \["[0-9.]+:9100", "[0-9.]+:9100"\]',
        f'targets: ["{args.worker_a_ip}:9100", "{args.worker_b_ip}:9100"]',
        prometheus_text,
    )
    prometheus_text = re.sub(
        r'targets: \["[0-9.]+:9400", "[0-9.]+:9400"\]',
        f'targets: ["{args.worker_a_ip}:9400", "{args.worker_b_ip}:9400"]',
        prometheus_text,
    )
    prometheus_output.parent.mkdir(parents=True, exist_ok=True)
    prometheus_output.write_text(prometheus_text, encoding="utf-8")
    print(f"control env: {destination}")
    print(f"worker bundles: {bundle}")
    print(f"node inventory: {inventory}")
    print(f"initial admin password: {admin_password}")
    print("下一步把两个 worker env 分别安全复制为对应主机 /opt/gpu-control/.env")


def node(args: argparse.Namespace) -> None:
    ipaddress.ip_address(args.node_ip)
    ipaddress.ip_address(args.control_ip)
    write_env(
        ROOT / ".env.node.example",
        Path(args.output).resolve(),
        worker_values(
            args.node_id,
            args.node_ip,
            args.control_ip,
            args.agent_secret,
            args.asset_worker_secret,
            args.asset_worker_concurrency,
            args.node_mac_address,
        ),
        set(),
    )
    print(f"node env: {Path(args.output).resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate consistent control and worker env files")
    sub = parser.add_subparsers(dest="command", required=True)
    control_parser = sub.add_parser("control")
    control_parser.add_argument("--control-ip", required=True)
    control_parser.add_argument("--worker-a-ip", required=True)
    control_parser.add_argument("--worker-b-ip", required=True)
    control_parser.add_argument("--output", default=str(ROOT / ".env"))
    control_parser.add_argument("--bundle-dir", default=str(ROOT / "output" / "deploy"))
    control_parser.add_argument("--inventory", default=str(ROOT / "configs" / "nodes.yaml"))
    control_parser.add_argument(
        "--prometheus-output", default=str(ROOT / "configs" / "prometheus.yml")
    )
    control_parser.set_defaults(handler=control)

    node_parser = sub.add_parser("node")
    node_parser.add_argument("--node-id", required=True)
    node_parser.add_argument("--node-ip", required=True)
    node_parser.add_argument("--control-ip", required=True)
    node_parser.add_argument("--agent-secret", required=True)
    node_parser.add_argument("--asset-worker-secret", required=True)
    node_parser.add_argument("--asset-worker-concurrency", type=int, default=2)
    node_parser.add_argument(
        "--node-mac-address",
        default="",
        help="Physical host MAC override for hybrid WSL nodes",
    )
    node_parser.add_argument("--output", default=str(ROOT / ".env"))
    node_parser.set_defaults(handler=node)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
