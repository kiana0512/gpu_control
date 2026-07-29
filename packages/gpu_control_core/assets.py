import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_ASSET_EXTENSIONS = frozenset({".fbx", ".obj", ".glb", ".gltf", ".blend"})
SUPPORTED_REFERENCE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class UVUnwrapOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal[1024, 2048, 4096, 8192] = 2048
    padding_px: int = Field(default=10, ge=2, le=128)
    hard_edge_angle_degrees: float = Field(default=75.0, ge=1.0, le=179.0)
    hidden_axis: Literal["x+", "x-", "y+", "y-", "z+", "z-", "auto"] = "auto"
    texel_density_mode: Literal["uniform"] = "uniform"
    qa_profile: Literal["pbr-v1"] = "pbr-v1"


class AssetCreateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_asset_id: str = Field(min_length=1, max_length=128)
    options: UVUnwrapOptions = Field(default_factory=UVUnwrapOptions)

    @field_validator("external_asset_id")
    @classmethod
    def valid_external_id(cls, value: str) -> str:
        if not ASSET_ID_PATTERN.fullmatch(value):
            raise ValueError("external_asset_id contains unsupported characters")
        return value


class RetopologyAuditOptions(BaseModel):
    """Object selectors for the immutable retopology comparison audit."""

    model_config = ConfigDict(extra="forbid")

    high_object: str = Field(min_length=1, max_length=128)
    reference_object: str = Field(min_length=1, max_length=128)
    low_object: str = Field(min_length=1, max_length=128)
    require_closed: bool = False

    @field_validator("high_object", "reference_object", "low_object")
    @classmethod
    def valid_object_name(cls, value: str) -> str:
        if "\x00" in value or any(character in value for character in ("/", "\\")):
            raise ValueError("Blender object names cannot contain paths or NUL bytes")
        return value


class RetopologyAuditMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_asset_id: str = Field(min_length=1, max_length=128)
    options: RetopologyAuditOptions

    @field_validator("external_asset_id")
    @classmethod
    def valid_external_id(cls, value: str) -> str:
        if not ASSET_ID_PATTERN.fullmatch(value):
            raise ValueError("external_asset_id contains unsupported characters")
        return value


class RetopologyReferenceView(BaseModel):
    """One immutable external visual reference attached to a retopology job."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    view: Literal["front", "side", "top", "perspective", "detail", "other"]
    label: str | None = Field(default=None, max_length=128)

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value: str) -> str:
        return validate_reference_image_filename(value)


class RetopologyProcessOptions(RetopologyAuditOptions):
    """Deterministic generation, topology-style and evidence controls."""

    generated_low_object: str = Field(
        default="GPUCTRL_Retopo_v001", min_length=1, max_length=128
    )
    algorithm: Literal["agent", "quadriflow", "cleanup_existing"] = "agent"
    topology_style: Literal["quad_dominant", "preserve_existing"] = "quad_dominant"
    target_faces: int | None = Field(default=None, ge=50, le=5_000_000)
    preserve_sharp: bool = True
    preserve_boundary: bool = True
    render_resolution: Literal[256, 512, 1024] = 512
    max_repair_rounds: int = Field(default=1, ge=0, le=2)

    @field_validator("generated_low_object")
    @classmethod
    def valid_generated_object_name(cls, value: str) -> str:
        value = RetopologyAuditOptions.valid_object_name(value)
        if not re.fullmatch(r".+_v\d{3}", value):
            raise ValueError("generated_low_object must end with a version such as _v001")
        return value


class RetopologyProcessMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_asset_id: str = Field(min_length=1, max_length=128)
    options: RetopologyProcessOptions
    reference_views: list[RetopologyReferenceView] = Field(default_factory=list, max_length=32)
    user_request: str | None = Field(default=None, max_length=4000)

    @field_validator("external_asset_id")
    @classmethod
    def valid_external_id(cls, value: str) -> str:
        if not ASSET_ID_PATTERN.fullmatch(value):
            raise ValueError("external_asset_id contains unsupported characters")
        return value

    @field_validator("reference_views")
    @classmethod
    def unique_reference_filenames(
        cls, value: list[RetopologyReferenceView]
    ) -> list[RetopologyReferenceView]:
        names = [item.filename for item in value]
        if len(names) != len(set(names)):
            raise ValueError("reference view filenames must be unique")
        return value


def validate_asset_filename(filename: str) -> str:
    """Return a basename safe for storage and Blender import."""
    if not filename or filename != Path(filename).name or "\x00" in filename:
        raise ValueError("asset filename must be a safe basename")
    if Path(filename).suffix.lower() not in SUPPORTED_ASSET_EXTENSIONS:
        raise ValueError("supported asset formats are FBX, OBJ, GLB, GLTF and BLEND")
    if len(filename.encode("utf-8")) > 255:
        raise ValueError("asset filename is too long")
    return filename


def validate_reference_image_filename(filename: str) -> str:
    """Return a safe basename for an immutable visual reference image."""
    if not filename or filename != Path(filename).name or "\x00" in filename:
        raise ValueError("reference image filename must be a safe basename")
    if Path(filename).suffix.lower() not in SUPPORTED_REFERENCE_IMAGE_EXTENSIONS:
        raise ValueError("reference images must be PNG, JPG, JPEG or WEBP")
    if len(filename.encode("utf-8")) > 255:
        raise ValueError("reference image filename is too long")
    return filename


def asset_request_hash(metadata: AssetCreateMetadata, input_sha256: str) -> str:
    payload = {
        "external_asset_id": metadata.external_asset_id,
        "options": metadata.options.model_dump(mode="json"),
        "input_sha256": input_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def uv_process_request_hash(metadata: AssetCreateMetadata, input_sha256: str) -> str:
    payload = {
        "job_type": "UV_PROCESS_V2",
        "external_asset_id": metadata.external_asset_id,
        "options": metadata.options.model_dump(mode="json"),
        "input_sha256": input_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def retopology_audit_request_hash(
    metadata: RetopologyAuditMetadata, input_sha256: str
) -> str:
    payload = {
        "job_type": "RETOPOLOGY_AUDIT",
        "external_asset_id": metadata.external_asset_id,
        "options": metadata.options.model_dump(mode="json"),
        "input_sha256": input_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def retopology_process_request_hash(
    metadata: RetopologyProcessMetadata,
    project_sha256: str,
    reference_sha256: dict[str, str],
) -> str:
    payload = {
        "job_type": "RETOPOLOGY_PROCESS_V1",
        "external_asset_id": metadata.external_asset_id,
        "options": metadata.options.model_dump(mode="json"),
        "reference_views": [item.model_dump(mode="json") for item in metadata.reference_views],
        "user_request": metadata.user_request,
        "project_sha256": project_sha256,
        "reference_sha256": dict(sorted(reference_sha256.items())),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def lease_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
