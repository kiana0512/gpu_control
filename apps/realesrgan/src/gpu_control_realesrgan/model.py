from __future__ import annotations

import gc
import hashlib
import io
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.nn import functional as functional

from .common import MODEL_NAME, MODEL_SHA256, validate_png


class ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat: int = 64, num_grow_ch: int = 32) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.leaky_relu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value1 = self.leaky_relu(self.conv1(value))
        value2 = self.leaky_relu(self.conv2(torch.cat((value, value1), 1)))
        value3 = self.leaky_relu(self.conv3(torch.cat((value, value1, value2), 1)))
        value4 = self.leaky_relu(self.conv4(torch.cat((value, value1, value2, value3), 1)))
        value5 = self.conv5(torch.cat((value, value1, value2, value3, value4), 1))
        return value5 * 0.2 + value


class RRDB(nn.Module):
    def __init__(self, num_feat: int, num_grow_ch: int = 32) -> None:
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.rdb3(self.rdb2(self.rdb1(value))) * 0.2 + value


class RRDBNet(nn.Module):
    def __init__(self, num_block: int = 6) -> None:
        super().__init__()
        num_feat = 64
        self.conv_first = nn.Conv2d(3, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*(RRDB(num_feat, 32) for _ in range(num_block)))
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, 3, 3, 1, 1)
        self.leaky_relu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        feature = self.conv_first(value)
        body_feature = self.conv_body(self.body(feature))
        feature = feature + body_feature
        feature = self.leaky_relu(
            self.conv_up1(functional.interpolate(feature, scale_factor=2, mode="nearest"))
        )
        feature = self.leaky_relu(
            self.conv_up2(functional.interpolate(feature, scale_factor=2, mode="nearest"))
        )
        return self.conv_last(self.leaky_relu(self.conv_hr(feature)))


def _mb(value: int) -> int:
    return int(round(value / (1024 * 1024)))


