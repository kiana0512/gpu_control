import json
from pathlib import Path

from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

ROOT = Path(__file__).parents[2]
BUNDLE = ROOT / "workflows" / "production" / "modelview-single-view"


def test_single_view_bundle_matches_the_approved_two_step_contract() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    assert manifest.workflow_key == "modelview-single-view"
    assert manifest.version == "2026.09.20-refcontrol-normal-single-view-2step-r1"
    assert manifest.bindings == {
        "image_filename": "4.inputs.image",
        "material_image_filename": "5.inputs.image",
        "normal_image_filename": "42.inputs.image",
        "noise_seed": "14.inputs.noise_seed",
        "prompt": "41.inputs.text",
    }
    assert manifest.min_vram_mb == 24000
    assert manifest.output_nodes == ("29",)
    assert len(template) == 31
    assert template["44"]["inputs"] == {"pixels": ["42", 0], "vae": ["3", 0]}
    assert template["45"]["inputs"] == {"conditioning": ["11", 0], "latent": ["44", 0]}
    assert template["43"]["inputs"] == {"model": ["21", 0], "lora_name": "flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors", "strength_model": 0.8}
    assert template["12"]["inputs"] == {"model": ["43", 0], "conditioning": ["45", 0]}
    assert template["15"]["inputs"]["model"] == ["43", 0]
    assert template["17"]["inputs"]["latent_image"] == ["13", 0]
    assert "normal_image_filename" in manifest.parameter_schema["required"]
    assert template["1"]["class_type"] == "UnetLoaderGGUF"
    assert template["15"]["inputs"]["steps"] == 2
    assert template["29"]["class_type"] == "SaveImage"
    assert template["29"]["inputs"] == {
        "filename_prefix": "modelview-single-view",
        "images": ["33", 1],
    }
    assert not any(node["class_type"] == "PreviewImage" for node in template.values())
    assert {node["class_type"] for node in template.values()} == manifest.allowed_class_types


def test_single_view_renders_three_images_prompt_and_server_seed() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    rendered = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job/white-model.png",
            "material_image_filename": "job/reference.png",
            "normal_image_filename": "job/normal.png",
            "noise_seed": 20260826,
            "prompt": "保留白模结构，生成目标单视图材质",
        },
    )

    assert rendered["4"]["inputs"]["image"] == "job/white-model.png"
    assert rendered["5"]["inputs"]["image"] == "job/reference.png"
    assert rendered["42"]["inputs"]["image"] == "job/normal.png"
    assert rendered["14"]["inputs"]["noise_seed"] == 20260826
    assert rendered["41"]["inputs"]["text"] == "保留白模结构，生成目标单视图材质"
    assert template["41"]["inputs"]["text"] == ""
