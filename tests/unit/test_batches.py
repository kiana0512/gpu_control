import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from packages.gpu_control_core.batches import (
    ArchiveFrame,
    BatchContractError,
    build_result_archive,
    extract_batch_archive,
    parse_batch_manifest,
)
from packages.gpu_control_core.settings import Settings


def png_bytes(mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (4, 3), (255, 0, 0, 128) if mode == "RGBA" else "red").save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def manifest_for(name: str, payload: bytes) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "external_batch_id": "animation-001",
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": [
            {
                "ordinal": 0,
                "relative_path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }


def stored_zip(path: Path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, payload)


def test_manifest_requires_canonical_contiguous_paths(tmp_path: Path) -> None:
    settings = Settings(environment="test", job_root=tmp_path / "jobs")
    payload = png_bytes()
    valid, canonical, digest = parse_batch_manifest(
        json.dumps(manifest_for("scene/0001.png", payload)), settings
    )
    assert valid.frames[0].ordinal == 0
    assert hashlib.sha256(canonical).hexdigest() == digest

    unsafe = manifest_for("../0001.png", payload)
    with pytest.raises(BatchContractError, match="unsafe segment") as raised:
        parse_batch_manifest(json.dumps(unsafe), settings)
    assert raised.value.code == "MANIFEST_INVALID"

    discontinuous = manifest_for("0001.png", payload)
    discontinuous["frames"][0]["ordinal"] = 1  # type: ignore[index]
    with pytest.raises(BatchContractError, match="contiguous"):
        parse_batch_manifest(json.dumps(discontinuous), settings)


def test_archive_is_exact_stored_and_hash_verified(tmp_path: Path) -> None:
    settings = Settings(environment="test", job_root=tmp_path / "jobs")
    payload = png_bytes()
    parsed, _, _ = parse_batch_manifest(
        json.dumps(manifest_for("scene/0001.png", payload)), settings
    )
    archive_path = tmp_path / "input.zip"
    stored_zip(archive_path, "scene/0001.png", payload)
    frames = extract_batch_archive(archive_path, tmp_path / "extracted", parsed, settings)
    assert [(frame.ordinal, frame.output_relative_path) for frame in frames] == [
        (0, "scene/0001.png")
    ]
    assert (tmp_path / "extracted/scene/0001.png").read_bytes() == payload

    stored_zip(archive_path, "scene/extra.png", payload)
    with pytest.raises(BatchContractError) as mismatch:
        extract_batch_archive(archive_path, tmp_path / "mismatch", parsed, settings)
    assert mismatch.value.code == "FRAME_SET_MISMATCH"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("scene/0001.png", payload)
    with pytest.raises(BatchContractError, match="ZIP_STORED"):
        extract_batch_archive(archive_path, tmp_path / "compressed", parsed, settings)


def test_result_archive_preserves_paths_order_hashes_and_alpha(tmp_path: Path) -> None:
    output = tmp_path / "rgba.png"
    output.write_bytes(png_bytes("RGBA"))
    output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    built = build_result_archive(
        "batch-id",
        "animation-001",
        tmp_path / "batch",
        [
            ArchiveFrame(
                ordinal=0,
                input_relative_path="scene/0001.jpg",
                output_relative_path="scene/0001.png",
                input_sha256="a" * 64,
                output_path=output,
                expected_output_sha256=output_sha,
                job_id="job-id",
                node_id="worker-3090-a",
                attempts=1,
            )
        ],
    )
    assert built.sha256 == hashlib.sha256(built.path.read_bytes()).hexdigest()
    with zipfile.ZipFile(built.path) as archive:
        assert archive.namelist() == ["manifest.json", "results/scene/0001.png"]
        result_manifest = json.loads(archive.read("manifest.json"))
    assert result_manifest["total"] == 1
    assert result_manifest["items"][0]["output_sha256"] == output_sha

    rgb = tmp_path / "rgb.png"
    rgb.write_bytes(png_bytes("RGB"))
    with pytest.raises(BatchContractError) as no_alpha:
        build_result_archive(
            "batch-id-2",
            "animation-002",
            tmp_path / "batch-2",
            [
                ArchiveFrame(
                    ordinal=0,
                    input_relative_path="0001.png",
                    output_relative_path="0001.png",
                    input_sha256="b" * 64,
                    output_path=rgb,
                    expected_output_sha256=hashlib.sha256(rgb.read_bytes()).hexdigest(),
                    job_id="job-id-2",
                    node_id=None,
                    attempts=1,
                )
            ],
        )
    assert no_alpha.value.code == "OUTPUT_ALPHA_MISSING"
