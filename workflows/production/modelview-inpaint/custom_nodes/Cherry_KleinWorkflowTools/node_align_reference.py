# -*- coding: utf-8 -*-
"""Cherry - 双图基准匹配节点。"""

import sys
from pathlib import Path

import torch

try:
    from .node_align_pair import (
        _detect_bbox,
        _make_canvas,
        _p2t,
        _parse_color,
        _t2p,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Cherry_lizi"))
    from node_align_pair import (
        _detect_bbox,
        _make_canvas,
        _p2t,
        _parse_color,
        _t2p,
    )


def _contain_geometry(image_size, canvas):
    """Return uniform scale and top-left offset for contain fitting."""
    width, height = image_size
    scale = min(canvas / width, canvas / height)
    return scale, (canvas - width * scale) / 2, (canvas - height * scale) / 2


def _follower_geometry(image_size, bbox, target_bbox):
    """Fit a subject inside target_bbox without stretching and match centers."""
    del image_size
    left, top, right, bottom = bbox
    target_left, target_top, target_right, target_bottom = target_bbox
    subject_w = right - left + 1
    subject_h = bottom - top + 1
    target_w = target_right - target_left + 1
    target_h = target_bottom - target_top + 1
    scale = min(target_w / subject_w, target_h / subject_h)
    target_cx = (target_left + target_right) / 2
    target_cy = (target_top + target_bottom) / 2
    source_cx = (left + right) / 2
    source_cy = (top + bottom) / 2
    return scale, target_cx - source_cx * scale, target_cy - source_cy * scale


def _transform_bbox(bbox, scale, x, y):
    left, top, right, bottom = bbox
    return (
        x + left * scale,
        y + top * scale,
        x + (right + 1) * scale - 1,
        y + (bottom + 1) * scale - 1,
    )


def _render(img, canvas, scale, x, y, bg_mode, bg_color):
    width = max(1, round(img.size[0] * scale))
    height = max(1, round(img.size[1] * scale))
    resized = img.resize((width, height)).convert("RGBA")
    out = _make_canvas(canvas, bg_mode, bg_color, resized)
    out.alpha_composite(resized, (round(x), round(y)))
    return out.convert("RGB")


class CherryAlignReference:
    """Match one image subject to the selected reference image composition."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像A": ("IMAGE",),
                "图像B": ("IMAGE",),
                "对齐基准": (["图像A", "图像B"], {
                    "default": "图像A",
                    "tooltip": "基准图保留原构图，另一张图匹配其主体大小和位置",
                }),
                "画布尺寸": ("INT", {
                    "default": 2048, "min": 64, "max": 4096, "step": 1,
                }),
                "背景模式": (["自动采样", "自定义颜色", "边缘延伸"], {
                    "default": "自动采样",
                }),
                "背景色": ("STRING", {"default": ""}),
                "检测阈值": ("INT", {
                    "default": 18, "min": 1, "max": 100, "step": 1,
                }),
                "使用Alpha": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("图像A_匹配", "图像B_匹配")
    FUNCTION = "align"
    CATEGORY = "cherry_"

    def align(self, 图像A, 图像B, 对齐基准, 画布尺寸, 背景模式, 背景色,
              检测阈值, 使用Alpha):
        count = min(图像A.shape[0], 图像B.shape[0])
        if count <= 0:
            raise ValueError("图像A和图像B必须至少各包含一张图")

        bg_color = _parse_color(背景色) if 背景模式 == "自定义颜色" else (0, 0, 0)
        outputs_a, outputs_b = [], []

        for index in range(count):
            image_a = _t2p(图像A[index])
            image_b = _t2p(图像B[index])
            bbox_a = _detect_bbox(image_a, 检测阈值, 使用Alpha)
            bbox_b = _detect_bbox(image_b, 检测阈值, 使用Alpha)

            if 对齐基准 == "图像B":
                ref_scale, ref_x, ref_y = _contain_geometry(image_b.size, 画布尺寸)
                target_bbox = _transform_bbox(bbox_b, ref_scale, ref_x, ref_y)
                follower_scale, follower_x, follower_y = _follower_geometry(
                    image_a.size, bbox_a, target_bbox
                )
                geometry_a = (follower_scale, follower_x, follower_y)
                geometry_b = (ref_scale, ref_x, ref_y)
            else:
                ref_scale, ref_x, ref_y = _contain_geometry(image_a.size, 画布尺寸)
                target_bbox = _transform_bbox(bbox_a, ref_scale, ref_x, ref_y)
                follower_scale, follower_x, follower_y = _follower_geometry(
                    image_b.size, bbox_b, target_bbox
                )
                geometry_a = (ref_scale, ref_x, ref_y)
                geometry_b = (follower_scale, follower_x, follower_y)

            outputs_a.append(_p2t(_render(
                image_a, 画布尺寸, *geometry_a, 背景模式, bg_color
            )))
            outputs_b.append(_p2t(_render(
                image_b, 画布尺寸, *geometry_b, 背景模式, bg_color
            )))

        return torch.stack(outputs_a, dim=0), torch.stack(outputs_b, dim=0)


NODE_CLASS_MAPPINGS = {"CherryAlignReference": CherryAlignReference}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryAlignReference": "Cherry - 双图基准匹配",
}
