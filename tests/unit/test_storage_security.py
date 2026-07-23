from pathlib import Path

import pytest
from PIL import Image

from packages.gpu_control_core.security import (
    hash_api_secret,
    issue_api_key,
    sign_agent_request,
    validate_callback_url,
    verify_api_key,
)
from packages.gpu_control_core.settings import Settings
from packages.gpu_control_core.storage import (
    LocalJobStorage,
    StorageError,
    inspect_image,
    safe_filename,
)


def test_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalJobStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.job_dir("../../escape")
    with pytest.raises(StorageError):
        safe_filename("../image.png")


async def test_stream_write_is_hashed_and_atomic(tmp_path: Path) -> None:
    async def chunks():
        yield b"abc"
        yield b"def"

    destination = tmp_path / "input.bin"
    size, digest = await LocalJobStorage.stream_to_file(chunks(), destination, 10)
    assert size == 6
    assert digest == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721"
    assert destination.read_bytes() == b"abcdef"


def test_api_key_hash_and_verification() -> None:
    plaintext, prefix, secret = issue_api_key()
    assert plaintext.startswith(f"gpc_{prefix}_")
    encoded = hash_api_secret(secret, "pepper")
    assert verify_api_key(encoded, secret, "pepper")
    assert not verify_api_key(encoded, secret + "x", "pepper")


def test_callback_url_blocks_ssrf() -> None:
    assert validate_callback_url("https://callback.example.com/jobs", {"callback.example.com"})
    assert not validate_callback_url("http://callback.example.com/jobs", {"callback.example.com"})
    assert not validate_callback_url("https://127.0.0.1/jobs", {"127.0.0.1"})
    assert not validate_callback_url("https://metadata.internal/jobs", {"callback.example.com"})


def test_agent_signing_binds_method_path_body_nonce() -> None:
    signature = sign_agent_request("POST", "/v1/operations", b"{}", "1", "nonce", "secret")
    assert signature != sign_agent_request(
        "POST", "/v1/operations", b'{"x":1}', "1", "nonce", "secret"
    )
    assert signature != sign_agent_request("GET", "/v1/operations", b"{}", "1", "nonce", "secret")


def test_image_validation_rejects_non_image_and_pixel_limit(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not-an-image")
    with pytest.raises(StorageError, match="valid image"):
        inspect_image(invalid, 100)
    valid = tmp_path / "valid.png"
    Image.new("RGB", (4, 3), "white").save(valid)
    assert inspect_image(valid, 12) == (4, 3, "PNG")
    with pytest.raises(StorageError, match="pixel limit"):
        inspect_image(valid, 11)


def test_production_rejects_development_secrets() -> None:
    with pytest.raises(ValueError, match="production secrets"):
        Settings(
            environment="production",
            jwt_secret="development-only-change-me",
            api_key_pepper="development-only-change-me",
            node_agent_hmac_secret="development-only-change-me",
            alertmanager_webhook_token="development-only-change-me",
        )
