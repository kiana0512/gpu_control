from apps.scheduler.src.gpu_control_scheduler.main import (
    autodl_input_cache_namespace,
    autodl_runtime_gpu_identity,
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


def test_autodl_input_cache_namespaces_are_stable_and_tenant_isolated() -> None:
    first = autodl_input_cache_namespace(
        tenant_id="tenant-a",
        workflow_key=MODELVIEW_INPAINT_WORKFLOW_KEY,
        is_autodl_node=True,
    )

    assert first == autodl_input_cache_namespace(
        tenant_id="tenant-a",
        workflow_key=MODELVIEW_INPAINT_WORKFLOW_KEY,
        is_autodl_node=True,
    )
    assert first != autodl_input_cache_namespace(
        tenant_id="tenant-b",
        workflow_key=MODELVIEW_INPAINT_WORKFLOW_KEY,
        is_autodl_node=True,
    )
    assert first is not None
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")


def test_autodl_input_cache_is_disabled_for_other_workflows_and_local_nodes() -> None:
    assert (
        autodl_input_cache_namespace(
            tenant_id="tenant-a",
            workflow_key="modelview-single-view",
            is_autodl_node=True,
        )
        is None
    )
    assert (
        autodl_input_cache_namespace(
            tenant_id="tenant-a",
            workflow_key=MODELVIEW_INPAINT_WORKFLOW_KEY,
            is_autodl_node=False,
        )
        is None
    )


def test_autodl_runtime_gpu_identity_tracks_replaced_hardware() -> None:
    assert autodl_runtime_gpu_identity(
        {"provider": "autodl", "gpu_model": "RTX 5090"},
        {
            "devices": [
                {
                    "name": "cuda:0  NVIDIA RTX PRO 6000 Blackwell Server Edition : cudaMallocAsync"
                }
            ]
        },
    ) == (
        "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        "AutoDL RTX PRO 6000 Blackwell Server Edition",
    )


def test_runtime_gpu_identity_never_relabels_local_or_invalid_nodes() -> None:
    assert (
        autodl_runtime_gpu_identity(
            {"provider": "local"},
            {"devices": [{"name": "NVIDIA RTX 4090"}]},
        )
        is None
    )
    assert autodl_runtime_gpu_identity({"provider": "autodl"}, {"devices": []}) is None
