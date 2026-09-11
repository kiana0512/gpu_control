import json

import pytest
from pydantic import ValidationError

from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings

NEW_NODE = "worker-5070ti-01"
NEW_SECRET = "new-node-test-secret-" + "n" * 40


def test_new_node_hmac_does_not_change_legacy_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_AGENT_HMAC_SECRETS", json.dumps({NEW_NODE: NEW_SECRET}))
    cfg = Settings(
        _env_file=None,
        node_agent_hmac_secret="legacy-default-" + "d" * 40,
        node_agent_hmac_secret_worker_3090_a="legacy-a-" + "a" * 40,
        node_agent_hmac_secret_worker_3090_b="legacy-b-" + "b" * 40,
        node_agent_hmac_secret_worker_4070ti="legacy-4070-" + "c" * 40,
        node_agent_hmac_secret_control_4090="legacy-4090-" + "e" * 40,
    )
    assert cfg.node_agent_secret(NEW_NODE) == NEW_SECRET
    assert cfg.node_agent_secret("worker-3090-a") == "legacy-a-" + "a" * 40
    assert cfg.node_agent_secret("worker-3090-b") == "legacy-b-" + "b" * 40
    assert cfg.node_agent_secret("worker-4070ti-animation-host-01") == "legacy-4070-" + "c" * 40
    assert cfg.node_agent_secret("control-4090") == "legacy-4090-" + "e" * 40
    assert cfg.node_agent_secret("worker-legacy-unspecified") == cfg.node_agent_hmac_secret
    payload = b'{"node_id":"worker-5070ti-01"}'
    new_signature = sign_agent_request(
        "POST", "/api/v1/nodes/heartbeat", payload, "1", "nonce", cfg.node_agent_secret(NEW_NODE)
    )
    legacy_signature = sign_agent_request(
        "POST",
        "/api/v1/nodes/heartbeat",
        payload,
        "1",
        "nonce",
        cfg.node_agent_secret("control-4090"),
    )
    assert new_signature != legacy_signature


def test_new_secret_is_excluded_from_repr_and_serialization() -> None:
    cfg = Settings(_env_file=None, node_agent_hmac_secrets={NEW_NODE: NEW_SECRET})
    assert "node_agent_hmac_secrets" not in cfg.model_dump()
    assert NEW_SECRET not in repr(cfg)
    assert NEW_SECRET not in cfg.model_dump_json()
    assert NEW_SECRET not in repr(cfg.node_agent_hmac_secrets)


@pytest.mark.parametrize(
    "value",
    [
        "not-json-sensitive-value",
        "[]",
        {"invalid-node": NEW_SECRET},
        {NEW_NODE: "short-sensitive-value"},
        {NEW_NODE: "CHANGE_ME" + "x" * 40},
        {NEW_NODE: "x" * 40 + "\n"},
        {NEW_NODE: 12345678901234567890123456789012345678},
        {NEW_NODE: {"nested": NEW_SECRET}},
        {NEW_NODE: NEW_SECRET, "worker-5070ti-02": NEW_SECRET},
        '{"worker-5070ti-01":"' + NEW_SECRET + '","worker-5070ti-01":"' + "y" * 40 + '"}',
    ],
)
def test_invalid_secret_maps_fail_without_leaking_input(value: object) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, node_agent_hmac_secrets=value)
    assert NEW_SECRET not in str(error.value)
    assert "sensitive-value" not in str(error.value)


def test_invalid_json_from_environment_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_AGENT_HMAC_SECRETS", '{"worker-5070ti-01":"private-broken-value')
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "private-broken-value" not in str(error.value)


def test_explicit_map_can_migrate_a_legacy_node_without_reusing_default() -> None:
    cfg = Settings(_env_file=None, node_agent_hmac_secrets={"worker-3090-a": NEW_SECRET})
    assert cfg.node_agent_secret("worker-3090-a") == NEW_SECRET
    assert cfg.node_agent_secret("worker-3090-b") == cfg.node_agent_hmac_secret