class RealESRGANRuntime:
    def __init__(self, model_path: str, tile: int = 256, tile_pad: int = 16) -> None:
        self.model_path = Path(model_path)
        self.tile = tile
        self.tile_pad = tile_pad
        self.device = torch.device("cuda:0")
        self.model: RRDBNet | None = None
        self.ready = False
        self.last_error: str | None = None
        self.last_metrics: dict[str, Any] = {}
        self.model_sha256 = ""

    def load(self) -> None:
        self.ready = False
        if torch.version.cuda is None or not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; CPU fallback is forbidden")
        digest = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        if digest != MODEL_SHA256:
            raise RuntimeError(f"model SHA-256 mismatch: {digest}")
        checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=True)
        state = checkpoint.get("params_ema") or checkpoint.get("params")
        if not isinstance(state, dict):
            raise RuntimeError("model checkpoint has no params_ema state")
        model = RRDBNet(num_block=6)
        model.load_state_dict(state, strict=True)
        model.eval().half().to(self.device)
        self.model = model
        self.model_sha256 = digest
        probe = np.zeros((32, 32, 4), dtype=np.uint8)
        probe[..., :3] = np.arange(32, dtype=np.uint8)[None, :, None]
        probe[..., 3] = np.arange(32, dtype=np.uint8)[:, None]
        output, _ = self.enhance_rgba(probe, 1.0)
        if output.shape != probe.shape or not np.array_equal(output[..., 3], probe[..., 3]):
            raise RuntimeError("CUDA output dimension/alpha probe failed")
        self.ready = True
        self.last_error = None

    def unload(self) -> None:
        self.ready = False
        self.model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _restore_tile(self, bgr: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        tensor = torch.from_numpy(np.ascontiguousarray(bgr.transpose(2, 0, 1)))
        tensor = tensor.unsqueeze(0).to(self.device, dtype=torch.float16).div_(255.0)
        with torch.inference_mode():
            restored = self.model(tensor).clamp_(0, 1)
            restored_bytes = restored.squeeze(0).mul(255.0).round().to(torch.uint8)
        result = restored_bytes.permute(1, 2, 0).cpu().numpy()
        del tensor, restored, restored_bytes
        return result

    def _tiled_restore_to_original(self, rgb: np.ndarray) -> np.ndarray:
        height, width = rgb.shape[:2]
        output = np.empty_like(rgb)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        for y_start in range(0, height, self.tile):
            y_end = min(y_start + self.tile, height)
            y_pad_start = max(0, y_start - self.tile_pad)
            y_pad_end = min(height, y_end + self.tile_pad)
            for x_start in range(0, width, self.tile):
                x_end = min(x_start + self.tile, width)
                x_pad_start = max(0, x_start - self.tile_pad)
                x_pad_end = min(width, x_end + self.tile_pad)
                restored_pad = self._restore_tile(
                    bgr[y_pad_start:y_pad_end, x_pad_start:x_pad_end]
                )
                scale = 4
                crop = restored_pad[
                    (y_start - y_pad_start) * scale : (y_end - y_pad_start) * scale,
                    (x_start - x_pad_start) * scale : (x_end - x_pad_start) * scale,
                ]
                # Downsample each aligned x4 tile immediately so the full x4 image
                # never resides in GPU or host memory.
                downsampled = cv2.resize(
                    crop,
                    (x_end - x_start, y_end - y_start),
                    interpolation=cv2.INTER_AREA,
                )
                output[y_start:y_end, x_start:x_end] = cv2.cvtColor(
                    downsampled, cv2.COLOR_BGR2RGB
                )
                del restored_pad, crop, downsampled
        return output

    def enhance_rgba(self, rgba: np.ndarray, strength: float) -> tuple[np.ndarray, dict[str, Any]]:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        allocated_before = torch.cuda.memory_allocated()
        free_before, total = torch.cuda.mem_get_info()
        started = time.perf_counter()
        original_rgb = rgba[..., :3]
        restored_rgb = self._tiled_restore_to_original(original_rgb)
        blended = np.rint(
            strength * restored_rgb.astype(np.float32)
            + (1.0 - strength) * original_rgb.astype(np.float32)
        ).clip(0, 255).astype(np.uint8)
        output = np.empty_like(rgba)
        output[..., :3] = blended
        output[..., 3] = rgba[..., 3]
        torch.cuda.synchronize()
        peak = torch.cuda.max_memory_allocated()
        del restored_rgb, blended
        gc.collect()
        torch.cuda.empty_cache()
        free_after, _ = torch.cuda.mem_get_info()
        metrics = {
            "processing_ms": int(round((time.perf_counter() - started) * 1000)),
            "allocated_before_mb": _mb(allocated_before),
            "peak_additional_mb": _mb(max(0, peak - allocated_before)),
            "vram_free_before_mb": _mb(free_before),
            "vram_free_after_mb": _mb(free_after),
            "vram_total_mb": _mb(total),
        }
        self.last_metrics = metrics
        return output, metrics

    def enhance_png(self, payload: bytes, strength: float) -> tuple[bytes, dict[str, Any]]:
        validate_png(payload)
        with Image.open(io.BytesIO(payload)) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        output, metrics = self.enhance_rgba(rgba, strength)
        buffer = io.BytesIO()
        Image.fromarray(output, mode="RGBA").save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue(), metrics

    def status(self) -> dict[str, Any]:
        device_name = None
        free_mb = None
        total_mb = None
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            free, total = torch.cuda.mem_get_info()
            free_mb, total_mb = _mb(free), _mb(total)
        return {
            "ready": self.ready,
            "model": MODEL_NAME,
            "model_sha256": self.model_sha256 or MODEL_SHA256,
            "python": ".".join(str(v) for v in __import__("sys").version_info[:3]),
            "pytorch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "device": device_name,
            "tile": self.tile,
            "tile_pad": self.tile_pad,
            "precision": "FP16",
            "vram_free_mb": free_mb,
            "vram_total_mb": total_mb,
            "last_metrics": self.last_metrics,
            "last_error": self.last_error,
        }
