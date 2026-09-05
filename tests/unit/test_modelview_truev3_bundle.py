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

    assert manifest.version == (
        "2026.08.29-cba4414-truev3-gguf-mask-4input-rseed-steps2-r1"
    )
    assert manifest.bindings == {
        "image_filename": "4.inputs.image",
        "material_image_filename": "5.inputs.image",
        "mask_filename": "44.inputs.image",
        "noise_seed": "14.inputs.noise_seed",
        "prompt": "60.inputs.text",
    }
    assert manifest.min_vram_mb == 24000
    assert manifest.output_nodes == ("29",)
    assert len(template) == 28
    assert set(template) == {
        "1",
        "2",
        "3",
        "4",
        "5",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "14",
        "15",
        "16",
        "17",
        "18",
        "21",
        "22",
        "23",
        "24",
        "25",
        "29",
        "33",
        "43",
        "44",
        "45",
        "52",
        "60",
    }
    assert "20" not in template
    assert not any(node["class_type"] == "PreviewImage" for node in template.values())
    assert template["29"]["class_type"] == "SaveImage"
    assert template["29"]["inputs"]["images"] == ["33", 1]
    assert template["14"]["inputs"]["noise_seed"] == 468546072632498
    assert template["1"]["class_type"] == "UnetLoaderGGUF"
    assert template["1"]["inputs"] == {
        "unet_name": "Flux2-Klein-9B-True-V3-Q5_K.gguf",
    }
    assert template["2"]["inputs"] == {
        "clip_name": "qwen_3_8b_fp8mixed.safetensors",
        "device": "default",
        "type": "flux2",
    }
    assert template["3"]["inputs"] == {"vae_name": "flux2-vae.safetensors"}
    assert template["21"]["inputs"]["lora_name"] == (
        "flux-kelin/baimo_shangcaizhi_klein_v1_000005500.safetensors"
    )
    assert template["21"]["inputs"]["strength_model"] == 0.9
    assert template["15"]["inputs"] | {"model": None} == {
        "denoise": 1,
        "model": None,
        "scheduler": "simple",
        "steps": 2,
    }
    assert template["16"]["inputs"] == {"sampler_name": "euler"}
    assert template["22"]["inputs"] | {"image": None} == {
        "aspect_threshold": 1.2,
        "image": None,
        "long_size": 1528,
        "square_size": 1024,
    }
    assert template["4"]["inputs"]["image"] == (
        "509caff28e32103999d6a06a5a06b1a1907751ee58706edd1731a95eabdff15d.png"
    )
    assert template["5"]["inputs"]["image"] == (
        "b4c6465b6bd7dd0d455adc09419b385672b34d42fa212898015849730fab4628.png"
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
    assert template["33"]["inputs"]["图像A"] == ["4", 0]
    assert template["33"]["inputs"]["图像B"] == ["25", 0]
    assert template["43"]["inputs"] == {
        "mask": ["45", 0],
        "samples": ["7", 0],
    }
    assert template["44"]["class_type"] == "LoadImage"
    assert template["45"]["inputs"] == {
        "channel": "red",
        "image": ["52", 0],
    }
    assert template["52"]["inputs"]["image"] == ["44", 0]
    assert template["60"] == {
        "_meta": {"title": "提示词"},
        "class_type": "ttN text",
        "inputs": {"text": ""},
    }
    assert template["9"]["inputs"]["text"] == ["60", 0]
    assert template["17"]["inputs"]["latent_image"] == ["43", 0]
    assert {node["class_type"] for node in template.values()} == manifest.allowed_class_types


def test_truev3_binds_three_public_images_server_seed_and_one_final_output() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    template = json.loads((BUNDLE / "template.api.json").read_text(encoding="utf-8"))
    automatic = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job-a/white-model.png",
            "material_image_filename": "job-a/six-view.png",
            "mask_filename": "job-a/mask.png",
            "noise_seed": 101,
        },
    )
    overridden = render_workflow(
        manifest,
        template,
        {
            "image_filename": "job-b/white-model.png",
            "material_image_filename": "job-b/six-view.png",
            "mask_filename": "job-b/mask.png",
            "noise_seed": 202,
            "prompt": "preserve geometry and repair only the selected material",
        },
    )

    assert automatic["4"]["inputs"]["image"] == "job-a/white-model.png"
    assert automatic["5"]["inputs"]["image"] == "job-a/six-view.png"
    assert automatic["44"]["inputs"]["image"] == "job-a/mask.png"
    assert automatic["14"]["inputs"]["noise_seed"] == 101
    assert automatic["60"]["inputs"]["text"] == ""
    assert overridden["4"]["inputs"]["image"] == "job-b/white-model.png"
    assert overridden["5"]["inputs"]["image"] == "job-b/six-view.png"
    assert overridden["44"]["inputs"]["image"] == "job-b/mask.png"
    assert overridden["14"]["inputs"]["noise_seed"] == 202
    assert overridden["60"]["inputs"]["text"] == (
        "preserve geometry and repair only the selected material"
    )
    assert template["4"]["inputs"]["image"] == (
        "509caff28e32103999d6a06a5a06b1a1907751ee58706edd1731a95eabdff15d.png"
    )
    assert template["14"]["inputs"]["noise_seed"] == 468546072632498
    assert template["60"]["inputs"]["text"] == ""


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


