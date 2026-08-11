import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_ASSET_EXTENSIONS = frozenset({".fbx", ".obj", ".glb", ".gltf", ".blend"})
SUPPORTED_REFERENCE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
# Substance 3D Baker 15.1.0 on the production Windows host natively reads
# binary glTF scenes.  Li3D's project/high-poly artifacts are commonly GLB,
# while retopology/UV deliveries are FBX, so the bake boundary must accept
# both sides of that real hand-off without rewriting model geometry.
SUPPORTED_BAKER_EXTENSIONS = frozenset({".fbx", ".obj", ".glb"})
SUPPORTED_BAKER_TEXTURE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".tga", ".exr"}
)
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class UVUnwrapOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["legacy_pbr", "mof_low_seam"] = "legacy_pbr"
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
    uv_algorithm: Literal["legacy_pbr", "mof_low_seam"] = "legacy_pbr"
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
RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION = "3.0.0"
RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256 = (
    "0a6e539a03e6dcecd9518c6fa592c112892f829717d2c768721463796a604138"
)
RETOPOLOGY_DIRECT_V2_MAX_DIMENSION_RELATIVE_ERROR = 0.05
RETOPOLOGY_FBX_UNIT_SCALE_FACTOR_CENTIMETERS = 100.0


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


def retopology_fbx_meter_evidence_valid(payload: object) -> bool:
    """Require an actual meter-unit FBX suitable for raw browser loaders."""

    if not isinstance(payload, dict):
        return False
    readback = payload.get("fbx_readback")
    if not isinstance(readback, dict) or readback.get("passed") is not True:
        return False
    contract = readback.get("unit_contract")
    if not isinstance(contract, dict):
        return False
    unit_scale = contract.get("unit_scale_factor_centimeters")
    original_unit_scale = contract.get("original_unit_scale_factor_centimeters")
    return (
        contract.get("schema_version") == "retopology_fbx_units.v1"
        and contract.get("passed") is True
        and contract.get("coordinate_unit") == "meter"
        and contract.get("raw_coordinates_are_meters") is True
        and contract.get("global_scale") == 1.0
        and contract.get("apply_unit_scale") is True
        and contract.get("apply_scale_options") == "FBX_SCALE_UNITS"
        and contract.get("axis_forward") == "-Z"
        and contract.get("axis_up") == "Y"
        and isinstance(unit_scale, int | float)
        and not isinstance(unit_scale, bool)
        and math.isfinite(float(unit_scale))
        and math.isclose(
            float(unit_scale),
            RETOPOLOGY_FBX_UNIT_SCALE_FACTOR_CENTIMETERS,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        and isinstance(original_unit_scale, int | float)
        and not isinstance(original_unit_scale, bool)
        and math.isfinite(float(original_unit_scale))
        and math.isclose(
            float(original_unit_scale),
            RETOPOLOGY_FBX_UNIT_SCALE_FACTOR_CENTIMETERS,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )


def _retopology_fbx_meter_contract_valid(contract: object) -> bool:
    if not isinstance(contract, dict):
        return False
    unit_scale = contract.get("unit_scale_factor_centimeters")
    original_unit_scale = contract.get("original_unit_scale_factor_centimeters")
    return (
        contract.get("schema_version") == "retopology_fbx_units.v1"
        and contract.get("passed") is True
        and contract.get("coordinate_unit") == "meter"
        and contract.get("raw_coordinates_are_meters") is True
        and contract.get("global_scale") == 1.0
        and contract.get("apply_unit_scale") is True
        and contract.get("apply_scale_options") == "FBX_SCALE_UNITS"
        and contract.get("axis_forward") == "-Z"
        and contract.get("axis_up") == "Y"
        and isinstance(unit_scale, int | float)
        and not isinstance(unit_scale, bool)
        and math.isclose(float(unit_scale), 100.0, rel_tol=0.0, abs_tol=1e-9)
        and isinstance(original_unit_scale, int | float)
        and not isinstance(original_unit_scale, bool)
        and math.isclose(float(original_unit_scale), 100.0, rel_tol=0.0, abs_tol=1e-9)
    )


def _retopology_clean_topology_valid(topology: object) -> bool:
    if not isinstance(topology, dict):
        return False
    required_zero = (
        "degenerate_faces",
        "nonmanifold_edges",
        "loose_edges",
        "loose_vertices",
        "duplicate_vertices",
        "duplicate_faces",
        "inconsistent_orientation_edges",
    )
    intersections = topology.get("self_intersections")
    return (
        isinstance(topology.get("faces"), int)
        and topology["faces"] > 0
        and topology.get("finite_coordinates") is True
        and all(topology.get(key) == 0 for key in required_zero)
        and isinstance(intersections, dict)
        and intersections.get("intersecting_triangle_pairs") == 0
    )


def retopology_bake_alignment_evidence_valid(payload: object) -> bool:
    """Validate the strict post-topology bake-alignment report."""

    if not isinstance(payload, dict):
        return False
    if (
        payload.get("schema_version") != "retopology_bake_alignment.v2"
        or payload.get("mode") != "transform_only_alignment_then_separate_uv"
        or payload.get("passed") is not True
        or payload.get("source_high_is_sole_coordinate_authority") is not True
        or payload.get("direct_object_transform_copy_used") is not False
        or payload.get("uniform_scale_only") is not True
        or payload.get("mirror_candidates_allowed") is not False
        or payload.get("topology_rebuild_allowed") is not False
        or payload.get("alignment_changes_topology_or_uv") is not False
        or payload.get("uv_is_a_separate_pre_alignment_stage") is not True
        or payload.get("alignment_skill") != "blender-align-bake-models"
        or payload.get("automatic_visual_review_required") is not True
        or payload.get("uv_algorithm") not in {"legacy_pbr", "mof_low_seam"}
    ):
        return False
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return False
    for pair in pairs:
        if not isinstance(pair, dict):
            return False
        role = pair.get("role_identification")
        transform = pair.get("transform_application")
        registration = pair.get("registration")
        views = pair.get("final_views")
        high_topology = pair.get("final_high_topology")
        low_topology = pair.get("final_low_topology")
        if (
            not isinstance(role, dict)
            or role.get("method") != "higher_face_count_is_high"
            or not isinstance(role.get("high_faces"), int)
            or not isinstance(role.get("original_low_faces"), int)
            or role["high_faces"] <= role["original_low_faces"]
            or not isinstance(transform, dict)
            or transform.get("copied_high_object_transform") is not False
            or transform.get("geometry_registration_used") is not True
            or transform.get("applied_exactly_once_to_duplicate_mesh") is not True
            or transform.get("mirror_introduced") is not False
            or pair.get("alignment_scope") != "transform_only"
            or pair.get("rebuild_allowed") is not False
            or pair.get("fallback") is not None
            or not isinstance(registration, dict)
            or registration.get("skill") != "blender-align-bake-models"
            or registration.get("transform_only") is not True
            or registration.get("axis_scale_used") is not False
            or registration.get("mirror_allowed") is not False
            or registration.get("topology_uv_preserved_during_alignment") is not True
            or pair.get("original_high_preserved") is not True
            or pair.get("original_low_preserved") is not True
            or pair.get("originals_hidden") is not True
            or not isinstance(high_topology, dict)
            or not isinstance(low_topology, dict)
            or not isinstance(high_topology.get("faces"), int)
            or not isinstance(low_topology.get("faces"), int)
            or low_topology["faces"] >= high_topology["faces"]
            or low_topology.get("finite_coordinates") is not True
            or low_topology.get("degenerate_faces") != 0
            or not isinstance(pair.get("uv"), dict)
            or not isinstance(views, dict)
            or views.get("views")
            != ["front", "back", "left", "right", "top", "bottom", "perspective"]
            or views.get("low_display") != "opaque_bright_orange_solid_with_dark_wire"
            or views.get("low_transparency") is not False
            or views.get("xray") is not False
        ):
            return False
    return all(
        isinstance(payload.get(name), dict)
        and _retopology_fbx_meter_contract_valid(payload[name].get("unit_contract"))
        for name in ("bake_high_fbx", "bake_low_fbx")
    )


def retopology_bake_pair_validation_evidence_valid(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    high = payload.get("high")
    low = payload.get("low")
    return (
        payload.get("schema_version") == "retopology_bake_pair_validation.v2"
        and payload.get("passed") is True
        and payload.get("fresh_blender_scene_reimport") is True
        and payload.get("low_faces_less_than_high") is True
        and payload.get("low_has_uv") is True
        and payload.get("low_structure_match") is True
        and isinstance(high, dict)
        and isinstance(low, dict)
        and high.get("passed") is True
        and low.get("passed") is True
        and low.get("uv_passed") is True
        and isinstance(high.get("faces"), int)
        and isinstance(low.get("faces"), int)
        and low["faces"] < high["faces"]
        and _retopology_fbx_meter_contract_valid(high.get("unit_contract"))
        and _retopology_fbx_meter_contract_valid(low.get("unit_contract"))
    )


def retopology_bake_visual_qa_evidence_valid(payload: object) -> bool:
    required_views = {"front", "back", "left", "right", "top", "bottom", "perspective"}
    if not isinstance(payload, dict):
        return False
    checked = payload.get("views_checked")
    return (
        payload.get("schema_version") == "retopology_bake_visual_qa.v1"
        and payload.get("passed") is True
        and payload.get("visual_match") is True
        and payload.get("correct_orientation") is True
        and payload.get("no_wrong_mirror") is True
        and payload.get("no_long_spikes") is True
        and payload.get("no_visible_intersections") is True
        and isinstance(checked, list)
        and set(checked) == required_views
        and payload.get("failure_codes") == []
    )


def retopology_auto_align_v3_evidence_valid(payload: object) -> bool:
    """Validate the v3 same-job source-coordinate finalization evidence.

    v3 deliberately performs no ICP, topology/UV editing, automatic visual
    review, or second modeling pass.  The generated low must be restored to
    the source high's coordinate frame and both FBX files must survive a fresh
    import with the expected bounds and low-mesh structure.
    """

    if not isinstance(payload, dict):
        return False
    if (
        payload.get("schema") != "li3d-auto-retopo-align-v1"
        or payload.get("pass") is not True
        or payload.get("transform_only_alignment") is not True
        or payload.get("alignment_mode") != "source_matrix_restore"
        or payload.get("coordinate_authority") != "high"
        or payload.get("icp_used") is not False
        or payload.get("topology_or_uv_edited") is not False
        or payload.get("low_display") != "opaque_yellow"
        or payload.get("topology_uv_unchanged") is not True
    ):
        return False
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return False
    for pair in pairs:
        if not isinstance(pair, dict):
            return False
        matrix_error = pair.get("matrix_error_after")
        center_error = pair.get("center_error_ratio")
        size_error = pair.get("size_error_ratio")
        if (
            not isinstance(matrix_error, int | float)
            or isinstance(matrix_error, bool)
            or not math.isfinite(float(matrix_error))
            or float(matrix_error) > 1e-5
            or not isinstance(center_error, int | float)
            or isinstance(center_error, bool)
            or not math.isfinite(float(center_error))
            or float(center_error) > 1e-5
            or not isinstance(size_error, int | float)
            or isinstance(size_error, bool)
            or not math.isfinite(float(size_error))
            or float(size_error) > 0.15
            or pair.get("high_determinant_sign")
            != pair.get("low_determinant_sign_after")
            or not isinstance(pair.get("delivered_high_name"), str)
            or not pair.get("delivered_high_name")
            or not isinstance(pair.get("delivered_low_name"), str)
            or not pair.get("delivered_low_name")
        ):
            return False
    readback = payload.get("fbx_readback")
    if not isinstance(readback, dict):
        return False
    tolerance = readback.get("tolerance")
    high_error = readback.get("high_center_size_error_ratio")
    low_error = readback.get("low_center_size_error_ratio")
    return (
        readback.get("pass") is True
        and readback.get("low_structure_match") is True
        and isinstance(tolerance, int | float)
        and not isinstance(tolerance, bool)
        and math.isclose(float(tolerance), 1e-5, rel_tol=0.0, abs_tol=1e-12)
        and isinstance(high_error, int | float)
        and not isinstance(high_error, bool)
        and math.isfinite(float(high_error))
        and float(high_error) <= float(tolerance)
        and isinstance(low_error, int | float)
        and not isinstance(low_error, bool)
        and math.isfinite(float(low_error))
        and float(low_error) <= float(tolerance)
        and readback.get("expected_low_structure")
        == readback.get("actual_low_structure")
    )


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
    """Return a safe FBX/OBJ/GLB basename accepted by the fixed Baker profiles."""
    filename = validate_asset_filename(filename)
    if Path(filename).suffix.lower() not in SUPPORTED_BAKER_EXTENSIONS:
        raise ValueError("Substance Baker inputs must be FBX, OBJ or GLB")
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
