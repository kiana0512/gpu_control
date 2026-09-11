#!/usr/bin/env python3
"""Rebuild a verified control-plane Docker context from pinned Git objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

SOURCE_ROOT = Path(__file__).resolve().parent
REQUIRED_TAG = "runtime/5070-control-c61648f"
FINAL_REVISION = "c61648fe96fd5be429cd1f7b470da43131b88204"
STAGES = {
    "resource": {
        "revision": "13ef0a129759404c2313bc7d7a31827c1244eea8",
        "installer": "apply_resource_layer.py",
    },
    "final": {
        "revision": FINAL_REVISION,
        "installer": "apply_control_layer.py",
    },
}


def git(*args: str) -> bytes:
    result = subprocess.run(  # noqa: S603 - fixed Git subcommands and locked revisions; no shell
        ["git", "-C", str(SOURCE_ROOT), *args],  # noqa: S607 - use the operator's installed Git
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"Required Git object unavailable; fetch tag {REQUIRED_TAG}. "
            f"Git command failed: {' '.join(args)}"
        )
    return result.stdout


def prepare(stage: str, output: Path) -> dict[str, object]:
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    output = output.resolve()
    if output == SOURCE_ROOT or SOURCE_ROOT in output.parents:
        raise ValueError("Choose a temporary output directory outside this source archive")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be absent or an empty directory")

    tag_revision = git("rev-parse", "--verify", f"refs/tags/{REQUIRED_TAG}^{{commit}}")
    if tag_revision.decode().strip() != FINAL_REVISION:
        raise ValueError(f"Tag {REQUIRED_TAG} does not point to the locked final commit")
    revision = STAGES[stage]["revision"]
    git("merge-base", "--is-ancestor", revision, FINAL_REVISION)

    source = SOURCE_ROOT / stage
    manifest = json.loads((source / "layer/base-modules.json").read_bytes())
    files: dict[str, bytes] = {}
    modules: dict[str, str] = {}
    for detail in manifest.values():
        relative = PurePosixPath(detail["source"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Manifest contains an unsafe source path")
        expected = detail["new_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid expected SHA-256 for {relative}")
        content = git("show", f"{revision}:{relative}")
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ValueError(f"Pinned Git module does not match release manifest: {relative}")
        files[f"layer/{relative}"] = content
        modules[str(relative)] = actual

    descriptors = [
        "Dockerfile",
        f"layer/{STAGES[stage]['installer']}",
        "layer/base-modules.json",
    ]
    for relative in descriptors:
        files[relative] = (source / relative).read_bytes()

    # Validate every Git object and descriptor before creating the context.
    output.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)
    receipt: dict[str, object] = {
        "stage": stage,
        "source_revision": revision,
        "required_tag": REQUIRED_TAG,
        "required_tag_revision": FINAL_REVISION,
        "module_sha256": modules,
        "context_file_sha256": {
            relative: hashlib.sha256(content).hexdigest()
            for relative, content in sorted(files.items())
        },
    }
    (output / "context-manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = prepare(args.stage, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Context preparation failed: {exc}\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
