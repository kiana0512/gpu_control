"""Hard geometry guard for Klein dual-image material transfer workflows.

The Klein model can occasionally import structures from the material reference
image. This node clamps the final generated image to the visible silhouette of
image 1, so image-2-only geometry is removed after generation.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F


def _resize_image_to(image: torch.Tensor, height: int, width: int) -> torch.Tensor:
    if int(image.shape[1]) == int(height) and int(image.shape[2]) == int(width):
        return image
    nchw = image.permute(0, 3, 1, 2)
    resized = F.interpolate(
        nchw,
        size=(int(height), int(width)),
        mode="bilinear",
        align_corners=False,
    )
    return resized.permute(0, 2, 3, 1)


def _edge_background_color(image: torch.Tensor) -> torch.Tensor:
    rgb = image[..., :3].float()
    top = rgb[:, 0, :, :]
    bottom = rgb[:, -1, :, :]
    left = rgb[:, :, 0, :]
    right = rgb[:, :, -1, :]
    edges = torch.cat([top, bottom, left, right], dim=1)
    return edges.median(dim=1).values[:, None, None, :]


def _mask_from_image1(image: torch.Tensor, threshold: float) -> torch.Tensor:
    rgb = image[..., :3].float()
    threshold = float(threshold)

    if image.shape[-1] >= 4:
        alpha = image[..., 3].float()
        if alpha.max().item() > 0:
            return (alpha > threshold).float()

    background = _edge_background_color(image)
    color_delta = (rgb - background).abs().mean(dim=-1)
    luminance = rgb.mean(dim=-1)
    background_luminance = background.mean(dim=-1)
    brighter_than_background = luminance - background_luminance

    return ((color_delta > threshold) | (brighter_than_background > threshold)).float()


def _dilate(mask: torch.Tensor, pixels: int) -> torch.Tensor:
    pixels = int(pixels)
    if pixels <= 0:
        return mask
    kernel = pixels * 2 + 1
    return F.max_pool2d(mask[:, None, :, :], kernel, stride=1, padding=pixels)[:, 0]


def _soften(mask: torch.Tensor, pixels: int) -> torch.Tensor:
    pixels = int(pixels)
    if pixels <= 0:
        return mask
    kernel = pixels * 2 + 1
    softened = F.avg_pool2d(mask[:, None, :, :], kernel, stride=1, padding=pixels)[:, 0]
    return softened.clamp(0.0, 1.0)


def clamp_to_image1_geometry(
    generated_image: torch.Tensor,
    image1_geometry: torch.Tensor,
    threshold: float = 0.08,
    grow_pixels: int = 2,
    feather_pixels: int = 2,
    background_mode: str = "图1背景",
) -> tuple[torch.Tensor, torch.Tensor]:
    """Composite generated pixels only inside image1's foreground silhouette."""
    if generated_image.ndim != 4 or image1_geometry.ndim != 4:
        raise ValueError("generated_image and image1_geometry must be BHWC tensors")

    target_h = int(image1_geometry.shape[1])
    target_w = int(image1_geometry.shape[2])
    generated = _resize_image_to(generated_image.float(), target_h, target_w)
    geometry = image1_geometry.float()

    mask = _mask_from_image1(geometry, threshold)
    mask = _dilate(mask, grow_pixels)
    mask = _soften(mask, feather_pixels)

    mask_expanded = mask[..., None]
    if str(background_mode) in {"black", "黑色"}:
        background = torch.zeros_like(generated)
    else:
        background = geometry[..., : generated.shape[-1]]
        if background.shape[-1] != generated.shape[-1]:
            background = background[..., :3]
    result = generated * mask_expanded + background * (1.0 - mask_expanded)
    return result.clamp(0.0, 1.0), mask.clamp(0.0, 1.0)


class CherryImage1GeometryGuard:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generated_image": ("IMAGE",),
                "image1_geometry": ("IMAGE",),
                "mask_threshold": (
                    "FLOAT",
                    {"default": 0.08, "min": 0.0, "max": 1.0, "step": 0.01},
                ),
                "grow_pixels": (
                    "INT",
                    {"default": 2, "min": 0, "max": 64, "step": 1},
                ),
                "feather_pixels": (
                    "INT",
                    {"default": 2, "min": 0, "max": 64, "step": 1},
                ),
                "background_mode": (
                    ["image1_background", "black"],
                    {"default": "image1_background"},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("guarded_image", "image1_mask")
    FUNCTION = "guard"
    CATEGORY = "Cherry_lizi/Klein工作流"

    def guard(
        self,
        generated_image,
        image1_geometry,
        mask_threshold,
        grow_pixels,
        feather_pixels,
        background_mode,
    ):
        return clamp_to_image1_geometry(
            generated_image,
            image1_geometry,
            mask_threshold,
            grow_pixels,
            feather_pixels,
            background_mode,
        )


NODE_CLASS_MAPPINGS = {"CherryImage1GeometryGuard": CherryImage1GeometryGuard}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryImage1GeometryGuard": "Cherry - Image1 Geometry Guard"
}
