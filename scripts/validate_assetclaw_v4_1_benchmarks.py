#!/usr/bin/env python3
"""Offline, read-only verification for the frozen AssetClaw V4.1 benchmark input.

This command never calls a service, opens a network connection, extracts an
archive, or creates a GPU job.  It treats the supplied session as immutable and
fails closed on an unknown, ambiguous, incomplete, or inconsistent input.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

EXPECTED_SESSION_ID = "v4_1-20260730-r1"
EXPECTED_BUNDLES = {
    "B1": 1,
    "B6": 6,
    "B30": 30,
    "B64": 64,
    "B97": 97,
    "B300": 300,
}
REQUIRED_BUNDLE_FILES = frozenset(
    {"input.zip", "input_manifest.json", "benchmark_metadata.json"}
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
BUNDLE_DIRECTORY_PATTERN = re.compile(r"^B[0-9]+$")
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_FRAME_PIXELS = 50_000_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024


class BenchmarkValidationError(ValueError):
    """Stable, reportable verification failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        bundle: str | None = None,
        relative_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.bundle = bundle
        self.relative_path = relative_path

    def evidence(self) -> dict[str, str]:
        result = {"code": self.code, "message": _safe_text(str(self))}
        if self.bundle is not None:
            result["bundle"] = self.bundle
        if self.relative_path is not None:
            result["relative_path"] = _safe_text(self.relative_path)
        return result


@dataclass(frozen=True)
class BundleDeclaration:
    name: str
    frame_count: int
    total_pixels: int
    archive_size_bytes: int
    archive_sha256: str
    canonical_manifest_sha256: str


@dataclass(frozen=True)
class FrameDeclaration:
    ordinal: int
    relative_path: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    pixels: int


