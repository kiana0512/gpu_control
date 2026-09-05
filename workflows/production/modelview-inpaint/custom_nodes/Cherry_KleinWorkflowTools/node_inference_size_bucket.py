"""Orientation-aware inference size selection for Klein workflows."""

from __future__ import annotations


def choose_inference_bucket(
    width: int,
    height: int,
    threshold: float = 1.2,
    square_size: int = 1024,
    long_size: int = 1536,
) -> tuple[int, int, str]:
    """Return the processing width, height, and orientation bucket."""
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")

    if width / height >= float(threshold):
        return int(long_size), int(square_size), "landscape"
    if height / width >= float(threshold):
        return int(square_size), int(long_size), "portrait"
    return int(square_size), int(square_size), "square"


class CherryInferenceSizeBucket:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "aspect_threshold": (
                    "FLOAT",
                    {"default": 1.20, "min": 1.01, "max": 3.0, "step": 0.01},
                ),
                "square_size": (
                    "INT",
                    {"default": 1024, "min": 64, "max": 4096, "step": 8},
                ),
                "long_size": (
                    "INT",
                    {"default": 1536, "min": 64, "max": 4096, "step": 8},
                ),
            }
        }

    RETURN_TYPES = ("INT", "INT", "INT", "INT", "STRING")
    RETURN_NAMES = (
        "original_width",
        "original_height",
        "process_width",
        "process_height",
        "bucket_name",
    )
    FUNCTION = "bucket"
    CATEGORY = "Cherry_lizi/Klein工作流"

    def bucket(self, image, aspect_threshold, square_size, long_size):
        original_height = int(image.shape[1])
        original_width = int(image.shape[2])
        process_width, process_height, name = choose_inference_bucket(
            original_width,
            original_height,
            aspect_threshold,
            square_size,
            long_size,
        )
        return (
            original_width,
            original_height,
            process_width,
            process_height,
            name,
        )


NODE_CLASS_MAPPINGS = {"CherryInferenceSizeBucket": CherryInferenceSizeBucket}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryInferenceSizeBucket": "Cherry - 图像推理尺寸分桶"
}
