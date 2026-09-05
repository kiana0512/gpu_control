"""Compact semantic prompt fusion for Klein dual-image workflows.

The default path should work with no user text. Optional manual text boxes are
short correction hooks for object identity and the locked image-1 view.
"""

from __future__ import annotations


FINAL_FIELDS = (
    "VIEW",
    "OBJECT",
    "VISIBLE_PARTS",
    "ASSEMBLY",
    "MATERIAL_MAP",
)


def parse_analysis_fields(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Parse the compact semantic contract and report ignored fields."""
    fields: dict[str, list[str]] = {}
    rejected: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip().upper()
        value = " ".join(value.strip().split())
        if not value:
            continue
        if name in FINAL_FIELDS:
            fields.setdefault(name, []).append(value)
        else:
            rejected.append(name)
    return fields, rejected


def _normalize_manual_field(manual_value: str, field_name: str) -> str:
    manual = str(manual_value or "").strip()
    if not manual:
        return ""
    if ":" in manual:
        name, value = manual.split(":", 1)
        if name.strip().upper() == field_name:
            return " ".join(value.strip().split())
    return " ".join(manual.split())


def fuse_klein_prompt(
    fixed_prompt: str,
    automatic_analysis: str,
    manual_object: str = "",
    manual_view: str = "",
) -> tuple[str, str, str]:
    """Fuse fixed geometry rules with compact automatic semantics."""
    fields, rejected = parse_analysis_fields(automatic_analysis)
    preview_lines = [
        f"{name}: {value}"
        for name in FINAL_FIELDS
        for value in fields.get(name, [])
    ]

    semantic_lines: list[str] = []
    overrides = {
        "VIEW": _normalize_manual_field(manual_view, "VIEW"),
        "OBJECT": _normalize_manual_field(manual_object, "OBJECT"),
    }
    for name in FINAL_FIELDS:
        override = overrides.get(name, "")
        if override:
            semantic_lines.append(f"{name}: {override}")
            continue
        semantic_lines.extend(f"{name}: {value}" for value in fields.get(name, []))

    base = str(fixed_prompt or "").strip()
    if semantic_lines:
        semantic_block = "SEMANTIC GUIDANCE:\n" + "\n".join(semantic_lines)
        final = f"{base}\n\n{semantic_block}" if base else semantic_block
    else:
        final = base

    warnings: list[str] = []
    if not preview_lines:
        warnings.append("Missing compact semantic fields; using fixed prompt only.")
    if rejected:
        warnings.append(
            "Filtered non-whitelisted fields: "
            + ", ".join(dict.fromkeys(rejected))
        )

    return (
        final,
        "\n".join(preview_lines) or "No compact semantic fields were parsed.",
        "\n".join(warnings) or "OK",
    )


class CherryKleinPromptFusion:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "manual_object": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
                "manual_view": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
                "auto_analysis": ("STRING", {"forceInput": True}),
                "fixed_prompt": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("final_prompt", "auto_preview", "warnings")
    FUNCTION = "fuse"
    CATEGORY = "Cherry_lizi/Klein Workflow"

    def fuse(self, *args, **kwargs):
        manual_object = ""
        manual_view = ""
        automatic_analysis = ""
        fixed_prompt = ""

        if len(args) >= 4:
            manual_object = args[0]
            manual_view = args[1]
            automatic_analysis = args[2]
            fixed_prompt = args[3]
        elif len(args) >= 3:
            # Backward compatibility with the old three-input node:
            # manual_object, auto_analysis, fixed_prompt.
            manual_object = args[0]
            automatic_analysis = args[1]
            fixed_prompt = args[2]

        if kwargs:
            values = list(kwargs.values())
            manual_object = kwargs.get(
                "manual_object",
                values[0] if values else manual_object,
            )
            manual_view = kwargs.get(
                "manual_view",
                values[1] if len(values) > 1 else manual_view,
            )
            automatic_analysis = kwargs.get(
                "auto_analysis",
                values[2] if len(values) > 2 else automatic_analysis,
            )
            fixed_prompt = kwargs.get(
                "fixed_prompt",
                values[3] if len(values) > 3 else fixed_prompt,
            )

        return fuse_klein_prompt(
            fixed_prompt,
            automatic_analysis,
            manual_object,
            manual_view,
        )


class CherryKleinTextBox:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("STRING",)
    FUNCTION = "pass_text"
    CATEGORY = "Cherry_lizi/Klein Workflow"

    def pass_text(self, text: str):
        return (" ".join(str(text or "").strip().split()),)


NODE_CLASS_MAPPINGS = {
    "CherryKleinPromptFusion": CherryKleinPromptFusion,
    "CherryKleinTextBox": CherryKleinTextBox,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryKleinPromptFusion": "Cherry - Klein Prompt Fusion",
    "CherryKleinTextBox": "Cherry - Klein Text Box",
}
