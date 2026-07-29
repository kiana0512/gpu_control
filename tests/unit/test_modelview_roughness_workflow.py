import json
from pathlib import Path

from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

BUNDLE = Path("workflows/production/modelview-roughness")
FIXED_PROMPT = (
    "Convert the input image into a grayscale roughness map. Detect each different "
    "material area and independently assign physically reasonable roughness values. "
    "Maintain precise contours, alignment, material boundaries, seams, wear, scratches, "
    "and fine surface details. Do not reproduce albedo colors, lighting, highlights, or shadows."
)


def test_roughness_contract_exposes_only_the_input_image() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")

    assert manifest.bindings == {"image_filename": "323.inputs.image"}
    assert manifest.output_nodes == ("355",)
    assert "prompt" not in manifest.parameter_schema["properties"]


def test_roughness_render_preserves_fixed_prompt_and_final_output() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    rendered = render_workflow(
        manifest,
        template,
        {"image_filename": "job-id/input/material.png"},
    )

    assert rendered["323"]["inputs"]["image"] == "job-id/input/material.png"
    assert rendered["332"]["inputs"]["prompt"] == FIXED_PROMPT
    assert rendered["355"]["class_type"] == "PreviewImage"
