#!/usr/bin/env python3
"""Generate deterministic, synthetic fixtures for the six-API load harness.

The generator has no network code and accepts no source-asset path.  It writes
only to a fresh directory outside the repository, leaves an INCOMPLETE marker
on failure, and never overwrites an existing fixture directory or file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw

DEFAULT_OUTPUT = Path("/opt/gpu-control-load-fixtures/synthetic-six-api-v1")
INCOMPLETE_MARKER = ".synthetic-six-api-v1.incomplete"
SCHEMA_VERSION = "synthetic-six-api-v1"
PINNED_BLENDER_IMAGE = (
    "li3d/blender-worker@sha256:"
    "9bf4344503041abec7dd67067ccbbb0946223af53b06d1a4a67a27acfeaab6ad"
)
PINNED_PYTHON_IMAGE = (
    "gpu-control-api@sha256:"
    "06147d527d4a146141c9cf3c56b62c474096543cbdbde2050b2d1a652e478cb3"
)
REQUIRED_BLENDER_FILES = (
    "uv/asset.blend",
    "retopology/audit.blend",
    "retopology/process.blend",
    "bake/asset_low.fbx",
    "bake/asset_high.fbx",
)


class FixtureGenerationError(RuntimeError):
    """Raised when generation would violate the synthetic fixture contract."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_output(output: Path, repository_root: Path) -> tuple[Path, Path]:
    repository = repository_root.expanduser().resolve()
    destination = output.expanduser().resolve(strict=False)
    if destination in {Path("/"), Path("/opt"), Path("/srv"), Path("/tmp")}:  # noqa: S108
        raise FixtureGenerationError("refusing a broad fixture output directory")
    if _is_within(destination, repository):
        raise FixtureGenerationError("synthetic load fixtures must remain outside the repository")
    return destination, repository


