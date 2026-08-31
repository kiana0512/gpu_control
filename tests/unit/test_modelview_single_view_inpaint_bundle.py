import hashlib
import json
from pathlib import Path

from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

ROOT = Path(__file__).parents[2]
BUNDLE = ROOT / "workflows" / "production" / "modelview-single-view-inpaint"
SOURCE = Path(
    "/home/lilithgames/下载/ModelViewCreator_flux_fill_inpaint -View generation.json"
)
FIXED_PROMPT_SHA256 = "3c0118ac184e923ba56d3172f8d928025bea8a0c6f1a90b535219239ef500a70"


def test_single_view_inpaint_bundle_matches_approved_four_input_contract() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    assert manifest.workflow_key == "modelview-single-view-inpaint"
    assert manifest.version == (
        "2026.08.31-e39ed5f-single-view-inpaint-4input-rseed-steps2-r1"
    )
    assert manifest.bindings == {
        "image_filename": "4.inputs.image",
        "material_image_filename": "5.inputs.image",
        "mask_filename": "44.inputs.image",
        "noise_seed": "14.inputs.noise_seed",
        "prompt": "61.inputs.text",
    }
    assert manifest.min_vram_mb == 24000
    assert manifest.output_nodes == ("29",)
    assert len(template) == 30
    assert template["15"]["inputs"]["steps"] == 2
    assert template["21"]["inputs"]["strength_model"] == 0.9
    assert template["17"]["inputs"]["latent_image"] == ["43", 0]
    assert template["45"]["inputs"] == {"channel": "red", "image": ["52", 0]}
    assert template["63"]["inputs"] == {
        "clean_whitespace": "true",
        "delimiter": ", ",
        "text_a": ["61", 0],
        "text_b": ["62", 0],
    }
    assert template["9"]["inputs"]["text"] == ["63", 0]
    assert template["29"]["class_type"] == "SaveImage"
    assert template["29"]["inputs"] == {
        "filename_prefix": "modelview-single-view-inpaint",
        "images": ["33", 1],
    }
    assert not any(node["class_type"] == "PreviewImage" for node in template.values())
    assert {node["class_type"] for node in template.values()} == manifest.allowed_class_types


def test_single_view_inpaint_binds_only_public_prompt_and_preserves_fixed_guard() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))
    fixed_prompt = template["62"]["inputs"]["text"]

    rendered = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job/current.png",
            "material_image_filename": "job/reference.png",
            "mask_filename": "job/mask.png",
            "noise_seed": 20260831,
            "prompt": "只重绘蒙版内的磨损金属区域",
        },
    )

    assert rendered["4"]["inputs"]["image"] == "job/current.png"
    assert rendered["5"]["inputs"]["image"] == "job/reference.png"
    assert rendered["44"]["inputs"]["image"] == "job/mask.png"
    assert rendered["14"]["inputs"]["noise_seed"] == 20260831
    assert rendered["61"]["inputs"]["text"] == "只重绘蒙版内的磨损金属区域"
    assert rendered["62"]["inputs"]["text"] == fixed_prompt
    assert hashlib.sha256(fixed_prompt.encode()).hexdigest() == FIXED_PROMPT_SHA256
    assert template["61"]["inputs"]["text"] == ""


def test_single_view_inpaint_source_hash_is_the_user_approved_workflow() -> None:
    if not SOURCE.exists():
        return
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == (
        "e39ed5f5ec3916e5b3d45415a472734d0707fcbf3ca971ee2064f0d94f056c26"
    )


def test_single_view_inpaint_visible_workflow_is_mounted_on_all_24gb_nodes() -> None:
    source_name = "ModelViewCreator_flux_fill_inpaint -View generation.json"
    for compose_path in (
        ROOT / "deploy" / "control-plane" / "compose.yaml",
        ROOT / "deploy" / "gpu-node" / "compose.yaml",
    ):
        compose = compose_path.read_text(encoding="utf-8")
        assert source_name in compose

    server_script = (ROOT / "scripts" / "comfyui-server.sh").read_text(
        encoding="utf-8"
    )
    assert source_name in server_script
