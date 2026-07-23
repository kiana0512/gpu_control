import subprocess
import sys
from pathlib import Path

from scripts.bootstrap_nodes import load_inventory


def parse_env(path: Path) -> dict[str, str]:
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def test_control_env_generator_produces_consistent_ready_to_copy_files(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    control = tmp_path / "control.env"
    bundle = tmp_path / "bundle"
    inventory = tmp_path / "nodes.yaml"
    prometheus = tmp_path / "prometheus.yml"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(root / "scripts" / "generate_env.py"),
            "control",
            "--control-ip",
            "10.20.0.10",
            "--worker-a-ip",
            "10.20.0.11",
            "--worker-b-ip",
            "10.20.0.12",
            "--output",
            str(control),
            "--bundle-dir",
            str(bundle),
            "--inventory",
            str(inventory),
            "--prometheus-output",
            str(prometheus),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "control env" in result.stdout
    values = parse_env(control)
    assert "NODE_ID" not in values and "NODE_BIND_IP" not in values
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert values["REDIS_PASSWORD"] in values["REDIS_URL"]
    assert all("CHANGE_ME" not in value for value in values.values())
    worker_a = parse_env(bundle / "worker-3090-a.env")
    worker_b = parse_env(bundle / "worker-3090-b.env")
    assert worker_a["NODE_ID"] == "worker-3090-a"
    assert worker_b["NODE_ID"] == "worker-3090-b"
    assert worker_a["NODE_BIND_IP"] == "0.0.0.0"  # noqa: S104
    assert worker_a["NODE_ADVERTISE_IP"] == "10.20.0.11"
    assert worker_b["NODE_ADVERTISE_IP"] == "10.20.0.12"
    assert worker_a["NODE_AGENT_HMAC_SECRET"] != worker_b["NODE_AGENT_HMAC_SECRET"]
    assert len(load_inventory(inventory)) == 3
    rendered_prometheus = prometheus.read_text(encoding="utf-8")
    assert "http_sd_configs" in rendered_prometheus
    assert "http://api:8000/internal/prometheus/workers" in rendered_prometheus
