import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_ASSET_EXTENSIONS = frozenset({".fbx", ".obj", ".glb", ".gltf", ".blend"})
SUPPORTED_REFERENCE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
SUPPORTED_BAKER_EXTENSIONS = frozenset({".fbx", ".obj"})
SUPPORTED_BAKER_TEXTURE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".exr"}
)
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


class SubstanceBakeOptions(BaseModel):
    """Whitelisted Substance 3D Baker profiles exposed by GPU Control.

    Callers select a profile and bounded values only.  They can never submit a
    command, executable path or arbitrary CLI argument.
    """

    model_config = ConfigDict(extra="forbid")

    profile: Literal[
        "ao-self-v1",
        "normal-dx-v1",
        "pbr-core-v1",
        "li3d-pbr-full-v2",
    ]
    resolution: Literal[256, 512, 1024, 2048, 4096] = 2048
    texture_cache_mb: Literal[8192, 16384, 32768] = 32768


class SubstanceBakeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_asset_id: str = Field(min_length=1, max_length=128)
    options: SubstanceBakeOptions

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
    """Legacy V5 controls retained only for rollback and historical tests."""

    generated_low_object: str = Field(default="GPUCTRL_Retopo_v001", min_length=1, max_length=128)
    algorithm: Literal["agent", "quadriflow", "cleanup_existing"] = "agent"
    topology_style: Literal["mixed", "quad_dominant", "preserve_existing"] = "mixed"
    topology_mode: Literal["mixed", "quad_dominant"] = "mixed"
    target_faces: int | None = Field(default=None, ge=50, le=5_000_000)
    preserve_sharp: bool = True
    preserve_boundary: bool = True
    planar_reduction: bool = True
    planar_angle_threshold: float = Field(default=5.0, ge=0.1, le=45.0)
    preserve_hard_edges: bool = True
    preserve_components: bool = True
    allow_triangles: bool = True
    allow_ngons: bool = False
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
    reference_views: list[RetopologyReferenceView] = Field(default_factory=list, max_length=16)
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


