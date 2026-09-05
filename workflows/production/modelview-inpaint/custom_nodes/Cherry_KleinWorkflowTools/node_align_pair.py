# -*- coding: utf-8 -*-
"""Cherry - 双图主体对齐节点

输入两张尺寸不同的图，自动检测主体边界框，缩放并居中到统一画布，
对齐后用所选模式填充画布缺失区域，使背景自然延展。
"""
import torch
import numpy as np
from PIL import Image


def _t2p(t: torch.Tensor) -> Image.Image:
    arr = (t.clamp(0, 1).cpu().numpy() * 255.0).astype(np.uint8)
    return Image.fromarray(arr, "RGBA" if arr.shape[-1] == 4 else "RGB")


def _p2t(p: Image.Image) -> torch.Tensor:
    arr = np.asarray(p).astype(np.float32) / 255.0
    if arr.ndim == 2:
        arr = arr[..., None].repeat(3, axis=-1)
    return torch.from_numpy(arr)


def _parse_color(s: str):
    s = str(s or "").strip()
    if not s:
        return (58, 58, 58, 255)
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
        if len(s) == 8:
            return (int(s[0:2], 16), int(s[2:4], 16),
                    int(s[4:6], 16), int(s[6:8], 16))
    parts = [int(x.strip()) for x in s.split(",")]
    if len(parts) == 3:
        return (parts[0], parts[1], parts[2], 255)
    if len(parts) == 4:
        return tuple(parts)
    return (128, 128, 128, 255)


