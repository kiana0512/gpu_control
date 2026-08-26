import json
from pathlib import Path

from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

ROOT = Path(__file__).parents[2]
BUNDLE = ROOT / "workflows" / "production" / "modelview-single-view"


def test_single_view_bundle_matches_the_approved_four_step_contract() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    assert manifest.workflow_key == "modelview-single-view"
    assert manifest.version == "2026.08.26-c0e6218-single-view-4step-r1"
    assert manifest.bindings == {
        "image_filename": "4.inputs.image",
        "material_image_filename": "5.inputs.image",
        "noise_seed": "14.inputs.noise_seed",
        "prompt": "41.inputs.text",
    }
    assert manifest.min_vram_mb == 24000
    assert manifest.output_nodes == ("29",)
    assert len(template) == 27
    assert template["1"]["class_type"] == "UnetLoaderGGUF"
    assert template["15"]["inputs"]["steps"] == 4
    assert template["29"]["class_type"] == "SaveImage"
    assert template["29"]["inputs"] == {
        "filename_prefix": "modelview-single-view",
        "images": ["33", 1],
    }
    assert not any(node["class_type"] == "PreviewImage" for node in template.values())
    assert {node["class_type"] for node in template.values()} == manifest.allowed_class_types


def test_single_view_renders_two_images_prompt_and_server_seed() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    rendered = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job/white-model.png",
            "material_image_filename": "job/reference.png",
            "noise_seed": 20260826,
            "prompt": "保留白模结构，生成目标单视图材质",
        },
    )

    assert rendered["4"]["inputs"]["image"] == "job/white-model.png"
    assert rendered["5"]["inputs"]["image"] == "job/reference.png"
    assert rendered["14"]["inputs"]["noise_seed"] == 20260826
    assert rendered["41"]["inputs"]["text"] == "保留白模结构，生成目标单视图材质"
    assert template["41"]["inputs"]["text"] == ""
