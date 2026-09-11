from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MODEL_NAME = "RealESRGAN_x4plus_anime_6B"
MODEL_SHA256 = "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da"
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 16_777_216


class ImageContractError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedImage:
    width: int
    height: int
    mode: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_png(payload: bytes) -> ValidatedImage:
    if not payload:
        raise ImageContractError("INVALID_IMAGE", "PNG request body is empty", 400)
    if len(payload) > MAX_BODY_BYTES:
        raise ImageContractError("IMAGE_TOO_LARGE", "PNG exceeds the 32 MiB limit", 413)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                raise ImageContractError(
                    "UNSUPPORTED_MEDIA_TYPE", "Only PNG images are supported", 415
                )
            if getattr(image, "n_frames", 1) != 1:
                raise ImageContractError(
                    "UNSUPPORTED_MEDIA_TYPE", "Animated or multi-page PNG is unsupported", 415
                )
            if image.mode not in {"RGB", "RGBA"}:
                raise ImageContractError(
                    "UNSUPPORTED_MEDIA_TYPE", "PNG must use RGB or RGBA pixels", 415
                )
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ImageContractError("INVALID_IMAGE", "PNG dimensions are invalid", 400)
            if width * height > MAX_PIXELS:
                raise ImageContractError(
                    "IMAGE_TOO_LARGE", "PNG exceeds the 16,777,216 pixel limit", 413
                )
            image.load()
            return ValidatedImage(width=width, height=height, mode=image.mode)
    except ImageContractError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageContractError("INVALID_IMAGE", "PNG could not be decoded", 400) from exc
