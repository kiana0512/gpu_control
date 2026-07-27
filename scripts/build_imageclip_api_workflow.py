#!/usr/bin/env python3
"""Build a final-output-only API prompt from a tracked ComfyUI UI graph."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import urlopen


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui-workflow", type=Path, required=True)
    parser.add_argument("--object-info-url", required=True)
    parser.add_argument("--final-output-node", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def object_info(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=30) as response:  # noqa: S310 - operator-provided LAN URL
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError("ComfyUI object_info response must be an object")
    return value


def control_after_generate(definition_inputs: dict[str, Any], name: str) -> bool:
    """Return whether ComfyUI serializes an extra control widget after this input."""
    for group in ("required", "optional", "hidden"):
        values = definition_inputs.get(group, {})
        if not isinstance(values, dict):
            continue
        specification = values.get(name)
        if not isinstance(specification, list) or len(specification) < 2:
            continue
        options = specification[1]
        return isinstance(options, dict) and bool(options.get("control_after_generate"))
    return False


def serialized_control_after_generate(
    definition_inputs: dict[str, Any], name: str, values: list[Any], index: int
) -> bool:
    """Detect a UI-only seed control even when object_info omits its flag."""
    if control_after_generate(definition_inputs, name):
        return True
    return (
        name in {"seed", "noise_seed"}
        and index < len(values)
        and values[index] in {"fixed", "increment", "decrement", "randomize"}
    )


def required_input_present(name: str, specification: Any, inputs: dict[str, Any]) -> bool:
    if name in inputs:
        return True
    if not isinstance(specification, list) or len(specification) < 2:
        return False
    if specification[0] != "COMFY_AUTOGROW_V3" or not isinstance(specification[1], dict):
        return False
    template = specification[1].get("template", {})
    if not isinstance(template, dict):
        return False
    prefix = template.get("prefix")
    minimum = template.get("min", 0)
    if not isinstance(prefix, str) or not isinstance(minimum, int):
        return False
    count = sum(key.startswith(f"{name}.{prefix}") for key in inputs)
    return count >= minimum


def build_prompt(
    workflow: dict[str, Any], definitions: dict[str, Any], final_output_node: int
) -> dict[str, Any]:
    raw_nodes = workflow.get("nodes")
    raw_links = workflow.get("links")
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise ValueError("input must be a ComfyUI UI workflow with nodes and links")
    nodes = {int(node["id"]): node for node in raw_nodes if isinstance(node, dict)}
    final = nodes.get(final_output_node)
    if final is None or final.get("type") != "SaveImage":
        raise ValueError("final output node must exist and be SaveImage")
    links = {
        int(link[0]): {
            "origin_id": int(link[1]),
            "origin_slot": int(link[2]),
            "target_id": int(link[3]),
            "target_slot": int(link[4]),
        }
        for link in raw_links
        if isinstance(link, list) and len(link) >= 5
    }

    ancestors: set[int] = set()
    pending = [final_output_node]
    while pending:
        node_id = pending.pop()
        if node_id in ancestors:
            continue
        node = nodes.get(node_id)
        if node is None:
            raise ValueError(f"workflow link references missing node {node_id}")
        if int(node.get("mode", 0)) not in {0}:
            raise ValueError(f"required node {node_id} is disabled or bypassed")
        ancestors.add(node_id)
        for input_row in node.get("inputs", []):
            link_id = input_row.get("link") if isinstance(input_row, dict) else None
            if link_id is not None:
                link = links.get(int(link_id))
                if link is None or link["target_id"] != node_id:
                    raise ValueError(f"node {node_id} has an invalid input link")
                origin = nodes.get(link["origin_id"])
                if origin is None:
                    raise ValueError(
                        f"workflow link references missing node {link['origin_id']}"
                    )
                # PrimitiveNode is a UI-only widget proxy. ComfyUI's API export
                # inlines its single value into the target input and omits the
                # primitive node from the prompt graph.
                if origin.get("type") != "PrimitiveNode":
                    pending.append(link["origin_id"])

    prompt: dict[str, Any] = {}
    output_nodes: set[int] = set()
    for node_id in sorted(ancestors):
        node = nodes[node_id]
        class_type = str(node.get("type", ""))
        definition = definitions.get(class_type)
        if not isinstance(definition, dict):
            raise ValueError(f"ComfyUI does not provide class_type {class_type}")
        definition_inputs = definition.get("input", {})
        accepted_names: set[str] = set()
        if isinstance(definition_inputs, dict):
            for group in ("required", "optional", "hidden"):
                values = definition_inputs.get(group, {})
                if isinstance(values, dict):
                    accepted_names.update(str(name) for name in values)
        if definition.get("output_node"):
            output_nodes.add(node_id)

        values = list(node.get("widgets_values") or [])
        widget_index = 0
        inputs: dict[str, Any] = {}
        for input_row in node.get("inputs", []):
            if not isinstance(input_row, dict):
                continue
            name = str(input_row.get("name", ""))
            link_id = input_row.get("link")
            widget = input_row.get("widget")
            widget_value: Any = None
            has_widget = isinstance(widget, dict)
            # ComfyUI keeps the serialized widget value even when that input is
            # linked; the link wins in the API prompt. It also appends a UI-only
            # control value (for example seed's "fixed" mode) after widgets whose
            # object_info declares control_after_generate.
            if has_widget:
                if widget_index >= len(values):
                    raise ValueError(f"node {node_id} is missing widget value for {name}")
                widget_value = values[widget_index]
                widget_index += 1
                if serialized_control_after_generate(
                    definition_inputs, name, values, widget_index
                ):
                    if widget_index >= len(values):
                        raise ValueError(
                            f"node {node_id} is missing control-after-generate value for {name}"
                        )
                    widget_index += 1
            if link_id is not None:
                link = links[int(link_id)]
                origin = nodes[link["origin_id"]]
                if origin.get("type") == "PrimitiveNode":
                    primitive_values = list(origin.get("widgets_values") or [])
                    if len(primitive_values) != 1:
                        raise ValueError(
                            f"primitive node {link['origin_id']} must contain exactly one value"
                        )
                    inputs[name] = primitive_values[0]
                else:
                    inputs[name] = [str(link["origin_id"]), link["origin_slot"]]
            elif has_widget and name in accepted_names:
                inputs[name] = widget_value

        missing = []
        required = definition_inputs.get("required", {}) if isinstance(definition_inputs, dict) else {}
        if isinstance(required, dict):
            missing = sorted(
                str(name)
                for name, specification in required.items()
                if not required_input_present(str(name), specification, inputs)
            )
        if missing:
            raise ValueError(f"node {node_id} is missing required inputs: {', '.join(missing)}")
        if widget_index != len(values):
            raise ValueError(
                f"node {node_id} has {len(values) - widget_index} unmapped widget values"
            )
        prompt[str(node_id)] = {
            "inputs": inputs,
            "class_type": class_type,
            "_meta": {"title": str(node.get("title") or class_type)},
        }

    if output_nodes != {final_output_node}:
        raise ValueError(
            "final graph contains unexpected output nodes: "
            + ", ".join(str(value) for value in sorted(output_nodes))
        )
    if set(prompt) != {str(value) for value in ancestors}:
        raise AssertionError("prompt ancestry mismatch")
    return prompt


def main() -> None:
    args = arguments()
    workflow = read_json(args.ui_workflow)
    definitions = object_info(args.object_info_url)
    prompt = build_prompt(workflow, definitions, args.final_output_node)
    canonical = json.dumps(prompt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(prompt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(
        json.dumps(
            {
                "nodes": len(prompt),
                "class_types": sorted({row["class_type"] for row in prompt.values()}),
                "final_output_node": str(args.final_output_node),
                "sha256": digest,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