def prepare_output_root(output: Path, repository_root: Path) -> tuple[Path, Path]:
    destination, repository = resolve_output(output, repository_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir(mode=0o750)
    except FileExistsError as exc:
        raise FixtureGenerationError(
            f"output already exists; refusing overwrite: {destination}"
        ) from exc
    marker = destination / INCOMPLETE_MARKER
    marker.write_text(f"schema_version={SCHEMA_VERSION}\n", encoding="utf-8")
    return destination, repository


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_bytes_no_overwrite(path: Path, content: bytes) -> None:
    _ensure_parent(path)
    try:
        with path.open("xb") as target:
            target.write(content)
    except FileExistsError as exc:
        raise FixtureGenerationError(f"refusing to overwrite {path}") from exc


def write_text_no_overwrite(path: Path, content: str) -> None:
    write_bytes_no_overwrite(path, content.encode("utf-8"))


def write_json_no_overwrite(path: Path, payload: Any) -> None:
    write_text_no_overwrite(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def save_png_no_overwrite(path: Path, image: Image.Image) -> None:
    _ensure_parent(path)
    try:
        with path.open("xb") as target:
            image.save(target, format="PNG", optimize=False, compress_level=6)
    except FileExistsError as exc:
        raise FixtureGenerationError(f"refusing to overwrite {path}") from exc


def synthetic_subject_frame(size: int, ordinal: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (226, 232, 239))
    draw = ImageDraw.Draw(image)
    band = max(1, size // 16)
    for row in range(0, size, band):
        shade = 226 - ((row // band + ordinal) % 4) * 5
        draw.rectangle((0, row, size, min(size, row + band)), fill=(shade, shade + 5, shade + 10))

    shift = int((ordinal - 2.5) * size * 0.018)
    center_x = size // 2 + shift
    head = size // 9
    body_width = size // 4
    body_top = size * 7 // 20
    body_bottom = size * 13 // 20
    accent = (46 + ordinal * 13, 92 + ordinal * 9, 186 - ordinal * 11)
    outline = (25, 31, 45)
    draw.rounded_rectangle(
        (
            center_x - body_width // 2,
            body_top,
            center_x + body_width // 2,
            body_bottom,
        ),
        radius=max(4, size // 32),
        fill=accent,
        outline=outline,
        width=max(2, size // 128),
    )
    draw.ellipse(
        (
            center_x - head,
            body_top - head * 2,
            center_x + head,
            body_top,
        ),
        fill=(244, 177, 91),
        outline=outline,
        width=max(2, size // 128),
    )
    limb = max(4, size // 30)
    draw.line(
        (center_x - body_width // 2, body_top + head, center_x - size // 4, size * 3 // 5),
        fill=outline,
        width=limb,
    )
    draw.line(
        (center_x + body_width // 2, body_top + head, center_x + size // 4, size * 3 // 5),
        fill=outline,
        width=limb,
    )
    draw.line(
        (center_x - body_width // 4, body_bottom, center_x - size // 7, size * 17 // 20),
        fill=outline,
        width=limb,
    )
    draw.line(
        (center_x + body_width // 4, body_bottom, center_x + size // 7, size * 17 // 20),
        fill=outline,
        width=limb,
    )
    draw.ellipse(
        (center_x - size // 18, body_top - size // 10, center_x - size // 40, body_top - size // 16),
        fill=outline,
    )
    draw.ellipse(
        (center_x + size // 40, body_top - size // 10, center_x + size // 18, body_top - size // 16),
        fill=outline,
    )
    draw.ellipse(
        (size // 5, size * 17 // 20, size * 4 // 5, size * 9 // 10),
        fill=(95, 105, 119),
    )
    return image


def synthetic_material_image(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (32, 36, 44))
    draw = ImageDraw.Draw(image)
    half = size // 2
    draw.rectangle((0, 0, half, half), fill=(168, 91, 47))
    draw.rectangle((half, 0, size, half), fill=(142, 151, 164))
    draw.rectangle((0, half, half, size), fill=(52, 108, 151))
    draw.rectangle((half, half, size, size), fill=(87, 73, 106))
    step = max(4, size // 32)
    for offset in range(0, size, step):
        draw.line((0, offset // 2, half, offset // 2 + size // 12), fill=(119, 58, 30), width=2)
        draw.line((half + offset, 0, half, offset), fill=(202, 209, 216), width=2)
        draw.line((0, half + offset, half, half + offset // 2), fill=(32, 73, 112), width=2)
        draw.line((half + offset, half, size, half + offset), fill=(124, 101, 142), width=2)
    return image


def synthetic_reference(size: int, *, side: bool) -> Image.Image:
    image = Image.new("RGB", (size, size), (238, 240, 244))
    draw = ImageDraw.Draw(image)
    margin = size // 5
    if side:
        polygon = [
            (margin + size // 12, margin),
            (size - margin, margin + size // 10),
            (size - margin - size // 12, size - margin),
            (margin, size - margin - size // 10),
        ]
    else:
        polygon = [
            (margin, margin),
            (size - margin, margin),
            (size - margin, size - margin),
            (margin, size - margin),
        ]
    draw.polygon(polygon, fill=(91, 132, 191), outline=(20, 28, 40))
    for fraction in (1, 2, 3):
        position = margin + (size - 2 * margin) * fraction // 4
        draw.line((position, margin, position, size - margin), fill=(217, 225, 235), width=2)
        draw.line((margin, position, size - margin, position), fill=(217, 225, 235), width=2)
    return image


def synthetic_texture(size: int, role: str) -> Image.Image:
    if role == "base_color":
        image = Image.new("RGB", (size, size), (62, 96, 156))
        draw = ImageDraw.Draw(image)
        tile = max(8, size // 8)
        for y in range(0, size, tile):
            for x in range(0, size, tile):
                if (x // tile + y // tile) % 2:
                    draw.rectangle((x, y, x + tile, y + tile), fill=(194, 117, 64))
        return image
    image = Image.new("L", (size, size), 0)
    pixels = image.load()
    if pixels is None:
        raise FixtureGenerationError("cannot allocate synthetic texture pixels")
    for y in range(size):
        for x in range(size):
            if role == "roughness":
                pixels[x, y] = 48 + ((x * 5 + y * 3) % 176)
            else:
                pixels[x, y] = 230 if (x // max(1, size // 4)) % 2 else 18
    return image


def generate_raster_fixtures(
    root: Path, *, frame_count: int, image_size: int, texture_size: int
) -> None:
    frame_root = root / "imageclip" / "frames"
    frames: list[dict[str, Any]] = []
    for ordinal in range(frame_count):
        path = frame_root / f"frame_{ordinal:04d}.png"
        save_png_no_overwrite(path, synthetic_subject_frame(image_size, ordinal))
        content_size = path.stat().st_size
        relative_path = f"frames/{path.name}"
        frames.append(
            {
                "ordinal": ordinal,
                "relative_path": relative_path,
                "size_bytes": content_size,
                "sha256": sha256_path(path),
            }
        )

    manifest = {
        "schema_version": "1.0",
        "external_batch_id": "loadtest:synthetic:imageclip:v1",
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": frames,
    }
    write_json_no_overwrite(root / "imageclip" / "manifest.json", manifest)
    archive_path = root / "imageclip" / "frames.zip"
    try:
        with zipfile.ZipFile(
            archive_path, "x", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for frame in frames:
                archive.write(
                    root / "imageclip" / str(frame["relative_path"]),
                    str(frame["relative_path"]),
                    compress_type=zipfile.ZIP_STORED,
                )
    except FileExistsError as exc:
        raise FixtureGenerationError(f"refusing to overwrite {archive_path}") from exc

    save_png_no_overwrite(root / "roughness" / "material.png", synthetic_material_image(image_size))
    save_png_no_overwrite(
        root / "retopology" / "front.png", synthetic_reference(texture_size, side=False)
    )
    save_png_no_overwrite(
        root / "retopology" / "side.png", synthetic_reference(texture_size, side=True)
    )
    for role in ("base_color", "roughness", "metallic"):
        save_png_no_overwrite(
            root / "bake" / f"{role}.png", synthetic_texture(texture_size, role)
        )


def write_metadata_and_fixture_manifest(root: Path) -> None:
    write_json_no_overwrite(
        root / "uv" / "metadata.json",
        {
            "external_asset_id": "loadtest:synthetic:uv:v1",
            "options": {
                "resolution": 1024,
                "padding_px": 10,
                "hard_edge_angle_degrees": 75.0,
                "hidden_axis": "auto",
                "texel_density_mode": "uniform",
                "qa_profile": "pbr-v1",
            },
        },
    )
    common_retopology = {
        "high_object": "synthetic_high",
        "reference_object": "synthetic_reference",
        "low_object": "synthetic_low",
        "require_closed": True,
    }
    write_json_no_overwrite(
        root / "retopology" / "audit.metadata.json",
        {
            "external_asset_id": "loadtest:synthetic:retopology-audit:v1",
            "options": common_retopology,
        },
    )
    write_json_no_overwrite(
        root / "retopology" / "process.metadata.json",
        {
            "external_asset_id": "loadtest:synthetic:retopology-process:v1",
            "options": {
                **common_retopology,
                "generated_low_object": "synthetic_generated_v001",
                "algorithm": "cleanup_existing",
                "topology_style": "preserve_existing",
                "topology_mode": "mixed",
                "target_faces": 96,
                "preserve_sharp": True,
                "preserve_boundary": True,
                "planar_reduction": False,
                "planar_angle_threshold": 5.0,
                "preserve_hard_edges": True,
                "preserve_components": True,
                "allow_triangles": True,
                "allow_ngons": False,
                "render_resolution": 256,
                "max_repair_rounds": 0,
            },
            "reference_views": [
                {"filename": "front.png", "view": "front", "label": "synthetic front"},
                {"filename": "side.png", "view": "side", "label": "synthetic side"},
            ],
            "user_request": "Synthetic closed-cube fixture; preserve components and source objects.",
        },
    )
    write_json_no_overwrite(
        root / "bake" / "metadata.json",
        {
            "external_asset_id": "loadtest:synthetic:substance:v1",
            "options": {
                "profile": "li3d-pbr-full-v2",
                "resolution": 512,
                "texture_cache_mb": 8192,
            },
        },
    )
    fixture = {
        "schema_version": "1.0",
        "apis": {
            "imageclip_batch": {
                "archive": str((root / "imageclip" / "frames.zip").resolve()),
                "manifest": str((root / "imageclip" / "manifest.json").resolve()),
            },
            "modelview_roughness": {
                "image": str((root / "roughness" / "material.png").resolve())
            },
            "uv_process": {
                "asset": str((root / "uv" / "asset.blend").resolve()),
                "metadata": str((root / "uv" / "metadata.json").resolve()),
            },
            "retopology_audit": {
                "project": str((root / "retopology" / "audit.blend").resolve()),
                "metadata": str((root / "retopology" / "audit.metadata.json").resolve()),
            },
            "retopology_process": {
                "project": str((root / "retopology" / "process.blend").resolve()),
                "metadata": str((root / "retopology" / "process.metadata.json").resolve()),
                "reference_images": [
                    str((root / "retopology" / "front.png").resolve()),
                    str((root / "retopology" / "side.png").resolve()),
                ],
            },
            "substance_bake": {
                "low_mesh": str((root / "bake" / "asset_low.fbx").resolve()),
                "high_mesh": str((root / "bake" / "asset_high.fbx").resolve()),
                "base_color_texture": str((root / "bake" / "base_color.png").resolve()),
                "roughness_texture": str((root / "bake" / "roughness.png").resolve()),
                "metallic_texture": str((root / "bake" / "metallic.png").resolve()),
                "metadata": str((root / "bake" / "metadata.json").resolve()),
            },
        },
    }
    write_text_no_overwrite(
        root / "fixtures.yaml",
        yaml.safe_dump(fixture, sort_keys=False, allow_unicode=True),
    )


def validate_png(path: Path) -> None:
    try:
        with Image.open(path) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                raise FixtureGenerationError(f"invalid PNG contract: {path}")
            image.verify()
        with Image.open(path) as image:
            image.load()
    except OSError as exc:
        raise FixtureGenerationError(f"cannot decode PNG fixture: {path}") from exc


def validate_raster_and_archive(root: Path) -> None:
    manifest_path = root / "imageclip" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not frames:
        raise FixtureGenerationError("ImageClip manifest needs at least one frame")
    expected = {str(frame["relative_path"]): frame for frame in frames}
    with zipfile.ZipFile(root / "imageclip" / "frames.zip") as archive:
        infos = {item.filename: item for item in archive.infolist() if not item.is_dir()}
        if set(infos) != set(expected):
            raise FixtureGenerationError("ImageClip archive entries differ from manifest")
        for relative_path, frame in expected.items():
            info = infos[relative_path]
            if info.compress_type != zipfile.ZIP_STORED:
                raise FixtureGenerationError("ImageClip archive must use ZIP_STORED")
            content = archive.read(info)
            if len(content) != frame["size_bytes"]:
                raise FixtureGenerationError(f"ImageClip size mismatch: {relative_path}")
            if hashlib.sha256(content).hexdigest() != frame["sha256"]:
                raise FixtureGenerationError(f"ImageClip SHA mismatch: {relative_path}")
            validate_png(root / "imageclip" / relative_path)
    for path in (
        root / "roughness" / "material.png",
        root / "retopology" / "front.png",
        root / "retopology" / "side.png",
        root / "bake" / "base_color.png",
        root / "bake" / "roughness.png",
        root / "bake" / "metallic.png",
    ):
        validate_png(path)


def run_blender_generator(
    root: Path,
    repository_root: Path,
    *,
    blender_binary: Path,
    blender_script: Path,
) -> None:
    if not blender_binary.is_file() or not os.access(blender_binary, os.X_OK):
        raise FixtureGenerationError(f"Blender executable is unavailable: {blender_binary}")
    if not blender_script.is_file():
        raise FixtureGenerationError(f"Blender fixture script is unavailable: {blender_script}")
    command = [
        str(blender_binary),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(blender_script),
        "--",
        "--output",
        str(root),
        "--repository-root",
        str(repository_root),
        "--allow-incomplete-root",
    ]
    try:
        subprocess.run(command, check=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        raise FixtureGenerationError(f"Blender fixture generation failed: exit {exc.returncode}") from exc


def validate_blender_receipt(root: Path) -> None:
    receipt_path = root / "blender_validation.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureGenerationError("Blender validation receipt is missing or invalid") from exc
    if receipt.get("schema_version") != "synthetic_blender_fixtures.v1":
        raise FixtureGenerationError("Blender validation receipt schema mismatch")
    if receipt.get("passed") is not True:
        raise FixtureGenerationError("Blender object/UV validation did not pass")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(REQUIRED_BLENDER_FILES):
        raise FixtureGenerationError("Blender validation receipt artifact set mismatch")
    for relative_path in REQUIRED_BLENDER_FILES:
        path = root / relative_path
        if not path.is_file() or path.stat().st_size < 1:
            raise FixtureGenerationError(f"Blender fixture is missing: {relative_path}")
        if artifacts[relative_path] != sha256_path(path):
            raise FixtureGenerationError(f"Blender fixture SHA mismatch: {relative_path}")


def validate_current_repository_contract(root: Path, repository_root: Path) -> None:
    repository_text = str(repository_root)
    if repository_text not in sys.path:
        sys.path.insert(0, repository_text)
    from packages.gpu_control_core.load_testing import (  # noqa: PLC0415
        load_fixture_manifest,
        validate_fixture_files,
    )

    fixtures = load_fixture_manifest(root / "fixtures.yaml")
    validate_fixture_files(fixtures, repository_root=repository_root)


def write_provenance(
    root: Path,
    *,
    frame_count: int,
    image_size: int,
    texture_size: int,
    blender_image: str,
    python_image: str,
    blender_script: Path,
) -> None:
    write_json_no_overwrite(
        root / "provenance.json",
        {
            "schema_version": SCHEMA_VERSION,
            "source_kind": "algorithmic-synthetic-only",
            "production_user_assets_read": False,
            "network_required": False,
            "frame_count": frame_count,
            "image_size": [image_size, image_size],
            "texture_size": [texture_size, texture_size],
            "blender_image": blender_image,
            "python_image": python_image,
            "generator_sha256": sha256_path(Path(__file__).resolve()),
            "blender_generator_sha256": sha256_path(blender_script.resolve()),
        },
    )


def write_checksums(root: Path) -> None:
    checksum_path = root / "SHA256SUMS"
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name not in {INCOMPLETE_MARKER, checksum_path.name}
    )
    lines = [f"{sha256_path(path)}  {path.relative_to(root).as_posix()}" for path in files]
    write_text_no_overwrite(checksum_path, "\n".join(lines) + "\n")


def verify_checksums(root: Path) -> None:
    checksum_path = root / "SHA256SUMS"
    declared: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or relative in declared:
            raise FixtureGenerationError("invalid SHA256SUMS entry")
        path = (root / relative).resolve()
        if not _is_within(path, root.resolve()) or not path.is_file():
            raise FixtureGenerationError(f"unsafe or missing checksum target: {relative}")
        if sha256_path(path) != digest:
            raise FixtureGenerationError(f"checksum mismatch: {relative}")
        declared[relative] = digest
    expected = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {INCOMPLETE_MARKER, "SHA256SUMS"}
    }
    if set(declared) != expected:
        raise FixtureGenerationError("SHA256SUMS does not cover the exact generated file set")


def generate(
    output: Path,
    repository_root: Path,
    *,
    frame_count: int,
    image_size: int,
    texture_size: int,
    blender_binary: Path,
    blender_script: Path,
    blender_image: str = PINNED_BLENDER_IMAGE,
    python_image: str = PINNED_PYTHON_IMAGE,
) -> Path:
    root, repository = prepare_output_root(output, repository_root)
    generate_raster_fixtures(
        root,
        frame_count=frame_count,
        image_size=image_size,
        texture_size=texture_size,
    )
    write_metadata_and_fixture_manifest(root)
    run_blender_generator(
        root,
        repository,
        blender_binary=blender_binary,
        blender_script=blender_script,
    )
    validate_raster_and_archive(root)
    validate_blender_receipt(root)
    validate_current_repository_contract(root, repository)
    write_provenance(
        root,
        frame_count=frame_count,
        image_size=image_size,
        texture_size=texture_size,
        blender_image=blender_image,
        python_image=python_image,
        blender_script=blender_script,
    )
    write_checksums(root)
    verify_checksums(root)
    (root / INCOMPLETE_MARKER).unlink()
    return root


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--image-size", type=int, default=768)
    parser.add_argument("--texture-size", type=int, default=512)
    parser.add_argument("--blender-binary", type=Path, default=Path("/opt/blender/blender"))
    parser.add_argument(
        "--blender-script",
        type=Path,
        default=Path(__file__).resolve().with_name(
            "generate_six_api_blender_fixtures.py"
        ),
    )
    parser.add_argument("--blender-image", default=PINNED_BLENDER_IMAGE)
    parser.add_argument("--python-image", default=PINNED_PYTHON_IMAGE)
    parsed = parser.parse_args()
    if not 1 <= parsed.frames <= 300:
        parser.error("--frames must be between 1 and 300")
    if not 64 <= parsed.image_size <= 4096:
        parser.error("--image-size must be between 64 and 4096")
    if not 64 <= parsed.texture_size <= 4096:
        parser.error("--texture-size must be between 64 and 4096")
    return parsed


def main() -> None:
    args = arguments()
    root = generate(
        args.output,
        args.repository_root,
        frame_count=args.frames,
        image_size=args.image_size,
        texture_size=args.texture_size,
        blender_binary=args.blender_binary,
        blender_script=args.blender_script,
        blender_image=args.blender_image,
        python_image=args.python_image,
    )
    print(json.dumps({"status": "GENERATED", "fixture_root": str(root)}, sort_keys=True))


if __name__ == "__main__":
    main()
