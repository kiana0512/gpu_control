import copy

import pytest

from packages.gpu_control_core.workflow import node_compatibility_reasons


def compatibility(**changes):
    labels = {
        "gpu_uuid": "GPU-5070-verified",
        "comfy_class_types": ["LoadImage", "SaveImage"],
        "validated_vram_profiles": {
            "modelview-inpaint": {
                "status": "PASSED",
                "node_id": "worker-5070ti-01",
                "version": "approved-v1",
                "template_sha256": "a" * 64,
                "gpu_uuid": "GPU-5070-verified",
                "min_vram_mb": 16000,
                "evidence_sha256": "b" * 64,
                "runtime_profile_sha256": "c" * 64,
            }
        },
    }
    args = {
        "min_vram_mb": 24000,
        "required_labels": {},
        "allowed_class_types": ["LoadImage", "SaveImage"],
        "total_vram_mb": 16303,
        "reported_labels": labels,
        "node_id": "worker-5070ti-01",
        "workflow_key": "modelview-inpaint",
        "workflow_version": "approved-v1",
        "template_sha256": "a" * 64,
    }
    args.update(changes)
    return args


def test_exact_accepted_node_and_template_can_use_verified_capacity():
    args = compatibility()
    before = copy.deepcopy(args)
    assert node_compatibility_reasons(**args) == []
    assert args == before


@pytest.mark.parametrize("field,value", [
    ("node_id", "worker-4070ti-animation-host-01"),
    ("workflow_key", "modelview-single-view"),
    ("workflow_version", "unverified-v2"),
    ("template_sha256", "d" * 64),
    ("node_id", None),
])
def test_acceptance_cannot_transfer_between_nodes_or_workflow_revisions(field, value):
    assert node_compatibility_reasons(**compatibility(**{field: value})) == [
        "vram 16303MB < required 24000MB"
    ]


@pytest.mark.parametrize("field,value", [
    ("status", "PENDING"),
    ("gpu_uuid", "GPU-other-device"),
    ("min_vram_mb", 0),
    ("min_vram_mb", True),
    ("min_vram_mb", "16000"),
    ("min_vram_mb", 24001),
    ("evidence_sha256", "missing-proof"),
    ("runtime_profile_sha256", None),
])
def test_invalid_acceptance_falls_back_to_declared_requirement(field, value):
    args = compatibility()
    args["reported_labels"]["validated_vram_profiles"]["modelview-inpaint"][field] = value
    assert node_compatibility_reasons(**args) == ["vram 16303MB < required 24000MB"]


def test_profile_does_not_bypass_labels_or_class_inventory():
    args = compatibility(required_labels={"pipeline_sha256": "required"})
    args["reported_labels"]["comfy_class_types"] = ["LoadImage"]
    assert node_compatibility_reasons(**args) == [
        "label pipeline_sha256 must equal required",
        "missing ComfyUI classes: SaveImage",
    ]


def test_verified_minimum_still_enforced_and_unprofiled_nodes_unchanged():
    assert node_compatibility_reasons(**compatibility(total_vram_mb=12282)) == [
        "vram 12282MB < required 16000MB"
    ]
    args = compatibility()
    del args["reported_labels"]["validated_vram_profiles"]
    assert node_compatibility_reasons(**args) == ["vram 16303MB < required 24000MB"]
    args["total_vram_mb"] = 24576
    assert node_compatibility_reasons(**args) == []
