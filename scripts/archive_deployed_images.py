#!/usr/bin/env python3
"""Save selected, already-built images as checksum-verified 128 MiB LFS parts.

This records local Docker identities. It does not build/deploy images, claim a
registry digest or SBOM, copy runtime volumes, or upload to GitHub.
"""

# Local operator-supplied argv; shell execution is never used.
# ruff: noqa: S603
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--name", default="runtime-images.tar.gz")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    if any(image.startswith("-") for image in args.images):
        parser.error("image selectors must not be command options")
    if Path(args.name).name != args.name or not args.name.endswith(".tar.gz"):
        parser.error("--name must be a .tar.gz filename")
    destination = args.output_dir.resolve()
    if destination.exists() and any(destination.iterdir()):
        parser.error("output directory must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    images = json.loads(subprocess.check_output(["/usr/bin/docker", "image", "inspect", *args.images]))
    evidence = []
    for tag, item in zip(args.images, images, strict=True):
        evidence.append(
            {
                "tag": tag,
                "local_image_id": item["Id"],
                "rootfs_layers": item["RootFS"]["Layers"],
                "labels": item["Config"].get("Labels", {}),
                "identity_semantics": "Docker Engine local ID; not asserted to be a registry digest",
            }
        )
    check = subprocess.check_output(
        ["/usr/bin/git", "check-attr", "filter", "--", str(destination / (args.name + ".part-00"))],
        text=True,
    )
    if not check.rstrip().endswith(": lfs"):
        parser.error("output part path is not covered by Git LFS")
    save = subprocess.Popen(
        ["/usr/bin/docker", "image", "save", *args.images], stdout=subprocess.PIPE, stderr=None
    )
    assert save.stdout is not None
    compressed = subprocess.Popen(
        ["/usr/bin/gzip", "-n", "-1"], stdin=save.stdout, stdout=subprocess.PIPE, stderr=None
    )
    save.stdout.close()
    assert compressed.stdout is not None
    whole = hashlib.sha256()
    parts = []
    part_size = 128 * 1024 * 1024
    index = 0
    try:
        while True:
            data = compressed.stdout.read(part_size)
            if not data:
                break
            path = destination / (args.name + f".part-{index:03d}")
            path.write_bytes(data)
            whole.update(data)
            parts.append(
                {
                    "file": path.name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            index += 1
        if compressed.wait() != 0:
            raise RuntimeError("gzip failed")
        if save.wait() != 0:
            raise RuntimeError("docker save failed")
    except BaseException:
        save.terminate()
        compressed.terminate()
        raise
    # Re-read every part to detect disk or splitting errors before publishing.
    verified = hashlib.sha256()
    for part in parts:
        raw = (destination / part["file"]).read_bytes()
        if len(raw) != part["size_bytes"] or hashlib.sha256(raw).hexdigest() != part["sha256"]:
            raise RuntimeError(f"part verification failed: {part['file']}")
        verified.update(raw)
    if not parts or verified.hexdigest() != whole.hexdigest():
        raise RuntimeError("combined archive verification failed")
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "images": evidence,
        "archive_name": args.name,
        "archive_sha256": whole.hexdigest(),
        "compressed_size_bytes": sum(p["size_bytes"] for p in parts),
        "parts": parts,
        "parts_reread_verified": True,
        "deployed_runtime_volumes_included": False,
    }
    (destination / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    (destination / "SHA256SUMS.txt").write_text(
        "".join(p["sha256"] + "  " + p["file"] + "\n" for p in parts)
    )
    print(
        json.dumps(
            {
                "images": args.images,
                "parts": len(parts),
                "bytes": record["compressed_size_bytes"],
                "archive_sha256": whole.hexdigest(),
                "verified": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