def test_required_official_plugin_revision_is_immutable_and_matches_the_workflow() -> None:
    manifest = WorkflowManifest.load(BUNDLE / "manifest.yaml")
    lock = yaml.safe_load(
        (ROOT / "docker" / "comfyui" / "custom_nodes.lock.yaml").read_text(encoding="utf-8")
    )
    revisions = {item["name"]: item["commit"] for item in lock["custom_nodes"]}

    assert revisions["ComfyUI_essentials"] == ("9d9f4bedfc9f0321c19faf71855e228c93bd0dc9")
    assert revisions["ComfyUI-GGUF"] == ("6ea2651e7df66d7585f6ffee804b20e92fb38b8a")
    assert revisions["ComfyUI_tinyterraNodes"] == (
        "97096a39847b74e15886a16049ae89ad918e1aea"
    )
    assert revisions["WAS-Node-Suite"] == ("afeee09ba44e713ec52a413ac6b105fd06b2d356")
    assert not any(item.startswith("ComfyUI-Easy-Use@") for item in manifest.required_custom_nodes)


def test_truev3_models_and_visible_workflow_mount_are_pinned() -> None:
    model_manifest = yaml.safe_load(
        (ROOT / "configs" / "modelviewcreator.models.manifest.yaml").read_text(encoding="utf-8")
    )
    models = {item["path"]: item for item in model_manifest["models"]}
    assert models["unet/Flux2-Klein-9B-True-V3-Q5_K.gguf"] == {
        "path": "unet/Flux2-Klein-9B-True-V3-Q5_K.gguf",
        "size_bytes": 6813702528,
        "sha256": "8167105716c715be31018f682916b5b9988f9afec4e20d7ad7cd9b42aa9774ce",
    }
    assert models["lora/baimo_shangcaizhi_klein_v1_000005500.safetensors"] == {
        "path": "lora/baimo_shangcaizhi_klein_v1_000005500.safetensors",
        "size_bytes": 165704408,
        "sha256": "5352ada24a83b36e7bf8b3004eae5f6b1676479f93e0d002c9f521d133804fb9",
    }

    source_name = "Flux2 Klein TrueV3-双图材质编辑-局部重绘.json"
    for compose_path in (
        ROOT / "deploy" / "control-plane" / "compose.yaml",
        ROOT / "deploy" / "gpu-node" / "compose.yaml",
    ):
        compose = compose_path.read_text(encoding="utf-8")
        assert source_name in compose
        assert "/flux_fill_inpaint.json:/opt/comfyui/user/default/workflows/" not in compose

    sync_script = (ROOT / "scripts" / "sync_models.sh").read_text(encoding="utf-8")
    assert "/opt/modelviewcreator/model/lora/flux-kelin" in sync_script
    assert "ln -sfn ../baimo_shangcaizhi_klein_v1_000005500.safetensors" in sync_script
