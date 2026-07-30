import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from packages.gpu_control_core.load_testing import API_NAMES, load_fixture_manifest
from scripts import generate_six_api_blender_fixtures as blender_generator
from scripts import generate_six_api_synthetic_fixtures as generator


def test_output_must_be_fresh_and_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(generator.FixtureGenerationError, match="outside the repository"):
        generator.prepare_output_root(repository / "fixtures", repository)

    output = tmp_path / "external" / "synthetic-six-api-v1"
    root, resolved_repository = generator.prepare_output_root(output, repository)
    assert root == output.resolve()
    assert resolved_repository == repository.resolve()
    assert (root / generator.INCOMPLETE_MARKER).is_file()

    with pytest.raises(generator.FixtureGenerationError, match="refusing overwrite"):
        generator.prepare_output_root(output, repository)


def test_blender_helper_accepts_only_owned_incomplete_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "external" / "synthetic-six-api-v1"
    output.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="refusing overwrite"):
        blender_generator.prepare_output_root(
            output, repository, allow_incomplete_root=True
        )

    (output / blender_generator.INCOMPLETE_MARKER).write_text(
        "schema_version=synthetic-six-api-v1\n", encoding="utf-8"
    )
    root, _ = blender_generator.prepare_output_root(
        output, repository, allow_incomplete_root=True
    )
    assert root == output.resolve()

    occupied = output / "uv" / "asset.blend"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"existing")
    with pytest.raises(RuntimeError, match="refusing to overwrite Blender fixture"):
        blender_generator.prepare_output_root(
            output, repository, allow_incomplete_root=True
        )


def _fake_blender_generation(
    root: Path,
    _: Path,
    *,
    blender_binary: Path,
    blender_script: Path,
) -> None:
    assert blender_binary == Path("/offline-test/blender")
    assert blender_script.is_file()
    artifacts: dict[str, str] = {}
    for index, relative in enumerate(generator.REQUIRED_BLENDER_FILES):
        path = root / relative
        generator.write_bytes_no_overwrite(path, f"synthetic-3d-{index}".encode())
        artifacts[relative] = generator.sha256_path(path)
    generator.write_json_no_overwrite(
        root / "blender_validation.json",
        {
            "schema_version": "synthetic_blender_fixtures.v1",
            "passed": True,
            "blender_version": "offline-test",
            "checks": {"object_names": True, "uv_layers": True},
            "artifacts": artifacts,
        },
    )


def test_complete_synthetic_fixture_contract_without_network_or_source_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[2]
    output = tmp_path / "synthetic-six-api-v1"
    blender_script = repository / "scripts" / "generate_six_api_blender_fixtures.py"
    monkeypatch.setattr(generator, "run_blender_generator", _fake_blender_generation)

    generated = generator.generate(
        output,
        repository,
        frame_count=3,
        image_size=64,
        texture_size=64,
        blender_binary=Path("/offline-test/blender"),
        blender_script=blender_script,
        blender_image="offline-blender@sha256:test",
        python_image="offline-python@sha256:test",
    )

    assert generated == output.resolve()
    assert not (generated / generator.INCOMPLETE_MARKER).exists()
    generator.validate_raster_and_archive(generated)
    generator.validate_blender_receipt(generated)
    generator.verify_checksums(generated)

    fixtures = load_fixture_manifest(generated / "fixtures.yaml")
    assert set(fixtures.entries) == set(API_NAMES)
    with zipfile.ZipFile(generated / "imageclip" / "frames.zip") as archive:
        assert len(archive.infolist()) == 3
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
    manifest = json.loads((generated / "imageclip" / "manifest.json").read_text("utf-8"))
    assert [frame["ordinal"] for frame in manifest["frames"]] == [0, 1, 2]
    assert manifest["parameters"] == {}

    for path in (
        generated / "roughness" / "material.png",
        generated / "retopology" / "front.png",
        generated / "bake" / "base_color.png",
        generated / "bake" / "roughness.png",
        generated / "bake" / "metallic.png",
    ):
        with Image.open(path) as image:
            image.verify()

    process_metadata = json.loads(
        (generated / "retopology" / "process.metadata.json").read_text("utf-8")
    )
    assert process_metadata["options"]["algorithm"] == "cleanup_existing"
    assert process_metadata["options"]["generated_low_object"].endswith("_v001")
    assert [item["filename"] for item in process_metadata["reference_views"]] == [
        "front.png",
        "side.png",
    ]
    bake_metadata = json.loads((generated / "bake" / "metadata.json").read_text("utf-8"))
    assert bake_metadata["options"] == {
        "profile": "li3d-pbr-full-v2",
        "resolution": 512,
        "texture_cache_mb": 8192,
    }
    provenance = json.loads((generated / "provenance.json").read_text("utf-8"))
    assert provenance["source_kind"] == "algorithmic-synthetic-only"
    assert provenance["production_user_assets_read"] is False
    assert provenance["network_required"] is False

    with pytest.raises(generator.FixtureGenerationError, match="refusing overwrite"):
        generator.generate(
            output,
            repository,
            frame_count=1,
            image_size=64,
            texture_size=64,
            blender_binary=Path("/offline-test/blender"),
            blender_script=blender_script,
        )


def test_checksum_verifier_fails_closed_after_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[2]
    output = tmp_path / "synthetic-six-api-v1"
    blender_script = repository / "scripts" / "generate_six_api_blender_fixtures.py"
    monkeypatch.setattr(generator, "run_blender_generator", _fake_blender_generation)
    generated = generator.generate(
        output,
        repository,
        frame_count=1,
        image_size=64,
        texture_size=64,
        blender_binary=Path("/offline-test/blender"),
        blender_script=blender_script,
    )
    with (generated / "roughness" / "material.png").open("ab") as target:
        target.write(b"tampered")
    with pytest.raises(generator.FixtureGenerationError, match="checksum mismatch"):
        generator.verify_checksums(generated)
