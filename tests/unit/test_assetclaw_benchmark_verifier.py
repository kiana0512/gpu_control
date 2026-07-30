import hashlib
import io
import json
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from scripts.validate_assetclaw_v4_1_benchmarks import (
    EXPECTED_BUNDLES,
    EXPECTED_SESSION_ID,
    BenchmarkValidationError,
    failure_report,
    validate_frozen_benchmarks,
    write_reports,
)


def png_bytes(width: int = 2, height: int = 3) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 255)).save(output, "PNG")
    return output.getvalue()


FrameMutator = Callable[[list[dict[str, Any]]], None]
IndexMutator = Callable[[dict[str, Any]], None]
MetadataMutator = Callable[[dict[str, Any]], None]


def build_session(
    root: Path,
    *,
    target: str = "B1",
    frame_mutator: FrameMutator | None = None,
    index_mutator: IndexMutator | None = None,
    metadata_mutator: MetadataMutator | None = None,
    zip_mode: str = "normal",
    missing_file: str | None = None,
    extra_file: bool = False,
) -> tuple[Path, str]:
    root.mkdir()
    content = png_bytes()
    content_sha256 = hashlib.sha256(content).hexdigest()
    bundles: dict[str, Any] = {}
    for name, frame_count in EXPECTED_BUNDLES.items():
        bundle = root / name
        bundle.mkdir()
        frames = [
            {
                "ordinal": ordinal,
                "relative_path": f"frames/{ordinal:06d}.png",
                "size_bytes": len(content),
                "sha256": content_sha256,
                "width": 2,
                "height": 3,
                "pixels": 6,
            }
            for ordinal in range(frame_count)
        ]
        if name == target and frame_mutator is not None:
            frame_mutator(frames)
        manifest = {
            "schema_version": "1.0",
            "frames": frames,
        }
        manifest_path = bundle / "input_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        canonical_manifest = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        canonical_manifest_sha256 = hashlib.sha256(canonical_manifest).hexdigest()
        archive_path = bundle / "input.zip"
        compression = zipfile.ZIP_DEFLATED if name == target and zip_mode == "deflated" else zipfile.ZIP_STORED
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
            with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
                for position, frame in enumerate(frames):
                    if name == target and zip_mode == "missing" and position == len(frames) - 1:
                        continue
                    archive.writestr(frame["relative_path"], content)
                    if name == target and zip_mode == "duplicate" and position == 0:
                        archive.writestr(frame["relative_path"], content)
                if name == target and zip_mode == "extra":
                    archive.writestr("frames/unexpected.png", content)
        archive_raw = archive_path.read_bytes()
        archive_sha256 = hashlib.sha256(archive_raw).hexdigest()
        metadata = {
            "schema_version": "1.0",
            "acceptance_session_id": EXPECTED_SESSION_ID,
            "bundle_id": name,
            "frame_count": frame_count,
            "total_pixels": frame_count * 6,
            "input_zip_bytes": len(archive_raw),
            "input_zip_sha256": archive_sha256,
            "canonical_manifest_sha256": canonical_manifest_sha256,
        }
        if name == target and metadata_mutator is not None:
            metadata_mutator(metadata)
        (bundle / "benchmark_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if name == target and missing_file is not None:
            (bundle / missing_file).unlink()
        if name == target and extra_file:
            (bundle / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        bundles[name] = {
            "bundle_id": name,
            "frame_count": frame_count,
            "total_pixels": frame_count * 6,
            "input_zip": {
                "path": f"{name}/input.zip",
                "size_bytes": len(archive_raw),
                "sha256": archive_sha256,
            },
            "input_manifest": {
                "path": f"{name}/input_manifest.json",
                "canonical_sha256": canonical_manifest_sha256,
            },
            "benchmark_metadata": {
                "path": f"{name}/benchmark_metadata.json",
            },
        }
    index = {
        "schema_version": "assetclaw-v4.1-frozen-input.v1",
        "acceptance_session_id": EXPECTED_SESSION_ID,
        "bundles": bundles,
    }
    if index_mutator is not None:
        index_mutator(index)
    index_path = root / "bundle_index.json"
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root, hashlib.sha256(index_path.read_bytes()).hexdigest()


def assert_failure(
    root: Path,
    expected_sha256: str,
    code: str,
) -> BenchmarkValidationError:
    with pytest.raises(BenchmarkValidationError) as captured:
        validate_frozen_benchmarks(root, expected_sha256)
    assert captured.value.code == code
    return captured.value


def test_complete_session_validates_from_root_and_index_and_writes_safe_reports(
    tmp_path: Path,
) -> None:
    root, expected_sha256 = build_session(tmp_path / "session")

    report = validate_frozen_benchmarks(root, expected_sha256.upper())
    report_from_file = validate_frozen_benchmarks(root / "bundle_index.json", expected_sha256)

    assert report["result"] == "PASS"
    assert report["acceptance_session_id"] == EXPECTED_SESSION_ID
    assert report["bundle_count"] == 6
    assert report["frame_records_verified"] == sum(EXPECTED_BUNDLES.values())
    assert report_from_file["bundle_index"] == report["bundle_index"]
    assert report["verification_boundary"] == {
        "mode": "offline_read_only",
        "network_access": False,
        "production_access": False,
        "gpu_tasks_created": False,
        "archives_extracted": False,
    }

    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    write_reports(
        report,
        session_root=root,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    assert json.loads(json_path.read_text(encoding="utf-8"))["result"] == "PASS"
    assert "| B300 | 300 |" in markdown_path.read_text(encoding="utf-8")
    assert not (root / "report.json").exists()
    with pytest.raises(BenchmarkValidationError, match="overwrite"):
        write_reports(
            report,
            session_root=root,
            json_path=json_path,
            markdown_path=None,
        )


def test_expected_index_sha_is_mandatory_64_hex_and_must_match(tmp_path: Path) -> None:
    root, expected_sha256 = build_session(tmp_path / "session")

    error = assert_failure(root, expected_sha256 + "7", "SHA256_INVALID")
    assert "65 characters" in str(error)
    assert_failure(root, "0" * 64, "INDEX_SHA256_MISMATCH")


@pytest.mark.parametrize("missing_file", sorted({"input.zip", "input_manifest.json", "benchmark_metadata.json"}))
def test_required_bundle_file_must_exist(tmp_path: Path, missing_file: str) -> None:
    root, expected_sha256 = build_session(
        tmp_path / "session",
        missing_file=missing_file,
    )
    assert_failure(root, expected_sha256, "BUNDLE_FILE_SET_MISMATCH")


def test_extra_bundle_file_is_rejected(tmp_path: Path) -> None:
    root, expected_sha256 = build_session(tmp_path / "session", extra_file=True)
    assert_failure(root, expected_sha256, "BUNDLE_FILE_SET_MISMATCH")


@pytest.mark.parametrize(
    ("target", "mutation", "expected_code"),
    [
        (
            "B1",
            lambda frames: frames[0].__setitem__("relative_path", "../escape.png"),
            "FRAME_PATH_INVALID",
        ),
        (
            "B6",
            lambda frames: frames[1].__setitem__("relative_path", frames[0]["relative_path"]),
            "FRAME_PATH_DUPLICATE",
        ),
        (
            "B6",
            lambda frames: frames.__setitem__(slice(0, 2), [frames[1], frames[0]]),
            "FRAME_ORDINAL_INVALID",
        ),
    ],
)
def test_manifest_rejects_traversal_duplicates_and_out_of_order_ordinals(
    tmp_path: Path,
    target: str,
    mutation: FrameMutator,
    expected_code: str,
) -> None:
    root, expected_sha256 = build_session(
        tmp_path / "session",
        target=target,
        frame_mutator=mutation,
    )
    assert_failure(root, expected_sha256, expected_code)


@pytest.mark.parametrize(
    ("zip_mode", "expected_code"),
    [
        ("extra", "ARCHIVE_FRAME_SET_MISMATCH"),
        ("missing", "ARCHIVE_FRAME_SET_MISMATCH"),
        ("duplicate", "ARCHIVE_PATH_DUPLICATE"),
        ("deflated", "ARCHIVE_ENTRY_INVALID"),
    ],
)
def test_archive_rejects_extra_missing_duplicate_and_compressed_frames(
    tmp_path: Path,
    zip_mode: str,
    expected_code: str,
) -> None:
    root, expected_sha256 = build_session(tmp_path / "session", zip_mode=zip_mode)
    assert_failure(root, expected_sha256, expected_code)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda frames: frames[0].__setitem__("sha256", "0" * 64), "FRAME_CONTENT_MISMATCH"),
        (lambda frames: frames[0].__setitem__("width", 3), "FRAME_DIMENSIONS_INVALID"),
        (lambda frames: frames[0].__setitem__("pixels", 5), "FRAME_DIMENSIONS_INVALID"),
        (
            lambda frames: frames[0].update({"width": 3, "height": 2, "pixels": 6}),
            "FRAME_DIMENSIONS_MISMATCH",
        ),
    ],
)
def test_frame_hash_and_dimensions_are_verified_against_actual_png(
    tmp_path: Path,
    mutation: FrameMutator,
    expected_code: str,
) -> None:
    root, expected_sha256 = build_session(
        tmp_path / "session",
        frame_mutator=mutation,
    )
    assert_failure(root, expected_sha256, expected_code)


