from scripts.build_imageclip_api_workflow import build_prompt, control_after_generate


def test_control_after_generate_accepts_boolean_and_fixed_modes() -> None:
    assert control_after_generate(
        {"required": {"seed": ["INT", {"control_after_generate": True}]}}, "seed"
    )
    assert control_after_generate(
        {"required": {"value": ["INT", {"control_after_generate": "fixed"}]}},
        "value",
    )


def test_builder_skips_ui_control_after_generate_value() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "Source", "mode": 0, "inputs": []},
            {
                "id": 2,
                "type": "KSampler",
                "mode": 0,
                "inputs": [
                    {"name": "model", "link": 1},
                    {"name": "positive", "link": 2},
                    {"name": "negative", "link": 3},
                    {"name": "latent_image", "link": 4},
                    {"name": "seed", "link": None, "widget": {"name": "seed"}},
                    {"name": "steps", "link": 5, "widget": {"name": "steps"}},
                    {"name": "cfg", "link": None, "widget": {"name": "cfg"}},
                    {
                        "name": "sampler_name",
                        "link": None,
                        "widget": {"name": "sampler_name"},
                    },
                    {"name": "scheduler", "link": None, "widget": {"name": "scheduler"}},
                    {"name": "denoise", "link": None, "widget": {"name": "denoise"}},
                ],
                "widgets_values": [123, "fixed", 8, 1, "euler", "simple", 1],
            },
            {
                "id": 3,
                "type": "SaveImage",
                "mode": 0,
                "inputs": [
                    {"name": "images", "link": 6},
                    {
                        "name": "filename_prefix",
                        "link": None,
                        "widget": {"name": "filename_prefix"},
                    },
                ],
                "widgets_values": ["final"],
            },
        ],
        "links": [
            [1, 1, 0, 2, 0],
            [2, 1, 0, 2, 1],
            [3, 1, 0, 2, 2],
            [4, 1, 0, 2, 3],
            [5, 1, 0, 2, 5],
            [6, 2, 0, 3, 0],
        ],
    }
    ksampler_required = {
        "model": ["MODEL", {}],
        "seed": ["INT", {"control_after_generate": True}],
        "steps": ["INT", {}],
        "cfg": ["FLOAT", {}],
        "sampler_name": [["euler"], {}],
        "scheduler": [["simple"], {}],
        "positive": ["CONDITIONING", {}],
        "negative": ["CONDITIONING", {}],
        "latent_image": ["LATENT", {}],
        "denoise": ["FLOAT", {}],
    }
    definitions = {
        "Source": {"input": {"required": {}}},
        "KSampler": {"input": {"required": ksampler_required}},
        "SaveImage": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "filename_prefix": ["STRING", {}],
                }
            },
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 3)

    assert prompt["2"]["inputs"] == {
        "model": ["1", 0],
        "positive": ["1", 0],
        "negative": ["1", 0],
        "latent_image": ["1", 0],
        "seed": 123,
        "steps": ["1", 0],
        "cfg": 1,
        "sampler_name": "euler",
        "scheduler": "simple",
        "denoise": 1,
    }


def test_builder_inlines_ui_primitive_without_emitting_a_fake_api_node() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "Source", "mode": 0, "inputs": []},
            {
                "id": 2,
                "type": "PrimitiveNode",
                "mode": 0,
                "inputs": [],
                "widgets_values": ["用户指定的局部重绘提示词"],
            },
            {
                "id": 3,
                "type": "Processor",
                "mode": 0,
                "inputs": [
                    {"name": "image", "link": 1},
                    {"name": "prompt", "link": 2, "widget": {"name": "prompt"}},
                ],
                "widgets_values": ["UI fallback"],
            },
            {
                "id": 4,
                "type": "SaveImage",
                "mode": 0,
                "inputs": [
                    {"name": "images", "link": 3},
                    {
                        "name": "filename_prefix",
                        "link": None,
                        "widget": {"name": "filename_prefix"},
                    },
                ],
                "widgets_values": ["final"],
            },
        ],
        "links": [
            [1, 1, 0, 3, 0],
            [2, 2, 0, 3, 1],
            [3, 3, 0, 4, 0],
        ],
    }
    definitions = {
        "Source": {"input": {"required": {}}},
        "Processor": {
            "input": {
                "required": {
                    "image": ["IMAGE", {}],
                    "prompt": ["STRING", {}],
                }
            }
        },
        "SaveImage": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "filename_prefix": ["STRING", {}],
                }
            },
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 4)

    assert "2" not in prompt
    assert prompt["3"]["inputs"]["prompt"] == "用户指定的局部重绘提示词"


