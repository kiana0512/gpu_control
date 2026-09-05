from apps.scheduler.src.gpu_control_scheduler.main import (
    uses_comfy_mask_upload_endpoint,
)
from packages.gpu_control_core.scheduling import (
    MODELVIEW_INPAINT_WORKFLOW_KEY,
    MODELVIEW_SINGLE_VIEW_INPAINT_WORKFLOW_KEY,
)


def test_generic_inpaint_mask_uses_comfy_editor_mask_endpoint() -> None:
    assert uses_comfy_mask_upload_endpoint("generic-inpaint", "mask-input.png") is True


def test_modelview_loadimage_mask_uses_regular_image_endpoint() -> None:
    for workflow_key in (
        MODELVIEW_INPAINT_WORKFLOW_KEY,
        MODELVIEW_SINGLE_VIEW_INPAINT_WORKFLOW_KEY,
    ):
        assert (
            uses_comfy_mask_upload_endpoint(
                workflow_key,
                "mask-input.png",
            )
            is False
        )


def test_normal_images_never_use_comfy_editor_mask_endpoint() -> None:
    assert uses_comfy_mask_upload_endpoint("generic-inpaint", "image-input.png") is False
    assert (
        uses_comfy_mask_upload_endpoint(
            MODELVIEW_INPAINT_WORKFLOW_KEY,
            "material-input.png",
        )
        is False
    )
