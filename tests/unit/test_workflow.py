import pytest

from packages.gpu_control_core.workflow import (
    WorkflowError,
    WorkflowManifest,
    node_compatibility_reasons,
    render_workflow,
    validate_api_workflow,
)


@pytest.fixture
def manifest() -> WorkflowManifest:
    return WorkflowManifest(
        workflow_key="fake",
        version="1",
        template_file="fake.json",
        parameter_schema={
            "type": "object",
            "properties": {"steps": {"type": "integer", "minimum": 1, "maximum": 100}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        bindings={"steps": "3.inputs.steps"},
        allowed_class_types=frozenset({"KSampler", "SaveImage"}),
        required_models=(),
        required_custom_nodes=(),
        min_vram_mb=0,
        timeout_seconds=60,
        node_labels={},
        output_nodes=("9",),
        enabled=True,
    )


def test_safe_binding_only_changes_declared_input(manifest: WorkflowManifest) -> None:
    template = {
        "3": {"class_type": "KSampler", "inputs": {"steps": 20}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "safe"}},
    }
    rendered = render_workflow(manifest, template, {"steps": 42})
    assert rendered["3"]["inputs"]["steps"] == 42
    assert template["3"]["inputs"]["steps"] == 20


def test_modelview_prompt_is_overwritten_per_request_without_cross_job_leakage() -> None:
    modelview = WorkflowManifest(
        workflow_key="modelview-inpaint",
        version="test",
        template_file="template.api.json",
        parameter_schema={
            "type": "object",
            "properties": {
                "image_filename": {"type": "string"},
                "prompt": {"type": "string", "maxLength": 4096},
            },
            "required": ["image_filename"],
            "additionalProperties": False,
        },
        bindings={
            "image_filename": "127.inputs.image",
            "prompt": "94.inputs.prompt",
        },
        allowed_class_types=frozenset({"LoadImage", "Qwen3 VL Plus", "SaveImage"}),
        required_models=(),
        required_custom_nodes=(),
        min_vram_mb=0,
        timeout_seconds=60,
        node_labels={},
        output_nodes=("9",),
        enabled=True,
    )
    template = {
        "127": {"class_type": "LoadImage", "inputs": {"image": "template.png"}},
        "94": {"class_type": "Qwen3 VL Plus", "inputs": {"prompt": ""}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["94", 0]}},
    }

    first = render_workflow(
        modelview,
        template,
        {"image_filename": "job-a/input.png", "prompt": "repair the left edge"},
    )
    second = render_workflow(
        modelview,
        template,
        {"image_filename": "job-b/input.png", "prompt": "restore the wooden texture"},
    )
    automatic = render_workflow(
        modelview,
        template,
        {"image_filename": "job-c/input.png"},
    )

    assert first["94"]["inputs"]["prompt"] == "repair the left edge"
    assert second["94"]["inputs"]["prompt"] == "restore the wooden texture"
    assert automatic["94"]["inputs"]["prompt"] == ""
    assert template["94"]["inputs"]["prompt"] == ""
    assert first["127"]["inputs"]["image"] == "job-a/input.png"
    assert second["127"]["inputs"]["image"] == "job-b/input.png"


def test_rejects_ui_workflow(manifest: WorkflowManifest) -> None:
    with pytest.raises(WorkflowError, match="Export Workflow"):
        validate_api_workflow({"nodes": [], "links": []}, manifest.allowed_class_types)


def test_rejects_unknown_parameter(manifest: WorkflowManifest) -> None:
    template = {"3": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    with pytest.raises(WorkflowError):
        render_workflow(manifest, template, {"steps": 20, "server_path": "../../etc/passwd"})


def test_rejects_binding_path_traversal(manifest: WorkflowManifest) -> None:
    unsafe = WorkflowManifest(**{**manifest.__dict__, "bindings": {"steps": "3.inputs.__class__"}})
    with pytest.raises(WorkflowError, match="unsafe binding"):
        render_workflow(
            unsafe, {"3": {"class_type": "KSampler", "inputs": {"steps": 1}}}, {"steps": 2}
        )


def test_node_compatibility_fails_closed_without_class_inventory() -> None:
    reasons = node_compatibility_reasons(
        min_vram_mb=22000,
        required_labels={"pipeline": "expected"},
        allowed_class_types={"LoadImage", "SaveImage"},
        total_vram_mb=24576,
        reported_labels={"pipeline": "expected"},
    )
    assert reasons == ["ComfyUI class inventory unavailable"]


def test_node_compatibility_reports_missing_classes_and_labels() -> None:
    reasons = node_compatibility_reasons(
        min_vram_mb=22000,
        required_labels={"pipeline": "expected"},
        allowed_class_types={"LoadImage", "SaveImage"},
        total_vram_mb=20000,
        reported_labels={
            "pipeline": "old",
            "comfy_class_types": ["LoadImage"],
        },
    )
    assert reasons == [
        "vram 20000MB < required 22000MB",
        "label pipeline must equal expected",
        "missing ComfyUI classes: SaveImage",
    ]
