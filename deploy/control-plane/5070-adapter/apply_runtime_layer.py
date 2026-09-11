"""Apply a verified single-module layer and keep installed wheel metadata honest."""

import base64
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

BASE_SETTINGS_SHA256 = "f319d60c07f0966ab049c8229782ae9203fdaa385aebffd80441145457aa85f9"
VERSION = "1.5.23.post1"


def main() -> None:
    dist = importlib.metadata.distribution("gpu-control")
    if dist.version != "1.5.23":
        raise SystemExit("unexpected base package version")
    site = Path(dist.locate_file(""))
    settings = site / "packages/gpu_control_core/settings.py"
    if hashlib.sha256(settings.read_bytes()).hexdigest() != BASE_SETTINGS_SHA256:
        raise SystemExit("base settings do not match the approved running images")
    before = site / "gpu_control-1.5.23.dist-info"
    after = site / f"gpu_control-{VERSION}.dist-info"
    if not before.is_dir() or after.exists():
        raise SystemExit("unexpected package metadata directory")
    with (before / "RECORD").open(newline="") as source:
        records = list(csv.reader(source))
    metadata = (before / "METADATA").read_text()
    if metadata.count("\nVersion: 1.5.23\n") != 1:
        raise SystemExit("base package METADATA version is ambiguous")
    settings.write_bytes(Path("/tmp/5070-settings.py").read_bytes())  # noqa: S108 - build-only COPY
    before.rename(after)
    (after / "METADATA").write_text(
        metadata.replace("\nVersion: 1.5.23\n", f"\nVersion: {VERSION}\n")
    )
    evidence = after / "gpu-control-5070-layer.json"
    evidence.write_text(
        json.dumps(
            {
                "package_version": VERSION,
                "source_revision": os.environ["GPU_CONTROL_BUILD_REVISION"],
                "base_image_id": os.environ["GPU_CONTROL_ADAPTER_BASE_IMAGE_ID"],
                "base_settings_sha256": BASE_SETTINGS_SHA256,
                "settings_sha256": hashlib.sha256(settings.read_bytes()).hexdigest(),
                "scope": "node HMAC map; additive node registration in API scripts",
            },
            indent=2,
        )
        + "\n"
    )
    changed = {
        "packages/gpu_control_core/settings.py",
        f"{after.name}/METADATA",
        f"{after.name}/gpu-control-5070-layer.json",
    }
    output = []
    for row in records:
        row[0] = row[0].replace(before.name + "/", after.name + "/", 1)
        if row[0] in changed:
            content = (site / row[0]).read_bytes()
            row[1] = "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(content).digest()
            ).decode().rstrip("=")
            row[2] = str(len(content))
        output.append(row)
    content = evidence.read_bytes()
    output.append(
        [
            f"{after.name}/{evidence.name}",
            "sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("="),
            str(len(content)),
        ]
    )
    with (after / "RECORD").open("w", newline="") as destination:
        csv.writer(destination).writerows(output)
    print("VERIFIED_SINGLE_MODULE_LAYER", VERSION)


if __name__ == "__main__":
    main()
