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
