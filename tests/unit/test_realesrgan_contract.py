import hashlib
import io

import pytest
from gpu_control_realesrgan.common import ImageContractError, validate_png
from gpu_control_realesrgan.controller import _cache_name, _normalize_strength
from PIL import Image


def png(mode: str = "RGBA", size: tuple[int, int] = (8, 6)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, (20, 40, 60, 80) if mode == "RGBA" else (20, 40, 60)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def test_validate_rgb_and_rgba_png() -> None:
    assert validate_png(png("RGBA")).mode == "RGBA"
    assert validate_png(png("RGB")).mode == "RGB"


def test_validate_rejects_non_png_and_palette() -> None:
    with pytest.raises(ImageContractError) as invalid:
        validate_png(b"not-png")
    assert invalid.value.code == "INVALID_IMAGE"
    buffer = io.BytesIO()
    Image.new("P", (2, 2)).save(buffer, format="PNG")
    with pytest.raises(ImageContractError) as palette:
        validate_png(buffer.getvalue())
    assert palette.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_strength_and_cache_names_are_stable() -> None:
    assert _normalize_strength(0.700000) == "0.7"
    assert _normalize_strength(1.0) == "1"
    assert _cache_name("client", "key") == hashlib.sha256(b"client\0key").hexdigest()
