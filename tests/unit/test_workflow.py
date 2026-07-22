import pytest

from packages.gpu_control_core.workflow import (
    WorkflowError,
    WorkflowManifest,
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