def test_builder_skips_serialized_seed_control_when_object_info_omits_flag() -> None:
    workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "Processor",
                "mode": 0,
                "inputs": [
                    {"name": "seed", "link": None, "widget": {"name": "seed"}},
                ],
                "widgets_values": [736972101620544, "randomize"],
            },
            {
                "id": 9,
                "type": "SaveImage",
                "mode": 0,
                "inputs": [
                    {"name": "images", "link": 1},
                    {
                        "name": "filename_prefix",
                        "link": None,
                        "widget": {"name": "filename_prefix"},
                    },
                ],
                "widgets_values": ["final"],
            },
        ],
        "links": [[1, 1, 0, 9, 0]],
    }
    definitions = {
        "Processor": {
            "input": {
                "required": {
                    "seed": ["INT", {"default": 0, "min": 0, "max": 2**64 - 1}],
                }
            }
        },
        "SaveImage": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "filename_prefix": ["STRING", {"default": "ComfyUI"}],
                }
            },
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 9)

    assert prompt["1"]["inputs"]["seed"] == 736972101620544


def test_builder_preserves_autogrow_nested_link_names() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "Source", "mode": 0, "inputs": []},
            {"id": 2, "type": "Source", "mode": 0, "inputs": []},
            {
                "id": 3,
                "type": "BatchImagesNode",
                "mode": 0,
                "inputs": [
                    {"name": "images.image0", "link": 1},
                    {"name": "images.image1", "link": 2},
                ],
                "widgets_values": [],
            },
            {
                "id": 9,
                "type": "SaveImage",
                "mode": 0,
                "inputs": [
                    {"name": "images", "link": 3},
                    {
                        "name": "filename_prefix",
                        "link": None,
                        "widget": {"name": "filename_prefix"},
                    },
                ],
                "widgets_values": ["final"],
            },
        ],
        "links": [
            [1, 1, 0, 3, 0],
            [2, 2, 0, 3, 1],
            [3, 3, 0, 9, 0],
        ],
    }
    definitions = {
        "Source": {"input": {"required": {}}},
        "BatchImagesNode": {
            "input": {
                "required": {
                    "images": [
                        "COMFY_AUTOGROW_V3",
                        {"template": {"prefix": "image", "min": 1, "max": 50}},
                    ]
                }
            }
        },
        "SaveImage": {
            "input": {
                "required": {
                    "images": ["IMAGE", {}],
                    "filename_prefix": ["STRING", {}],
                }
            },
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 9)

    assert prompt["3"]["inputs"] == {
        "images.image0": ["1", 0],
        "images.image1": ["2", 0],
    }


def test_builder_accepts_preview_image_as_the_single_final_output() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "Source", "mode": 0, "inputs": []},
            {
                "id": 2,
                "type": "PreviewImage",
                "mode": 0,
                "inputs": [{"name": "images", "link": 1}],
                "widgets_values": [],
            },
        ],
        "links": [[1, 1, 0, 2, 0]],
    }
    definitions = {
        "Source": {"input": {"required": {}}},
        "PreviewImage": {
            "input": {"required": {"images": ["IMAGE", {}]}},
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 2)

    assert set(prompt) == {"1", "2"}
    assert prompt["2"]["class_type"] == "PreviewImage"


def test_builder_resolves_ui_reroute_without_emitting_a_fake_api_node() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "Source", "mode": 0, "inputs": []},
            {
                "id": 2,
                "type": "Reroute",
                "mode": 0,
                "inputs": [{"name": "", "link": 1}],
            },
            {
                "id": 3,
                "type": "PreviewImage",
                "mode": 0,
                "inputs": [{"name": "images", "link": 2}],
                "widgets_values": [],
            },
        ],
        "links": [[1, 1, 0, 2, 0], [2, 2, 0, 3, 0]],
    }
    definitions = {
        "Source": {"input": {"required": {}}},
        "PreviewImage": {
            "input": {"required": {"images": ["IMAGE", {}]}},
            "output_node": True,
        },
    }

    prompt = build_prompt(workflow, definitions, 3)

    assert "2" not in prompt
    assert prompt["3"]["inputs"]["images"] == ["1", 0]