def test_canonical_manifest_sha_and_metadata_must_match_index(tmp_path: Path) -> None:
    def break_manifest_digest(index: dict[str, Any]) -> None:
        index["bundles"]["B1"]["input_manifest"]["canonical_sha256"] = "0" * 64

    root, expected_sha256 = build_session(
        tmp_path / "canonical",
        index_mutator=break_manifest_digest,
    )
    assert_failure(root, expected_sha256, "MANIFEST_CANONICAL_SHA256_MISMATCH")

    def break_metadata(metadata: dict[str, Any]) -> None:
        metadata["input_zip_bytes"] += 1

    other_root, other_sha256 = build_session(
        tmp_path / "metadata",
        metadata_mutator=break_metadata,
    )
    assert_failure(other_root, other_sha256, "METADATA_MISMATCH")


def test_failure_report_is_bounded_and_reports_never_write_into_input(tmp_path: Path) -> None:
    root, expected_sha256 = build_session(tmp_path / "session")
    error = assert_failure(root, expected_sha256 + "7", "SHA256_INVALID")
    report = failure_report(error)

    assert report["result"] == "FAIL"
    assert report["error"]["code"] == "SHA256_INVALID"
    with pytest.raises(BenchmarkValidationError, match="outside"):
        write_reports(
            report,
            session_root=root,
            json_path=root / "report.json",
            markdown_path=None,
        )


