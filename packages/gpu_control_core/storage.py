import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from PIL import Image, UnidentifiedImageError


class StorageError(RuntimeError):
    """A storage path or write violated the local storage contract."""


def safe_filename(name: str) -> str:
    candidate = PurePath(name).name
    if candidate != name or candidate in {"", ".", ".."} or "\x00" in candidate:
        raise StorageError("unsafe filename")
    return candidate


class LocalJobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def job_dir(self, job_id: str, now: datetime | None = None) -> Path:
        if not job_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in job_id
        ):
            raise StorageError("invalid job id")
        current = now or datetime.now(UTC)
        path = (self.root / current.strftime("%Y/%m/%d") / job_id).resolve()
        if self.root not in path.parents:
            raise StorageError("job path escapes storage root")
        return path

    def create_layout(self, job_id: str, now: datetime | None = None) -> Path:
        root = self.job_dir(job_id, now)
        for name in ("input", "workflow", "comfy", "output", "callback", "diagnostics"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    def create_staging_layout(self, job_id: str) -> Path:
        root = (self.root / ".staging" / job_id).resolve()
        if self.root not in root.parents:
            raise StorageError("staging path escapes storage root")
        if root.exists():
            raise StorageError("staging job already exists")
        for name in ("input", "workflow", "comfy", "output", "callback", "diagnostics"):
            (root / name).mkdir(parents=True, exist_ok=False)
        return root

    def batch_dir(self, batch_id: str, now: datetime | None = None) -> Path:
        if not batch_id or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in batch_id
        ):
            raise StorageError("invalid batch id")
        current = now or datetime.now(UTC)
        path = (self.root / "batches" / current.strftime("%Y/%m/%d") / batch_id).resolve()
        if self.root not in path.parents:
            raise StorageError("batch path escapes storage root")
        return path

    def create_batch_staging_layout(self, batch_id: str) -> Path:
        root = (self.root / ".batch-staging" / batch_id).resolve()
        if self.root not in root.parents:
            raise StorageError("batch staging path escapes storage root")
        if root.exists():
            raise StorageError("staging batch already exists")
        for name in ("input", "output", "diagnostics"):
            (root / name).mkdir(parents=True, exist_ok=False)
        return root

    def promote_batch_staging(
        self, staging: Path, batch_id: str, now: datetime | None = None
    ) -> Path:
        source = staging.resolve()
        staging_root = (self.root / ".batch-staging").resolve()
        if staging_root not in source.parents:
            raise StorageError("invalid batch staging path")
        destination = self.batch_dir(batch_id, now)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise StorageError("batch directory already exists")
        os.replace(source, destination)
        return destination

    def promote_staging(self, staging: Path, job_id: str, now: datetime | None = None) -> Path:
        source = staging.resolve()
        staging_root = (self.root / ".staging").resolve()
        if staging_root not in source.parents:
            raise StorageError("invalid staging path")
        destination = self.job_dir(job_id, now)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise StorageError("job directory already exists")
        os.replace(source, destination)
        return destination

    def remove_tree(self, path: Path) -> None:
        target = path.resolve()
        if target == self.root or self.root not in target.parents:
            raise StorageError("refusing to remove path outside storage root")
        shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def atomic_json(path: Path, value: Any, private: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            if private:
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    async def stream_to_file(
        chunks: AsyncIterator[bytes], destination: Path, max_bytes: int
    ) -> tuple[int, str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        total = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as handle:
                async for chunk in chunks:
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageError("upload exceeds configured size limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            return total, digest.hexdigest()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def inspect_image(path: Path, max_pixels: int) -> tuple[int, int, str]:
    """Validate a decoded image and return dimensions/normalized format."""
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
            if image_format not in {"JPEG", "PNG", "WEBP"}:
                raise StorageError("image format must be JPEG, PNG or WEBP")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise StorageError("image dimensions exceed configured pixel limit")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise StorageError("uploaded file is not a valid image") from exc
    return width, height, image_format
