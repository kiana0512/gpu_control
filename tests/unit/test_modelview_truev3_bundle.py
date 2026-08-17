import hashlib
import json
from pathlib import Path

import yaml

from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

ROOT = Path(__file__).parents[2]
BUNDLE = ROOT / "workflows" / "production" / "modelview-inpaint"
PLUGIN = BUNDLE / "custom_nodes" / "Cherry_KleinWorkflowTools"


def test_truev3_api_template_matches_the_approved_production_contract() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))

    assert manifest.version == "2026.08.17-a9dbbca-flux2-klein-truev3-3input-r2"
    assert manifest.bindings == {
        "image_filename": "4.inputs.image",
        "material_image_filename": "5.inputs.image",
        "viewport_reference_filename": "26.inputs.image",
        "prompt": "9.inputs.text",
    }
    assert manifest.min_vram_mb == 24000
    assert manifest.output_nodes == ("32",)
    assert len(template) == 27
    assert set(template) == {
        "1", "2", "3", "4", "5", "7", "8", "9", "10", "11", "12", "13",
        "14", "15", "16", "17", "18", "21", "22", "23", "24", "25", "26",
        "30", "31", "32", "33",
    }
    assert "20" not in template
    assert not any(node["class_type"] == "PreviewImage" for node in template.values())
    assert template["32"]["class_type"] == "SaveImage"
    assert template["32"]["inputs"]["images"] == ["31", 0]
    assert template["14"]["inputs"]["noise_seed"] == 293365702567203
    assert template["1"]["inputs"] == {
        "unet_name": "Flux2-Klein-9B-True-V3-int8mixedrow.safetensors",
        "weight_dtype": "default",
    }
    assert template["2"]["inputs"] == {
        "clip_name": "qwen_3_8b_fp8mixed.safetensors",
        "device": "default",
        "type": "flux2",
    }
    assert template["3"]["inputs"] == {"vae_name": "flux2-vae.safetensors"}
    assert template["21"]["inputs"]["lora_name"] == (
        "baimo_shangcaizhi_klein_v1_000005500.safetensors"
    )
    assert template["21"]["inputs"]["strength_model"] == 0.8
    assert template["15"]["inputs"] | {"model": None} == {
        "denoise": 1,
        "model": None,
        "scheduler": "simple",
        "steps": 12,
    }
    assert template["16"]["inputs"] == {"sampler_name": "euler"}
    assert template["22"]["inputs"] | {"image": None} == {
        "aspect_threshold": 1.2,
        "image": None,
        "long_size": 1536,
        "square_size": 1024,
    }
    assert template["30"]["inputs"]["method"] == "mkl"
    assert template["31"]["inputs"]["method"] == "hm-mkl-hm"
    assert template["5"]["inputs"]["image"] == (
        "c67b0fab153890a6225a371dc7a8a911bc2f4c3933b9399fc4470b19f047654e.jpg"
    )
    assert template["26"]["inputs"]["image"] == (
        "img_v3_0214l_5c6a7e7e-e76c-4a82-86c1-b8f7cfe87b4g.png"
    )
    assert template["33"]["inputs"] | {"图像A": None, "图像B": None} == {
        "图像A": None,
        "图像B": None,
        "对齐基准": "图像A",
        "画布尺寸": 2048,
        "背景模式": "自动采样",
        "背景色": "",
        "检测阈值": 18,
        "使用Alpha": True,
    }
    assert {node["class_type"] for node in template.values()} == manifest.allowed_class_types


def test_truev3_binds_three_public_images_and_one_final_output() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))
    default_prompt = template["9"]["inputs"]["text"]

    automatic = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job-a/white-model.png",
            "material_image_filename": "job-a/six-view.png",
            "viewport_reference_filename": "job-a/viewport-reference.png",
        },
    )
    overridden = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job-b/white-model.png",
            "material_image_filename": "job-b/six-view.png",
            "viewport_reference_filename": "job-b/viewport-reference.png",
            "prompt": "preserve geometry and repair only the selected material",
        },
    )

    assert automatic["4"]["inputs"]["image"] == "job-a/white-model.png"
    assert automatic["5"]["inputs"]["image"] == "job-a/six-view.png"
    assert automatic["26"]["inputs"]["image"] == "job-a/viewport-reference.png"
    assert automatic["9"]["inputs"]["text"] == default_prompt
    assert overridden["4"]["inputs"]["image"] == "job-b/white-model.png"
    assert overridden["5"]["inputs"]["image"] == "job-b/six-view.png"
    assert overridden["26"]["inputs"]["image"] == "job-b/viewport-reference.png"
    assert overridden["9"]["inputs"]["text"] == (
        "preserve geometry and repair only the selected material"
    )
    assert template["4"]["inputs"]["image"] == "11 (1).png"
    assert template["9"]["inputs"]["text"] == default_prompt


def test_bundled_cherry_sources_are_byte_exact_to_upstream_commit() -> None:
    expected = {}
    for line in (PLUGIN / "UPSTREAM.sha256").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        expected[filename] = digest

    assert set(expected) == {
        "__init__.py",
        "node_align_pair.py",
        "node_align_reference.py",
        "node_geometry_guard.py",
        "node_inference_size_bucket.py",
        "node_prompt_fusion.py",
    }
    for filename, digest in expected.items():
        assert hashlib.sha256((PLUGIN / filename).read_bytes()).hexdigest() == digest


def test_official_plugin_revisions_are_immutable_and_match_the_workflow() -> None:
    lock = yaml.safe_load(
        (ROOT / "docker" / "comfyui" / "custom_nodes.lock.yaml").read_text(
            encoding="utf-8"
        )
    )
    revisions = {item["name"]: item["commit"] for item in lock["custom_nodes"]}

    assert revisions["ComfyUI-Easy-Use"] == (
        "b5e31ef12ad9d0b187b545c2707735cc7d581c52"
    )
    assert revisions["ComfyUI_essentials"] == (
        "9d9f4bedfc9f0321c19faf71855e228c93bd0dc9"
    )


def test_truev3_models_and_visible_workflow_mount_are_pinned() -> None:
    model_manifest = yaml.safe_load(
        (ROOT / "configs" / "modelviewcreator.models.manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    models = {item["path"]: item for item in model_manifest["models"]}
    assert models["unet/Flux2-Klein-9B-True-V3-int8mixedrow.safetensors"] == {
        "path": "unet/Flux2-Klein-9B-True-V3-int8mixedrow.safetensors",
        "size_bytes": 9439884768,
        "sha256": "6d23ea6946f410a496bf706b136b17bea5e1cdd1a6ba17a1b5f23c64d30c7088",
    }
    assert models["lora/baimo_shangcaizhi_klein_v1_000005500.safetensors"] == {
        "path": "lora/baimo_shangcaizhi_klein_v1_000005500.safetensors",
        "size_bytes": 165704408,
        "sha256": "5352ada24a83b36e7bf8b3004eae5f6b1676479f93e0d002c9f521d133804fb9",
    }

    source_name = "Flux2 Klein TrueV3-双图材质编辑-精简测试.json"
    for compose_path in (
        ROOT / "deploy" / "control-plane" / "compose.yaml",
        ROOT / "deploy" / "gpu-node" / "compose.yaml",
    ):
        compose = compose_path.read_text(encoding="utf-8")
        assert source_name in compose
        assert "/flux_fill_inpaint.json:/opt/comfyui/user/default/workflows/" not in compose
