from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = REPOSITORY_ROOT / "deploy/control-plane/nginx/nginx.conf"
NGINX_VALIDATOR = REPOSITORY_ROOT / "scripts/validate_nginx_config.sh"


def _location(config: str, selector: str) -> str:
    marker = f"location {selector} {{"
    start = config.index(marker)
    cursor = start + len(marker)
    depth = 1
    while depth:
        character = config[cursor]
        depth += character == "{"
        depth -= character == "}"
        cursor += 1
    return config[start:cursor]


def test_business_gateway_budget_supports_120_vu_without_becoming_unbounded() -> None:
    config = NGINX_CONFIG.read_text(encoding="utf-8")

    assert (
        "limit_req_zone $binary_remote_addr zone=business_api:10m rate=240r/s;"
        in config
    )
    assert (
        "limit_conn_zone $binary_remote_addr zone=business_connections:10m;"
        in config
    )
    for selector in ("^~ /api/v1/assets/", "/api/"):
        block = _location(config, selector)
        assert "limit_req zone=business_api burst=480 nodelay;" in block
        assert "limit_conn business_connections 256;" in block

    assert "zone=api:" not in config
    assert "per_ip_connections" not in config


def test_control_paths_have_independent_rate_and_connection_zones() -> None:
    config = NGINX_CONFIG.read_text(encoding="utf-8")
    contracts = {
        "= /api/v1/nodes/heartbeat": (
            "node_heartbeat",
            "node_heartbeat_connections",
            "10r/s",
            20,
            16,
            "http://api:8000",
        ),
        "= /api/v1/scheduler/capacity": (
            "scheduler_capacity",
            "scheduler_capacity_connections",
            "60r/s",
            120,
            64,
            "http://api:8000",
        ),
        "= /api/v1/assets/capacity": (
            "asset_capacity",
            "asset_capacity_connections",
            "60r/s",
            120,
            64,
            "http://asset-api:8010",
        ),
    }

    for selector, (request_zone, connection_zone, rate, burst, connections, upstream) in (
        contracts.items()
    ):
        assert (
            f"limit_req_zone $binary_remote_addr zone={request_zone}:1m rate={rate};"
            in config
        )
        assert (
            "limit_conn_zone $binary_remote_addr "
            f"zone={connection_zone}:1m;" in config
        )
        block = _location(config, selector)
        assert f"limit_req zone={request_zone} burst={burst} nodelay;" in block
        assert f"limit_conn {connection_zone} {connections};" in block
        assert upstream in block
        assert "zone=business_api" not in block
        assert "business_connections" not in block

    assert len({contract[0] for contract in contracts.values()}) == len(contracts)
    assert len({contract[1] for contract in contracts.values()}) == len(contracts)


def test_admin_keeps_an_isolated_tight_budget() -> None:
    config = NGINX_CONFIG.read_text(encoding="utf-8")
    block = _location(config, "/admin/")

    assert "limit_req_zone $binary_remote_addr zone=admin:10m rate=5r/s;" in config
    assert (
        "limit_conn_zone $binary_remote_addr zone=admin_connections:10m;" in config
    )
    assert "limit_req zone=admin burst=10 nodelay;" in block
    assert "limit_conn admin_connections 10;" in block
    assert "zone=business_api" not in block


def test_nginx_validator_is_offline_and_runs_syntax_check() -> None:
    validator = NGINX_VALIDATOR.read_text(encoding="utf-8")

    assert "nginx:1.28.0-alpine" in validator
    assert "--network none" in validator
    assert 'nginx -t -c "/etc/nginx/nginx.conf"' in validator