def test_duplicate_json_keys_and_extra_bundle_directory_fail_closed(tmp_path: Path) -> None:
    root, _ = build_session(tmp_path / "duplicate-json")
    index_path = root / "bundle_index.json"
    index_path.write_text(
        '{"acceptance_session_id":"v4_1-20260730-r1",'
        '"acceptance_session_id":"v4_1-20260730-r1","bundles":{}}',
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(index_path.read_bytes()).hexdigest()
    assert_failure(root, expected_sha256, "JSON_DUPLICATE_KEY")

    other_root, other_sha256 = build_session(tmp_path / "extra-bundle")
    (other_root / "B999").mkdir()
    assert_failure(other_root, other_sha256, "BUNDLE_DIRECTORY_SET_MISMATCH")


def test_non_finite_json_and_symlink_bundle_fail_closed(tmp_path: Path) -> None:
    root, _ = build_session(tmp_path / "non-finite")
    index_path = root / "bundle_index.json"
    index_path.write_text(
        '{"acceptance_session_id":"v4_1-20260730-r1","bundles":{},"value":NaN}',
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(index_path.read_bytes()).hexdigest()
    assert_failure(root, expected_sha256, "JSON_INVALID")

    other_root, other_sha256 = build_session(tmp_path / "symlink-bundle")
    (other_root / "B999").symlink_to(other_root / "B1", target_is_directory=True)
    assert_failure(other_root, other_sha256, "BUNDLE_DIRECTORY_INVALID")