class RetopologyV6ProcessOptions(BaseModel):
    """V6 high-only controls; polygon budgets are never accepted from users."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["agent"] = "agent"
    budget_mode: Literal["automatic"] = "automatic"
    topology_style: Literal["mixed_game_ready"] = "mixed_game_ready"
    preserve_source: Literal[True] = True
    preserve_sharp_edges: bool = True
    preserve_boundaries: bool = True
    delivery_profile: Literal[
        "next_gen_game_prop",
        "realtime_background_prop",
        "mobile_game_prop",
    ] = "next_gen_game_prop"


class RetopologyV6ProcessMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["6.0"] = "6.0"
    external_asset_id: str = Field(min_length=1, max_length=128)
    options: RetopologyV6ProcessOptions = Field(default_factory=RetopologyV6ProcessOptions)
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


RETOPOLOGY_V6_POLICY_SHA256 = "e7b24c93c11d550ac9fedd167ff23f9ddd70cba4db014caaf2e157cddeafb266"
RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256 = (
    "d86f218d2194bd6260a491da66f89b8954a72ef8e5309c0ff1062c639d8f6ec4"
)
RETOPOLOGY_DIRECT_V2_MAX_DIMENSION_RELATIVE_ERROR = 0.05


def retopology_coordinate_dimension_evidence_valid(payload: object) -> bool:
    """Validate the fail-closed Direct V2 high/low dimension evidence."""

    if not isinstance(payload, dict):
        return False
    limit = payload.get("maximum_dimension_relative_error")
    if (
        not isinstance(limit, int | float)
        or isinstance(limit, bool)
        or not math.isclose(
            float(limit),
            RETOPOLOGY_DIRECT_V2_MAX_DIMENSION_RELATIVE_ERROR,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return False
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return False
    for pair in pairs:
        if not isinstance(pair, dict):
            return False
        errors = pair.get("high_low_dimension_relative_error")
        maximum = pair.get("high_low_maximum_dimension_relative_error")
        pair_limit = pair.get("maximum_dimension_relative_error_limit")
        if (
            not isinstance(errors, list)
            or len(errors) != 3
            or not all(
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and 0.0 <= float(value) <= float(limit)
                for value in errors
            )
            or not isinstance(maximum, int | float)
            or isinstance(maximum, bool)
            or not math.isfinite(float(maximum))
            or not math.isclose(
                float(maximum),
                max(float(value) for value in errors),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not isinstance(pair_limit, int | float)
            or isinstance(pair_limit, bool)
            or not math.isclose(float(pair_limit), float(limit), rel_tol=0.0, abs_tol=1e-12)
        ):
            return False
    return True


_RETOPOLOGY_V5_IGNORED_OPTIONS = frozenset(
    {
        "target_faces",
        "high_object",
        "reference_object",
        "low_object",
        "generated_low_object",
        "bootstrap_mode",
        "topology_mode",
        "planar_reduction",
        "planar_angle_threshold",
        "preserve_hard_edges",
        "preserve_components",
        "allow_triangles",
        "allow_ngons",
        "render_resolution",
        "max_repair_rounds",
        "require_closed",
    }
)


def adapt_retopology_v6_metadata_json(
    metadata: str,
) -> tuple[RetopologyV6ProcessMetadata, list[str]]:
    """Canonicalize V6 metadata and safely absorb explicitly known V5 fields.

    The adapter never translates ``target_faces`` into an internal budget or
    generator parameter. Unknown fields remain rejected by Pydantic so this
    compatibility window cannot silently widen the production contract.
    """

    try:
        payload = json.loads(metadata)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("metadata must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("metadata must be one JSON object")
    raw_options = payload.get("options")
    if not isinstance(raw_options, dict):
        raise ValueError("metadata.options must be one JSON object")

    options: dict[str, Any] = dict(raw_options)
    ignored = sorted(key for key in options if key in _RETOPOLOGY_V5_IGNORED_OPTIONS)
    for key in ignored:
        options.pop(key, None)

    warnings: list[str] = []
    if "target_faces" in ignored:
        warnings.append("DEPRECATED_TARGET_FACES_IGNORED")
    if any(key != "target_faces" for key in ignored):
        warnings.append("DEPRECATED_RETOPOLOGY_FIELDS_IGNORED")

    legacy_algorithm = options.get("algorithm")
    if legacy_algorithm in {"quadriflow", "cleanup_existing"}:
        options["algorithm"] = "agent"
        warnings.append("DEPRECATED_RETOPOLOGY_ALGORITHM_IGNORED")
    legacy_style = options.get("topology_style")
    if legacy_style in {"mixed", "quad_dominant", "preserve_existing"}:
        options["topology_style"] = "mixed_game_ready"
        warnings.append("DEPRECATED_TOPOLOGY_STYLE_NORMALIZED")
    if "preserve_sharp" in options:
        options.setdefault("preserve_sharp_edges", options.pop("preserve_sharp"))
        warnings.append("DEPRECATED_PRESERVE_SHARP_NORMALIZED")
    if "preserve_boundary" in options:
        options.setdefault("preserve_boundaries", options.pop("preserve_boundary"))
        warnings.append("DEPRECATED_PRESERVE_BOUNDARY_NORMALIZED")

    api_version = payload.get("api_version")
    if api_version in {None, "4.0", "5.0"}:
        payload["api_version"] = "6.0"
        warnings.append("DEPRECATED_RETOPOLOGY_CONTRACT_ADAPTED_TO_V6")
    payload["options"] = options
    return RetopologyV6ProcessMetadata.model_validate(payload), list(dict.fromkeys(warnings))


def validate_asset_filename(filename: str) -> str:
    """Return a basename safe for storage and Blender import."""
    if not filename or filename != Path(filename).name or "\x00" in filename:
        raise ValueError("asset filename must be a safe basename")
    if Path(filename).suffix.lower() not in SUPPORTED_ASSET_EXTENSIONS:
        raise ValueError("supported asset formats are FBX, OBJ, GLB, GLTF and BLEND")
    if len(filename.encode("utf-8")) > 255:
        raise ValueError("asset filename is too long")
    return filename


def validate_baker_texture_filename(filename: str) -> str:
    """Return a safe image basename accepted by TextureTransfer.Raytraced."""
    if not filename or filename != Path(filename).name or "\x00" in filename:
        raise ValueError("Baker texture filename must be a safe basename")
    if Path(filename).suffix.lower() not in SUPPORTED_BAKER_TEXTURE_EXTENSIONS:
        raise ValueError("Baker textures must be PNG, JPG, TIFF, TGA or EXR")
    if len(filename.encode("utf-8")) > 255:
        raise ValueError("Baker texture filename is too long")
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


def validate_baker_filename(filename: str) -> str:
    """Return a safe FBX/OBJ basename accepted by the fixed Baker profiles."""
    filename = validate_asset_filename(filename)
    if Path(filename).suffix.lower() not in SUPPORTED_BAKER_EXTENSIONS:
        raise ValueError("Substance Baker inputs must be FBX or OBJ")
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


def substance_bake_request_hash(
    metadata: SubstanceBakeMetadata,
    input_sha256: dict[str, str],
) -> str:
    payload = {
        "job_type": "SUBSTANCE_BAKE_V1",
        "external_asset_id": metadata.external_asset_id,
        "options": metadata.options.model_dump(mode="json"),
        "input_sha256": dict(sorted(input_sha256.items())),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def retopology_audit_request_hash(metadata: RetopologyAuditMetadata, input_sha256: str) -> str:
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


def retopology_v6_process_request_hash(
    metadata: RetopologyV6ProcessMetadata,
    project_sha256: str,
    reference_sha256: dict[str, str],
) -> str:
    payload = {
        "job_type": "RETOPOLOGY_PROCESS_V2",
        "engine_contract": "retopology-direct-v2",
        "package_sha256": RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
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