def _sample_bg(img: Image.Image):
    """四角采样平均背景色 (R,G,B,A)"""
    iw, ih = img.size
    rgba = np.asarray(img.convert("RGBA"))
    cs = max(4, min(iw, ih) // 20)
    samples = np.concatenate([
        rgba[:cs, :cs].reshape(-1, 4),
        rgba[:cs, -cs:].reshape(-1, 4),
        rgba[-cs:, :cs].reshape(-1, 4),
        rgba[-cs:, -cs:].reshape(-1, 4),
    ])
    return tuple(int(x) for x in samples.mean(axis=0))


def _detect_bbox(img: Image.Image, threshold: int, use_alpha: bool):
    iw, ih = img.size
    if use_alpha and img.mode == "RGBA":
        mask = np.asarray(img)[:, :, 3] > 20
    else:
        rgb = np.asarray(img.convert("RGB")).astype(np.float32)
        cs = max(4, min(iw, ih) // 20)
        bg = np.concatenate([
            rgb[:cs, :cs].reshape(-1, 3),
            rgb[:cs, -cs:].reshape(-1, 3),
            rgb[-cs:, :cs].reshape(-1, 3),
            rgb[-cs:, -cs:].reshape(-1, 3),
        ]).mean(axis=0)
        mask = np.abs(rgb - bg).mean(axis=2) > threshold

    if not mask.any():
        return (0, 0, iw - 1, ih - 1)
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    return (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))


def _make_canvas(canvas: int, mode: str, bg_color, img_s: Image.Image):
    """根据模式生成画布底图。img_s 是已缩放好的原图。"""
    if mode == "自定义颜色":
        return Image.new("RGBA", (canvas, canvas), bg_color)
    if mode == "自动采样":
        sampled = _sample_bg(img_s)
        return Image.new("RGBA", (canvas, canvas), sampled)
    if mode == "边缘延伸":
        return _edge_extend_to(img_s, canvas)
    return Image.new("RGBA", (canvas, canvas), bg_color)


def _edge_extend_to(img: Image.Image, size: int) -> Image.Image:
    """用 numpy 边缘像素复制（edge mode）把图扩到 size×size。"""
    img = img.convert("RGBA")
    arr = np.asarray(img)  # H,W,4
    h, w = arr.shape[:2]

    pad_left   = max(0, (size - w) // 2)
    pad_right  = max(0, size - w - pad_left)
    pad_top    = max(0, (size - h) // 2)
    pad_bottom = max(0, size - h - pad_top)

    crop_left = max(0, (w - size) // 2)
    crop_top  = max(0, (h - size) // 2)
    arr = arr[crop_top:crop_top + min(h, size),
              crop_left:crop_left + min(w, size)]

    if pad_left + pad_right + pad_top + pad_bottom > 0:
        arr = np.pad(arr,
                     ((pad_top, pad_bottom), (pad_left, pad_right), (0, 0)),
                     mode="edge")
    return Image.fromarray(arr, "RGBA")


def _align(img: Image.Image, bbox, canvas: int, target_long: int,
           bg_mode: str, bg_color):
    cmin, rmin, cmax, rmax = bbox
    long_side = max(cmax - cmin + 1, rmax - rmin + 1, 1)
    scale = target_long / long_side

    new_w = max(1, round(img.size[0] * scale))
    new_h = max(1, round(img.size[1] * scale))
    img_s = img.resize((new_w, new_h), Image.LANCZOS).convert("RGBA")

    out = _make_canvas(canvas, bg_mode, bg_color, img_s)

    cx = (cmin + cmax) / 2.0 * scale
    cy = (rmin + rmax) / 2.0 * scale
    out.alpha_composite(img_s, (round(canvas / 2 - cx), round(canvas / 2 - cy)))
    return out


class CherryAlignPair:
    """Cherry - 双图主体对齐"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像A":     ("IMAGE",),
                "图像B":     ("IMAGE",),
                "画布尺寸":   ("INT",   {"default": 1024, "min": 64, "max": 4096, "step": 64,
                                         "tooltip": "输出画布的宽高（正方形）"}),
                "背景模式":   (["自动采样", "自定义颜色", "边缘延伸"],
                              {"default": "自动采样",
                               "tooltip": "自动采样=取原图角落色；自定义=用下面背景色；边缘延伸=镜像/复制原图边缘像素铺满"}),
                "背景色":    ("STRING", {"default": "#3a3a3a",
                                         "tooltip": "仅在 背景模式=自定义颜色 时生效。支持 #RRGGBB / #RRGGBBAA / r,g,b"}),
                "主体占比":   ("FLOAT", {"default": 0.78, "min": 0.1, "max": 1.0, "step": 0.01,
                                         "tooltip": "主体长边占画布的比例（控制留白）"}),
                "检测阈值":   ("INT",   {"default": 18, "min": 1, "max": 100, "step": 1,
                                         "tooltip": "背景与主体的差异阈值，越小越敏感"}),
                "使用Alpha": ("BOOLEAN", {"default": True,
                                          "tooltip": "若图带 alpha 通道，优先用 alpha 检测主体"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("图像A_对齐", "图像B_对齐")
    FUNCTION     = "align"
    CATEGORY     = "cherry_"

    def align(self, 图像A, 图像B, 画布尺寸, 背景模式, 背景色,
              主体占比, 检测阈值, 使用Alpha):
        bg_color = _parse_color(背景色)
        target_long = int(round(画布尺寸 * 主体占比))

        outs_a, outs_b = [], []
        n = min(图像A.shape[0], 图像B.shape[0])
        for i in range(n):
            a = _t2p(图像A[i])
            b = _t2p(图像B[i])
            ba = _detect_bbox(a, 检测阈值, 使用Alpha)
            bb = _detect_bbox(b, 检测阈值, 使用Alpha)
            outs_a.append(_p2t(_align(a, ba, 画布尺寸, target_long, 背景模式, bg_color).convert("RGB")))
            outs_b.append(_p2t(_align(b, bb, 画布尺寸, target_long, 背景模式, bg_color).convert("RGB")))

        return (torch.stack(outs_a, dim=0), torch.stack(outs_b, dim=0))


NODE_CLASS_MAPPINGS = {
    "CherryAlignPair": CherryAlignPair,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryAlignPair": "Cherry - 双图主体对齐",
}
