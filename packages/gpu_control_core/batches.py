import hashlib
import json
import os
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import (
    TERMINAL_BATCH_STATUSES,
    BatchItemStatus,
    BatchStatus,
    JobStatus,
    Priority,
)
from .models import BatchEvent, Job, JobBatch, JobBatchItem, WorkflowVersion
from .repository import transition_job
from .settings import Settings
from .storage import LocalJobStorage, StorageError, inspect_image
from .workflow import WorkflowManifest, render_workflow


class BatchContractError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        ordinal: int | None = None,
        relative_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ordinal = ordinal
        self.relative_path = relative_path


class BatchFrameManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=0)
    relative_path: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def relative_path_is_canonical(cls, value: str) -> str:
        return canonical_relative_path(value)


class BatchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    external_batch_id: str = Field(min_length=1, max_length=128)
    failure_policy: Literal["all_or_nothing"]
    output_naming: Literal["preserve_stem_png"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    frames: list[BatchFrameManifest] = Field(min_length=1)

    @field_validator("external_batch_id")
    @classmethod
    def external_id_is_printable(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("external_batch_id must be printable and trimmed")
        return value

    @model_validator(mode="after")
    def frames_are_contiguous_and_unique(self) -> "BatchManifest":
        if [frame.ordinal for frame in self.frames] != list(range(len(self.frames))):
            raise ValueError("frame ordinals must be ordered and contiguous from zero")
        input_paths: set[str] = set()
        output_paths: set[str] = set()
        for frame in self.frames:
            input_key = frame.relative_path.casefold()
            output_key = rgba_output_path(frame.relative_path).casefold()
            if input_key in input_paths:
                raise ValueError("normalized input paths must be unique")
            if output_key in output_paths:
                raise ValueError("normalized output paths must be unique")
            input_paths.add(input_key)
            output_paths.add(output_key)
        return self


@dataclass(frozen=True)
class ExtractedFrame:
    ordinal: int
    input_relative_path: str
    output_relative_path: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    image_format: str


@dataclass(frozen=True)
class ArchiveFrame:
    ordinal: int
    input_relative_path: str
    output_relative_path: str
    input_sha256: str
    output_path: Path
    expected_output_sha256: str
    job_id: str
    node_id: str | None
    attempts: int


@dataclass(frozen=True)
class BuiltBatchArchive:
    path: Path
    size_bytes: int
    sha256: str
    manifest: dict[str, Any]


_BATCH_TRANSITIONS: dict[BatchStatus, frozenset[BatchStatus]] = {
    BatchStatus.VALIDATING: frozenset(
        {BatchStatus.QUEUED, BatchStatus.FAILED, BatchStatus.CANCELLING}
    ),
    BatchStatus.QUEUED: frozenset(
        {BatchStatus.RUNNING, BatchStatus.CANCELLING, BatchStatus.FAILED}
    ),
    BatchStatus.RUNNING: frozenset(
        {BatchStatus.ASSEMBLING, BatchStatus.CANCELLING, BatchStatus.FAILED}
    ),
    BatchStatus.ASSEMBLING: frozenset(
        {BatchStatus.SUCCEEDED, BatchStatus.CANCELLING, BatchStatus.FAILED}
    ),
    BatchStatus.CANCELLING: frozenset({BatchStatus.CANCELLED, BatchStatus.FAILED}),
    BatchStatus.SUCCEEDED: frozenset(),
    BatchStatus.CANCELLED: frozenset(),
    BatchStatus.FAILED: frozenset(),
}


async def transition_batch(
    session: AsyncSession,
    batch: JobBatch,
    target: BatchStatus,
    event: str,
    details: dict[str, Any] | None = None,
) -> None:
    current = BatchStatus(batch.status)
    if target != current and target not in _BATCH_TRANSITIONS[current]:
        raise ValueError(f"illegal batch transition {current.value} -> {target.value}")
    sequence = await session.scalar(
        select(func.coalesce(func.max(BatchEvent.sequence), 0)).where(
            BatchEvent.batch_id == batch.id
        )
    )
    now = datetime.now(UTC)
    batch.status = target.value
    batch.updated_at = now
    if target == BatchStatus.RUNNING and batch.started_at is None:
        batch.started_at = now
    if target in TERMINAL_BATCH_STATUSES:
        batch.finished_at = now
    session.add(
        BatchEvent(
            batch_id=batch.id,
            sequence=int(sequence or 0) + 1,
            previous_status=current.value,
            status=target.value,
            event=event,
            details=details or {},
        )
    )


def canonical_relative_path(value: str) -> str:
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("relative path must use Unicode NFC")
    if "\\" in value or "\x00" in value:
        raise ValueError("relative path contains a forbidden character")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError("relative path must not be absolute")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relative path contains an unsafe segment")
    canonical = path.as_posix()
    if canonical != value:
        raise ValueError("relative path is not canonical")
    return canonical


def rgba_output_path(input_relative_path: str) -> str:
    path = PurePosixPath(input_relative_path)
    return path.with_suffix(".png").as_posix()


def parse_batch_manifest(raw: str, settings: Settings) -> tuple[BatchManifest, bytes, str]:
    try:
        value = json.loads(raw)
        manifest = BatchManifest.model_validate(value)
    except Exception as exc:
        raise BatchContractError("MANIFEST_INVALID", str(exc)) from exc
    if len(manifest.frames) > settings.batch_max_frames:
        raise BatchContractError(
            "BATCH_TOO_LARGE",
            f"frame count exceeds limit {settings.batch_max_frames}",
        )
    if any(frame.size_bytes > settings.batch_max_frame_bytes for frame in manifest.frames):
        raise BatchContractError(
            "BATCH_TOO_LARGE",
            f"a frame exceeds {settings.batch_max_frame_bytes} bytes",
        )
    if sum(frame.size_bytes for frame in manifest.frames) > settings.batch_max_uncompressed_bytes:
        raise BatchContractError(
            "BATCH_TOO_LARGE",
            f"uncompressed batch exceeds {settings.batch_max_uncompressed_bytes} bytes",
        )
    canonical = json.dumps(
        manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return manifest, canonical, digest


def extract_batch_archive(
    archive_path: Path,
    input_root: Path,
    manifest: BatchManifest,
    settings: Settings,
) -> list[ExtractedFrame]:
    expected = {frame.relative_path: frame for frame in manifest.frames}
    extracted: list[ExtractedFrame] = []
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise BatchContractError("ARCHIVE_ENTRY_INVALID", "archive is not a valid ZIP") from exc
    with archive:
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            try:
                name = canonical_relative_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
            except ValueError as exc:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID", str(exc), relative_path=info.filename
                ) from exc
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in {0, stat.S_IFDIR}:
                    raise BatchContractError(
                        "ARCHIVE_ENTRY_INVALID", "invalid directory entry", relative_path=name
                    )
                continue
            if kind not in {0, stat.S_IFREG}:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID",
                    "links and special files are forbidden",
                    relative_path=name,
                )
            if info.flag_bits & 0x1:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID", "encrypted entries are forbidden", relative_path=name
                )
            if info.compress_type != zipfile.ZIP_STORED:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID",
                    "v1 requires ZIP_STORED entries",
                    relative_path=name,
                )
            if name in entries:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID", "duplicate archive path", relative_path=name
                )
            entries[name] = info
        if set(entries) != set(expected):
            missing = sorted(set(expected) - set(entries))[:5]
            extra = sorted(set(entries) - set(expected))[:5]
            raise BatchContractError(
                "FRAME_SET_MISMATCH", f"archive/manifest mismatch; missing={missing}, extra={extra}"
            )
        input_root_resolved = input_root.resolve()
        for frame in manifest.frames:
            info = entries[frame.relative_path]
            if info.file_size != frame.size_bytes:
                raise BatchContractError(
                    "FRAME_HASH_MISMATCH",
                    "frame size does not match manifest",
                    ordinal=frame.ordinal,
                    relative_path=frame.relative_path,
                )
            destination = (input_root / frame.relative_path).resolve()
            if input_root_resolved not in destination.parents:
                raise BatchContractError(
                    "ARCHIVE_ENTRY_INVALID",
                    "archive path escapes input root",
                    ordinal=frame.ordinal,
                    relative_path=frame.relative_path,
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as target, archive.open(info, "r") as source:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > settings.batch_max_frame_bytes:
                            raise BatchContractError(
                                "BATCH_TOO_LARGE",
                                "frame exceeds configured limit",
                                ordinal=frame.ordinal,
                                relative_path=frame.relative_path,
                            )
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if written != frame.size_bytes or digest.hexdigest() != frame.sha256:
                    raise BatchContractError(
                        "FRAME_HASH_MISMATCH",
                        "frame content does not match manifest",
                        ordinal=frame.ordinal,
                        relative_path=frame.relative_path,
                    )
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            try:
                width, height, image_format = inspect_image(destination, settings.max_image_pixels)
            except StorageError as exc:
                raise BatchContractError(
                    "IMAGE_INVALID",
                    str(exc),
                    ordinal=frame.ordinal,
                    relative_path=frame.relative_path,
                ) from exc
            extracted.append(
                ExtractedFrame(
                    ordinal=frame.ordinal,
                    input_relative_path=frame.relative_path,
                    output_relative_path=rgba_output_path(frame.relative_path),
                    size_bytes=written,
                    sha256=digest.hexdigest(),
                    width=width,
                    height=height,
                    image_format=image_format,
                )
            )
    return extracted


def workflow_manifest_from_row(workflow: WorkflowVersion) -> WorkflowManifest:
    return WorkflowManifest(
        workflow_key=workflow.workflow_key,
        version=workflow.version,
        template_file="database",
        parameter_schema=workflow.parameter_schema,
        bindings={str(key): str(value) for key, value in workflow.bindings.items()},
        allowed_class_types=frozenset(str(value) for value in workflow.allowed_class_types),
        required_models=tuple(str(value) for value in workflow.required_models),
        required_custom_nodes=tuple(str(value) for value in workflow.required_custom_nodes),
        min_vram_mb=workflow.min_vram_mb,
        timeout_seconds=workflow.timeout_seconds,
        node_labels={str(key): str(value) for key, value in workflow.node_labels.items()},
        output_nodes=tuple(str(value) for value in workflow.output_nodes),
        enabled=workflow.enabled,
    )


async def materialize_batch_item(
    session: AsyncSession,
    storage: LocalJobStorage,
    settings: Settings,
    batch: JobBatch,
    item: JobBatchItem,
    workflow: WorkflowVersion,
) -> Job:
    job_id = str(uuid.uuid4())
    staging = storage.create_staging_layout(job_id)
    source = Path(batch.batch_dir) / "input" / item.input_relative_path
    destination = staging / "input" / f"image-{PurePosixPath(item.input_relative_path).name}"
    try:
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        parameters = dict(batch.parameters or {})
        parameters["image_filename"] = f"{job_id}/{destination.name}"
        rendered = render_workflow(workflow_manifest_from_row(workflow), workflow.template, parameters)
        storage.atomic_json(
            staging / "input" / "image.metadata.json",
            {
                "filename": destination.name,
                "source_relative_path": item.input_relative_path,
                "size_bytes": item.input_size_bytes,
                "sha256": item.input_sha256,
                "width": item.width,
                "height": item.height,
                "format": item.image_format,
                "batch_id": batch.id,
                "batch_item_id": item.id,
                "ordinal": item.ordinal,
            },
        )
        storage.atomic_json(staging / "workflow" / "template.snapshot.json", workflow.template)
        storage.atomic_json(staging / "workflow" / "rendered.api.json", rendered)
        storage.atomic_json(
            staging / "request.sanitized.json",
            {
                "batch_id": batch.id,
                "batch_item_id": item.id,
                "ordinal": item.ordinal,
                "input_relative_path": item.input_relative_path,
                "input_sha256": item.input_sha256,
            },
        )
        root = storage.promote_staging(staging, job_id)
    except Exception:
        storage.remove_tree(staging)
        raise
    now = datetime.now(UTC)
    job = Job(
        id=job_id,
        tenant_id=batch.tenant_id,
        workflow_key=batch.workflow_key,
        workflow_version=batch.workflow_version,
        status=JobStatus.RECEIVED.value,
        priority=Priority.BATCH.value,
        parameters=parameters,
        request_hash=hashlib.sha256(
            f"{batch.request_hash}:{item.ordinal}:{item.input_sha256}".encode()
        ).hexdigest(),
        request_id=batch.request_id,
        trace_id=batch.trace_id,
        job_dir=str(root),
        batch_id=batch.id,
        batch_item_id=item.id,
        max_attempts=settings.job_max_attempts,
        created_at=now,
    )
    session.add(job)
    await session.flush()
    item.job_id = job.id
    item.status = BatchItemStatus.QUEUED.value
    item.updated_at = now
    batch.last_materialized_at = now
    await transition_job(session, job, JobStatus.VALIDATING, "batch.item_validated")
    await transition_job(session, job, JobStatus.QUEUED, "batch.item_queued")
    return job


def build_result_archive(
    batch_id: str,
    external_batch_id: str,
    batch_dir: Path,
    frames: list[ArchiveFrame],
) -> BuiltBatchArchive:
    items: list[dict[str, Any]] = []
    for frame in sorted(frames, key=lambda value: value.ordinal):
        digest = hashlib.sha256()
        with frame.output_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != frame.expected_output_sha256:
            raise BatchContractError(
                "OUTPUT_HASH_MISMATCH",
                "stored output SHA-256 changed before assembly",
                ordinal=frame.ordinal,
                relative_path=frame.output_relative_path,
            )
        try:
            with Image.open(frame.output_path) as image:
                image.load()
                if (image.format or "").upper() != "PNG" or "A" not in image.getbands():
                    raise BatchContractError(
                        "OUTPUT_ALPHA_MISSING",
                        "output is not a PNG with an alpha channel",
                        ordinal=frame.ordinal,
                        relative_path=frame.output_relative_path,
                    )
        except (UnidentifiedImageError, OSError) as exc:
            raise BatchContractError(
                "OUTPUT_IMAGE_INVALID",
                "output cannot be decoded",
                ordinal=frame.ordinal,
                relative_path=frame.output_relative_path,
            ) from exc
        items.append(
            {
                "ordinal": frame.ordinal,
                "input_relative_path": frame.input_relative_path,
                "input_sha256": frame.input_sha256,
                "output_relative_path": frame.output_relative_path,
                "output_sha256": frame.expected_output_sha256,
                "status": BatchItemStatus.SUCCEEDED.value,
                "job_id": frame.job_id,
                "node_id": frame.node_id,
                "attempts": frame.attempts,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "external_batch_id": external_batch_id,
        "total": len(items),
        "items": items,
    }
    output_root = batch_dir / "output"
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / f"{batch_id}-rgba.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".result-", suffix=".zip", dir=output_root)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
            for frame in sorted(frames, key=lambda value: value.ordinal):
                archive.write(frame.output_path, f"results/{frame.output_relative_path}")
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256()
    size = 0
    with destination.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return BuiltBatchArchive(destination, size, digest.hexdigest(), manifest)