def _safe_text(value: str) -> str:
    """Keep reports single-line and free of terminal control characters."""

    return "".join(character if character.isprintable() else "?" for character in value).replace(
        "\r", "?"
    ).replace("\n", "?")[:2000]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise BenchmarkValidationError(
                        "ARCHIVE_TOO_LARGE", f"archive exceeds {MAX_ARCHIVE_BYTES} bytes"
                    )
                digest.update(chunk)
    except BenchmarkValidationError:
        raise
    except OSError as exc:
        raise BenchmarkValidationError("FILE_READ_FAILED", f"cannot read {path.name}: {exc}") from exc
    return size, digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkValidationError(
                "JSON_DUPLICATE_KEY", f"JSON object contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise BenchmarkValidationError("JSON_INVALID", f"JSON contains non-finite number {value!r}")


def _json_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BenchmarkValidationError("FILE_READ_FAILED", f"cannot stat {label}: {exc}") from exc
    if size < 2 or size > MAX_JSON_BYTES:
        raise BenchmarkValidationError(
            "JSON_SIZE_INVALID", f"{label} must be between 2 and {MAX_JSON_BYTES} bytes"
        )
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_json_constant,
        )
    except BenchmarkValidationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkValidationError("JSON_INVALID", f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkValidationError("JSON_INVALID", f"{label} must be a JSON object")
    return value, raw


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _one_alias(value: dict[str, Any], aliases: tuple[str, ...], label: str) -> Any:
    present = [name for name in aliases if name in value]
    if len(present) != 1:
        raise BenchmarkValidationError(
            "FIELD_MISSING_OR_AMBIGUOUS",
            f"{label} requires exactly one of {', '.join(aliases)}; found {present}",
        )
    return value[present[0]]


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkValidationError("FIELD_TYPE_INVALID", f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise BenchmarkValidationError(
            "FIELD_VALUE_INVALID", f"{label} must be greater than or equal to {minimum}"
        )
    return int(value)


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        length = len(value) if isinstance(value, str) else None
        suffix = f"; received {length} characters" if length is not None else ""
        raise BenchmarkValidationError(
            "SHA256_INVALID", f"{label} must be exactly 64 hexadecimal characters{suffix}"
        )
    return value.lower()


def _session_id(value: dict[str, Any], label: str) -> str:
    session_id = _one_alias(value, ("acceptance_session_id", "session_id"), label)
    if session_id != EXPECTED_SESSION_ID:
        raise BenchmarkValidationError(
            "SESSION_ID_MISMATCH",
            f"{label} session must be {EXPECTED_SESSION_ID!r}; received {session_id!r}",
        )
    return EXPECTED_SESSION_ID


def _canonical_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise BenchmarkValidationError(
            "FRAME_PATH_INVALID", f"{label} must be a non-empty path of at most 1024 characters"
        )
    if value != unicodedata.normalize("NFC", value):
        raise BenchmarkValidationError("FRAME_PATH_INVALID", f"{label} must use Unicode NFC")
    if "\\" in value or "\x00" in value or any(ord(character) < 32 for character in value):
        raise BenchmarkValidationError(
            "FRAME_PATH_INVALID", f"{label} contains a forbidden character"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.endswith("/")
        or value != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or any(len(part.encode("utf-8")) > 255 for part in path.parts)
    ):
        raise BenchmarkValidationError(
            "FRAME_PATH_INVALID", f"{label} is not a canonical safe relative path"
        )
    return path.as_posix()


def _declared_path(container: Any, default: str, label: str) -> None:
    if container is None:
        return
    candidate: Any
    if isinstance(container, str):
        candidate = container
    elif isinstance(container, dict):
        candidate = container.get("path", default)
    else:
        raise BenchmarkValidationError(
            "FIELD_TYPE_INVALID", f"{label} must be a path string or object"
        )
    if _canonical_relative_path(candidate, label) != default:
        raise BenchmarkValidationError(
            "BUNDLE_PATH_MISMATCH", f"{label} must be exactly {default!r}"
        )


def _nested_or_flat(
    record: dict[str, Any],
    container_name: str,
    nested_aliases: tuple[str, ...],
    flat_aliases: tuple[str, ...],
    label: str,
) -> Any:
    container = record.get(container_name)
    nested_present: list[str] = []
    if isinstance(container, dict):
        nested_present = [name for name in nested_aliases if name in container]
    flat_present = [name for name in flat_aliases if name in record]
    if len(nested_present) + len(flat_present) != 1:
        raise BenchmarkValidationError(
            "FIELD_MISSING_OR_AMBIGUOUS",
            f"{label} requires one declaration; nested={nested_present}, flat={flat_present}",
        )
    if nested_present:
        if not isinstance(container, dict):
            raise BenchmarkValidationError("FIELD_TYPE_INVALID", f"{container_name} must be an object")
        return container[nested_present[0]]
    return record[flat_present[0]]


def _bundle_declaration(name: str, record: Any) -> BundleDeclaration:
    if not isinstance(record, dict):
        raise BenchmarkValidationError("FIELD_TYPE_INVALID", f"bundle {name} must be an object")
    declared_name = record.get("bundle_id", record.get("name", name))
    if declared_name != name:
        raise BenchmarkValidationError(
            "BUNDLE_ID_MISMATCH", f"bundle key {name!r} declares {declared_name!r}"
        )
    frame_count = _positive_int(
        _one_alias(record, ("frame_count", "frames_count"), f"{name}.frame_count"),
        f"{name}.frame_count",
    )
    total_pixels = _positive_int(
        _one_alias(record, ("total_pixels", "input_pixels_total"), f"{name}.total_pixels"),
        f"{name}.total_pixels",
    )
    _declared_path(record.get("input_zip"), f"{name}/input.zip", f"{name}.input_zip.path")
    _declared_path(
        record.get("input_manifest"),
        f"{name}/input_manifest.json",
        f"{name}.input_manifest.path",
    )
    _declared_path(
        record.get("benchmark_metadata"),
        f"{name}/benchmark_metadata.json",
        f"{name}.benchmark_metadata.path",
    )
    archive_size_bytes = _positive_int(
        _nested_or_flat(
            record,
            "input_zip",
            ("size_bytes", "bytes"),
            ("input_zip_bytes", "archive_size_bytes"),
            f"{name}.input_zip.size_bytes",
        ),
        f"{name}.input_zip.size_bytes",
    )
    archive_sha256 = _sha256(
        _nested_or_flat(
            record,
            "input_zip",
            ("sha256",),
            ("input_zip_sha256", "archive_sha256"),
            f"{name}.input_zip.sha256",
        ),
        f"{name}.input_zip.sha256",
    )
    canonical_manifest_sha256 = _sha256(
        _nested_or_flat(
            record,
            "input_manifest",
            ("canonical_sha256", "canonical_manifest_sha256"),
            ("canonical_manifest_sha256", "input_manifest_canonical_sha256"),
            f"{name}.input_manifest.canonical_sha256",
        ),
        f"{name}.input_manifest.canonical_sha256",
    )
    return BundleDeclaration(
        name=name,
        frame_count=frame_count,
        total_pixels=total_pixels,
        archive_size_bytes=archive_size_bytes,
        archive_sha256=archive_sha256,
        canonical_manifest_sha256=canonical_manifest_sha256,
    )


def _bundle_records(index: dict[str, Any]) -> dict[str, BundleDeclaration]:
    raw = index.get("bundles")
    records: dict[str, Any] = {}
    if isinstance(raw, dict):
        records = dict(raw)
    elif isinstance(raw, list):
        for position, record in enumerate(raw):
            if not isinstance(record, dict):
                raise BenchmarkValidationError(
                    "FIELD_TYPE_INVALID", f"bundles[{position}] must be an object"
                )
            name = _one_alias(record, ("bundle_id", "name"), f"bundles[{position}].id")
            if not isinstance(name, str) or name in records:
                raise BenchmarkValidationError(
                    "BUNDLE_ID_INVALID", f"bundles[{position}] has an invalid or duplicate ID"
                )
            records[name] = record
    else:
        raise BenchmarkValidationError("FIELD_TYPE_INVALID", "bundle index needs bundles object")
    expected = set(EXPECTED_BUNDLES)
    actual = set(records)
    if actual != expected:
        raise BenchmarkValidationError(
            "BUNDLE_SET_MISMATCH",
            f"bundle index mismatch; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}",
        )
    declarations = {name: _bundle_declaration(name, records[name]) for name in EXPECTED_BUNDLES}
    for name, expected_frames in EXPECTED_BUNDLES.items():
        if declarations[name].frame_count != expected_frames:
            raise BenchmarkValidationError(
                "BUNDLE_FRAME_COUNT_MISMATCH",
                f"{name} must declare {expected_frames} frames; found {declarations[name].frame_count}",
                bundle=name,
            )
    return declarations


def _frames(manifest: dict[str, Any], bundle: BundleDeclaration) -> list[FrameDeclaration]:
    raw_frames = manifest.get("frames")
    if not isinstance(raw_frames, list):
        raise BenchmarkValidationError(
            "MANIFEST_FRAMES_INVALID", "input manifest frames must be a list", bundle=bundle.name
        )
    if len(raw_frames) != bundle.frame_count:
        raise BenchmarkValidationError(
            "BUNDLE_FRAME_COUNT_MISMATCH",
            f"manifest has {len(raw_frames)} frames; expected {bundle.frame_count}",
            bundle=bundle.name,
        )
    parsed: list[FrameDeclaration] = []
    paths: set[str] = set()
    for position, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            raise BenchmarkValidationError(
                "MANIFEST_FRAME_INVALID", f"frames[{position}] must be an object", bundle=bundle.name
            )
        ordinal = _positive_int(raw.get("ordinal"), f"frames[{position}].ordinal", allow_zero=True)
        if ordinal != position:
            raise BenchmarkValidationError(
                "FRAME_ORDINAL_INVALID",
                f"frames[{position}] has ordinal {ordinal}; ordinals must be ordered 0..N-1",
                bundle=bundle.name,
            )
        relative_path = _canonical_relative_path(
            raw.get("relative_path"), f"frames[{position}].relative_path"
        )
        if relative_path in paths:
            raise BenchmarkValidationError(
                "FRAME_PATH_DUPLICATE",
                "input manifest contains a duplicate frame path",
                bundle=bundle.name,
                relative_path=relative_path,
            )
        paths.add(relative_path)
        size_bytes = _positive_int(raw.get("size_bytes"), f"frames[{position}].size_bytes")
        if size_bytes > MAX_FRAME_BYTES:
            raise BenchmarkValidationError(
                "FRAME_TOO_LARGE",
                f"frame exceeds {MAX_FRAME_BYTES} bytes",
                bundle=bundle.name,
                relative_path=relative_path,
            )
        sha256 = _sha256(raw.get("sha256"), f"frames[{position}].sha256")
        width = _positive_int(raw.get("width"), f"frames[{position}].width")
        height = _positive_int(raw.get("height"), f"frames[{position}].height")
        pixels = _positive_int(raw.get("pixels"), f"frames[{position}].pixels")
        if width * height != pixels or pixels > MAX_FRAME_PIXELS:
            raise BenchmarkValidationError(
                "FRAME_DIMENSIONS_INVALID",
                f"frame dimensions {width}x{height} do not match pixels={pixels}",
                bundle=bundle.name,
                relative_path=relative_path,
            )
        parsed.append(
            FrameDeclaration(
                ordinal=ordinal,
                relative_path=relative_path,
                size_bytes=size_bytes,
                sha256=sha256,
                width=width,
                height=height,
                pixels=pixels,
            )
        )
    actual_pixels = sum(frame.pixels for frame in parsed)
    if actual_pixels != bundle.total_pixels:
        raise BenchmarkValidationError(
            "BUNDLE_PIXELS_MISMATCH",
            f"manifest pixels total {actual_pixels}; index declares {bundle.total_pixels}",
            bundle=bundle.name,
        )
    return parsed


def _metadata_matches(
    metadata: dict[str, Any],
    bundle: BundleDeclaration,
) -> None:
    _session_id(metadata, f"{bundle.name} metadata")
    bundle_id = _one_alias(metadata, ("bundle_id", "name"), f"{bundle.name}.metadata.bundle_id")
    if bundle_id != bundle.name:
        raise BenchmarkValidationError(
            "BUNDLE_ID_MISMATCH",
            f"metadata declares bundle {bundle_id!r}",
            bundle=bundle.name,
        )
    comparisons = (
        (
            "frame_count",
            _positive_int(
                _one_alias(
                    metadata,
                    ("frame_count", "frames_count"),
                    f"{bundle.name}.metadata.frame_count",
                ),
                f"{bundle.name}.metadata.frame_count",
            ),
            bundle.frame_count,
        ),
        (
            "total_pixels",
            _positive_int(
                _one_alias(
                    metadata,
                    ("total_pixels", "input_pixels_total"),
                    f"{bundle.name}.metadata.total_pixels",
                ),
                f"{bundle.name}.metadata.total_pixels",
            ),
            bundle.total_pixels,
        ),
        (
            "input_zip_bytes",
            _positive_int(
                _nested_or_flat(
                    metadata,
                    "input_zip",
                    ("size_bytes", "bytes"),
                    ("input_zip_bytes", "archive_size_bytes"),
                    f"{bundle.name}.metadata.input_zip_bytes",
                ),
                f"{bundle.name}.metadata.input_zip_bytes",
            ),
            bundle.archive_size_bytes,
        ),
        (
            "input_zip_sha256",
            _sha256(
                _nested_or_flat(
                    metadata,
                    "input_zip",
                    ("sha256",),
                    ("input_zip_sha256", "archive_sha256"),
                    f"{bundle.name}.metadata.input_zip_sha256",
                ),
                f"{bundle.name}.metadata.input_zip_sha256",
            ),
            bundle.archive_sha256,
        ),
        (
            "canonical_manifest_sha256",
            _sha256(
                _nested_or_flat(
                    metadata,
                    "input_manifest",
                    ("canonical_sha256", "canonical_manifest_sha256"),
                    ("canonical_manifest_sha256", "input_manifest_canonical_sha256"),
                    f"{bundle.name}.metadata.canonical_manifest_sha256",
                ),
                f"{bundle.name}.metadata.canonical_manifest_sha256",
            ),
            bundle.canonical_manifest_sha256,
        ),
    )
    for field, actual, expected in comparisons:
        if actual != expected:
            raise BenchmarkValidationError(
                "METADATA_MISMATCH",
                f"metadata {field}={actual!r}; index declares {expected!r}",
                bundle=bundle.name,
            )


def _verify_archive(
    path: Path,
    bundle: BundleDeclaration,
    frames: list[FrameDeclaration],
) -> list[dict[str, Any]]:
    actual_size, actual_sha256 = _sha256_file(path)
    if actual_size != bundle.archive_size_bytes:
        raise BenchmarkValidationError(
            "ARCHIVE_SIZE_MISMATCH",
            f"archive size {actual_size}; index declares {bundle.archive_size_bytes}",
            bundle=bundle.name,
        )
    if actual_sha256 != bundle.archive_sha256:
        raise BenchmarkValidationError(
            "ARCHIVE_SHA256_MISMATCH",
            f"archive SHA-256 {actual_sha256}; index declares {bundle.archive_sha256}",
            bundle=bundle.name,
        )
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BenchmarkValidationError(
            "ARCHIVE_INVALID", f"input.zip is not a valid ZIP: {exc}", bundle=bundle.name
        ) from exc
    expected = {frame.relative_path: frame for frame in frames}
    evidence: list[dict[str, Any]] = []
    with archive:
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            candidate = info.filename.rstrip("/") if info.is_dir() else info.filename
            try:
                name = _canonical_relative_path(candidate, "ZIP entry")
            except BenchmarkValidationError as exc:
                raise BenchmarkValidationError(
                    exc.code,
                    str(exc),
                    bundle=bundle.name,
                    relative_path=info.filename,
                ) from exc
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in {0, stat.S_IFDIR}:
                    raise BenchmarkValidationError(
                        "ARCHIVE_ENTRY_INVALID",
                        "ZIP directory entry has an invalid file type",
                        bundle=bundle.name,
                        relative_path=name,
                    )
                continue
            if kind not in {0, stat.S_IFREG} or info.flag_bits & 0x1:
                raise BenchmarkValidationError(
                    "ARCHIVE_ENTRY_INVALID",
                    "ZIP links, special files, and encrypted entries are forbidden",
                    bundle=bundle.name,
                    relative_path=name,
                )
            if info.compress_type != zipfile.ZIP_STORED:
                raise BenchmarkValidationError(
                    "ARCHIVE_ENTRY_INVALID",
                    "input.zip must use ZIP_STORED for every frame",
                    bundle=bundle.name,
                    relative_path=name,
                )
            if name in entries:
                raise BenchmarkValidationError(
                    "ARCHIVE_PATH_DUPLICATE",
                    "input.zip contains a duplicate path",
                    bundle=bundle.name,
                    relative_path=name,
                )
            entries[name] = info
        if set(entries) != set(expected):
            raise BenchmarkValidationError(
                "ARCHIVE_FRAME_SET_MISMATCH",
                f"archive/manifest mismatch; missing={sorted(set(expected) - set(entries))[:10]}, "
                f"extra={sorted(set(entries) - set(expected))[:10]}",
                bundle=bundle.name,
            )
        for frame in frames:
            info = entries[frame.relative_path]
            if info.file_size != frame.size_bytes or info.file_size > MAX_FRAME_BYTES:
                raise BenchmarkValidationError(
                    "FRAME_SIZE_MISMATCH",
                    f"ZIP size {info.file_size}; manifest declares {frame.size_bytes}",
                    bundle=bundle.name,
                    relative_path=frame.relative_path,
                )
            try:
                payload = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise BenchmarkValidationError(
                    "FRAME_READ_FAILED",
                    f"cannot read ZIP frame: {exc}",
                    bundle=bundle.name,
                    relative_path=frame.relative_path,
                ) from exc
            actual_sha256 = _sha256_bytes(payload)
            if len(payload) != frame.size_bytes or actual_sha256 != frame.sha256:
                raise BenchmarkValidationError(
                    "FRAME_CONTENT_MISMATCH",
                    "frame bytes do not match manifest size/SHA-256",
                    bundle=bundle.name,
                    relative_path=frame.relative_path,
                )
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(payload)) as image:
                        width, height = image.size
                        image_format = str(image.format or "").upper()
                        if width < 1 or height < 1 or width * height > MAX_FRAME_PIXELS:
                            raise BenchmarkValidationError(
                                "FRAME_DIMENSIONS_INVALID",
                                f"actual frame dimensions {width}x{height} exceed the safety limit",
                                bundle=bundle.name,
                                relative_path=frame.relative_path,
                            )
                        # Enforce the stricter 50 MP contract before decoding
                        # pixels; Pillow's own bomb threshold is higher.
                        image.load()
            except BenchmarkValidationError:
                raise
            except (
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
                UnidentifiedImageError,
                OSError,
            ) as exc:
                raise BenchmarkValidationError(
                    "FRAME_IMAGE_INVALID",
                    f"frame is not a safe decodable image: {exc}",
                    bundle=bundle.name,
                    relative_path=frame.relative_path,
                ) from exc
            if image_format != "PNG" or (width, height) != (frame.width, frame.height):
                raise BenchmarkValidationError(
                    "FRAME_DIMENSIONS_MISMATCH",
                    f"actual {image_format} {width}x{height}; manifest declares PNG "
                    f"{frame.width}x{frame.height}",
                    bundle=bundle.name,
                    relative_path=frame.relative_path,
                )
            evidence.append(
                {
                    "ordinal": frame.ordinal,
                    "relative_path": frame.relative_path,
                    "size_bytes": frame.size_bytes,
                    "sha256": frame.sha256,
                    "width": width,
                    "height": height,
                    "pixels": width * height,
                }
            )
    return evidence


def _bundle_directory(session_root: Path, bundle: str) -> Path:
    path = session_root / bundle
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkValidationError(
            "BUNDLE_DIRECTORY_INVALID", "bundle directory is missing or is a symlink", bundle=bundle
        )
    actual: set[str] = set()
    try:
        for item in path.iterdir():
            if item.is_symlink() or not item.is_file():
                raise BenchmarkValidationError(
                    "BUNDLE_FILE_INVALID",
                    "bundle entries must be regular files, never directories or symlinks",
                    bundle=bundle,
                    relative_path=item.name,
                )
            actual.add(item.name)
    except BenchmarkValidationError:
        raise
    except OSError as exc:
        raise BenchmarkValidationError(
            "BUNDLE_DIRECTORY_INVALID", f"cannot inspect bundle directory: {exc}", bundle=bundle
        ) from exc
    if actual != REQUIRED_BUNDLE_FILES:
        raise BenchmarkValidationError(
            "BUNDLE_FILE_SET_MISMATCH",
            f"bundle files mismatch; missing={sorted(REQUIRED_BUNDLE_FILES - actual)}, "
            f"extra={sorted(actual - REQUIRED_BUNDLE_FILES)}",
            bundle=bundle,
        )
    return path


def _input_paths(input_path: Path) -> tuple[Path, Path]:
    if input_path.is_symlink():
        raise BenchmarkValidationError("INPUT_PATH_INVALID", "input path must not be a symlink")
    if input_path.is_dir():
        session_root = input_path.resolve()
        index_path = session_root / "bundle_index.json"
    elif input_path.is_file() and input_path.name == "bundle_index.json":
        index_path = input_path.resolve()
        session_root = index_path.parent
    else:
        raise BenchmarkValidationError(
            "INPUT_PATH_INVALID", "input must be a session directory or bundle_index.json"
        )
    if index_path.is_symlink() or not index_path.is_file():
        raise BenchmarkValidationError(
            "INDEX_FILE_INVALID", "bundle_index.json is missing or is a symlink"
        )
    return session_root, index_path


def validate_frozen_benchmarks(
    input_path: Path,
    expected_index_sha256: str,
) -> dict[str, Any]:
    """Verify a complete immutable session without extracting or modifying it."""

    expected_digest = _sha256(expected_index_sha256, "expected index SHA-256")
    session_root, index_path = _input_paths(input_path)
    index, index_raw = _json_object(index_path, "bundle_index.json")
    actual_index_sha256 = _sha256_bytes(index_raw)
    if actual_index_sha256 != expected_digest:
        raise BenchmarkValidationError(
            "INDEX_SHA256_MISMATCH",
            f"bundle_index.json SHA-256 {actual_index_sha256}; expected {expected_digest}",
        )
    _session_id(index, "bundle index")
    declarations = _bundle_records(index)
    actual_bundle_directories: set[str] = set()
    for item in session_root.iterdir():
        if BUNDLE_DIRECTORY_PATTERN.fullmatch(item.name) is None:
            continue
        if item.is_symlink() or not item.is_dir():
            raise BenchmarkValidationError(
                "BUNDLE_DIRECTORY_INVALID",
                "B-prefixed session entries must be regular bundle directories",
                bundle=item.name,
            )
        actual_bundle_directories.add(item.name)
    if actual_bundle_directories != set(EXPECTED_BUNDLES):
        raise BenchmarkValidationError(
            "BUNDLE_DIRECTORY_SET_MISMATCH",
            f"bundle directories mismatch; missing={sorted(set(EXPECTED_BUNDLES) - actual_bundle_directories)}, "
            f"extra={sorted(actual_bundle_directories - set(EXPECTED_BUNDLES))}",
        )
    bundle_reports: list[dict[str, Any]] = []
    for name in EXPECTED_BUNDLES:
        declaration = declarations[name]
        bundle_path = _bundle_directory(session_root, name)
        manifest, _ = _json_object(bundle_path / "input_manifest.json", f"{name} manifest")
        canonical_manifest_sha256 = _sha256_bytes(_canonical_json(manifest))
        if canonical_manifest_sha256 != declaration.canonical_manifest_sha256:
            raise BenchmarkValidationError(
                "MANIFEST_CANONICAL_SHA256_MISMATCH",
                f"canonical manifest SHA-256 {canonical_manifest_sha256}; index declares "
                f"{declaration.canonical_manifest_sha256}",
                bundle=name,
            )
        frames = _frames(manifest, declaration)
        metadata, metadata_raw = _json_object(
            bundle_path / "benchmark_metadata.json", f"{name} metadata"
        )
        _metadata_matches(metadata, declaration)
        frame_evidence = _verify_archive(bundle_path / "input.zip", declaration, frames)
        bundle_reports.append(
            {
                "bundle_id": name,
                "frame_count": declaration.frame_count,
                "total_pixels": declaration.total_pixels,
                "input_zip_bytes": declaration.archive_size_bytes,
                "input_zip_sha256": declaration.archive_sha256,
                "canonical_manifest_sha256": canonical_manifest_sha256,
                "benchmark_metadata_sha256": _sha256_bytes(metadata_raw),
                "frames": frame_evidence,
            }
        )
    return {
        "schema_version": "gpu-control-assetclaw-v4.1-input-verification.v1",
        "result": "PASS",
        "verification_boundary": {
            "mode": "offline_read_only",
            "network_access": False,
            "production_access": False,
            "gpu_tasks_created": False,
            "archives_extracted": False,
        },
        "acceptance_session_id": EXPECTED_SESSION_ID,
        "bundle_index": {
            "relative_path": "bundle_index.json",
            "size_bytes": len(index_raw),
            "sha256": actual_index_sha256,
        },
        "bundle_count": len(bundle_reports),
        "frame_records_verified": sum(item["frame_count"] for item in bundle_reports),
        "bundles": bundle_reports,
    }


def failure_report(error: BenchmarkValidationError) -> dict[str, Any]:
    return {
        "schema_version": "gpu-control-assetclaw-v4.1-input-verification.v1",
        "result": "FAIL",
        "verification_boundary": {
            "mode": "offline_read_only",
            "network_access": False,
            "production_access": False,
            "gpu_tasks_created": False,
            "archives_extracted": False,
        },
        "acceptance_session_id": EXPECTED_SESSION_ID,
        "error": error.evidence(),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# AssetClaw V4.1 frozen benchmark verification",
        "",
        f"- Result: `{report['result']}`",
        f"- Acceptance session: `{EXPECTED_SESSION_ID}`",
        "- Boundary: offline, read-only, no production access, no GPU task creation",
        "",
    ]
    if report["result"] != "PASS":
        error = dict(report.get("error") or {})
        lines.extend(
            [
                "## Failure",
                "",
                f"- Code: `{_markdown_text(str(error.get('code', 'UNKNOWN')))}`",
                f"- Message: {_markdown_text(str(error.get('message', 'verification failed')))}",
            ]
        )
        if error.get("bundle"):
            lines.append(f"- Bundle: `{_markdown_text(str(error['bundle']))}`")
        if error.get("relative_path"):
            lines.append(f"- Path: `{_markdown_text(str(error['relative_path']))}`")
        return "\n".join(lines) + "\n"
    index = dict(report["bundle_index"])
    lines.extend(
        [
            "## Index",
            "",
            f"- SHA-256: `{index['sha256']}`",
            f"- Size: `{index['size_bytes']}` bytes",
            "",
            "## Bundles",
            "",
            "| Bundle | Frames | Pixels | ZIP bytes | ZIP SHA-256 | Manifest SHA-256 |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for raw in report["bundles"]:
        item = dict(raw)
        lines.append(
            f"| {item['bundle_id']} | {item['frame_count']} | {item['total_pixels']} | "
            f"{item['input_zip_bytes']} | `{item['input_zip_sha256']}` | "
            f"`{item['canonical_manifest_sha256']}` |"
        )
    return "\n".join(lines) + "\n"


def _markdown_text(value: str) -> str:
    return _safe_text(value).replace("`", "'").replace("|", "\\|")


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _report_destination(path: Path, session_root: Path) -> Path:
    if path.name in {"", ".", ".."}:
        raise BenchmarkValidationError("REPORT_PATH_INVALID", "report path needs a filename")
    parent = path.parent.resolve()
    destination = parent / path.name
    if not parent.is_dir() or parent.is_symlink():
        raise BenchmarkValidationError(
            "REPORT_PATH_INVALID", "report parent must be an existing regular directory"
        )
    if _path_within(destination, session_root.resolve()):
        raise BenchmarkValidationError(
            "REPORT_PATH_INVALID", "reports must remain outside the immutable input session"
        )
    if destination.exists() or destination.is_symlink():
        raise BenchmarkValidationError(
            "REPORT_PATH_EXISTS", f"refusing to overwrite existing report {destination.name!r}"
        )
    return destination


def _atomic_create(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as exc:
        raise BenchmarkValidationError(
            "REPORT_PATH_EXISTS", f"refusing to overwrite existing report {path.name!r}"
        ) from exc
    except OSError as exc:
        raise BenchmarkValidationError(
            "REPORT_WRITE_FAILED", f"could not create report {path.name!r}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def write_reports(
    report: dict[str, Any],
    *,
    session_root: Path,
    json_path: Path | None,
    markdown_path: Path | None,
) -> None:
    destinations: list[tuple[Path, bytes]] = []
    if json_path is not None:
        destinations.append(
            (
                _report_destination(json_path, session_root),
                (
                    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode("utf-8"),
            )
        )
    if markdown_path is not None:
        destinations.append(
            (
                _report_destination(markdown_path, session_root),
                markdown_report(report).encode("utf-8"),
            )
        )
    if len({path for path, _ in destinations}) != len(destinations):
        raise BenchmarkValidationError(
            "REPORT_PATH_INVALID", "JSON and Markdown reports need different paths"
        )
    for destination, payload in destinations:
        _atomic_create(destination, payload)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline read-only verification for AssetClaw session v4_1-20260730-r1; "
            "this command never sends requests or creates GPU tasks."
        )
    )
    parser.add_argument("input", type=Path, help="session root or bundle_index.json")
    parser.add_argument(
        "--expected-index-sha256",
        required=True,
        help="trusted, out-of-band 64-hex SHA-256 of the raw bundle_index.json",
    )
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        # Reject a malformed trust anchor (including the 65-character value in
        # the initial 07 handoff) before inspecting any untrusted input path.
        _sha256(args.expected_index_sha256, "expected index SHA-256")
        session_root, _ = _input_paths(args.input)
        report = validate_frozen_benchmarks(args.input, args.expected_index_sha256)
    except BenchmarkValidationError as exc:
        report = failure_report(exc)
        try:
            if "session_root" in locals():
                write_reports(
                    report,
                    session_root=session_root,
                    json_path=args.json_report,
                    markdown_path=args.markdown_report,
                )
        except BenchmarkValidationError as report_exc:
            print(f"report refused [{report_exc.code}]: {report_exc}", file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"verification failed [{exc.code}]: {exc}", file=sys.stderr)
        return 2
    try:
        write_reports(
            report,
            session_root=session_root,
            json_path=args.json_report,
            markdown_path=args.markdown_report,
        )
    except BenchmarkValidationError as exc:
        print(f"report refused [{exc.code}]: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("PASS: offline read-only verification only; no GPU task was created.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
