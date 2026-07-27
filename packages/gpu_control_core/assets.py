import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_ASSET_EXTENSIONS = frozenset({".fbx", ".obj", ".glb", ".gltf", ".blend"})
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


def validate_asset_filename(filename: str) -> str:
    """Return a basename safe for storage and Blender import."""
    if not filename or filename != Path(filename).name or "\x00" in filename:
        raise ValueError("asset filename must be a safe basename")
    if Path(filename).suffix.lower() not in SUPPORTED_ASSET_EXTENSIONS:
        raise ValueError("supported asset formats are FBX, OBJ, GLB, GLTF and BLEND")
    if len(filename.encode("utf-8")) > 255:
        raise ValueError("asset filename is too long")
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


def lease_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
