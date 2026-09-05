"""Conservative geometry-evidence policy for automatic UV algorithm routing.

The classifier deliberately requires several independent signals.  Polygon
count alone can never select MOF because it cannot distinguish an organic
asset from a detailed hard-surface prop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Literal

UV_AUTO_CLASSIFIER_VERSION = "uv-auto-classifier-v2"


@dataclass(frozen=True)
class UVGeometryEvidence:
    mesh_object_count: int
    face_count: int
    face_component_count: int
    vertex_count: int
    edge_count: int
    manifold_edge_count: int
    boundary_edge_count: int
    nonmanifold_edge_count: int
    modifier_count: int
    shape_key_count: int
    smooth_face_ratio: float
    authored_sharp_edge_ratio: float
    near_planar_edge_ratio: float
    curved_edge_ratio: float
    steep_edge_ratio: float
    very_steep_edge_ratio: float

    def validated(self) -> UVGeometryEvidence:
        counts = (
            self.mesh_object_count,
            self.face_count,
            self.face_component_count,
            self.vertex_count,
            self.edge_count,
            self.manifold_edge_count,
            self.boundary_edge_count,
            self.nonmanifold_edge_count,
            self.modifier_count,
            self.shape_key_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("UV geometry evidence counts must be non-negative")
        ratios = (
            self.smooth_face_ratio,
            self.authored_sharp_edge_ratio,
            self.near_planar_edge_ratio,
            self.curved_edge_ratio,
            self.steep_edge_ratio,
            self.very_steep_edge_ratio,
        )
        if any(not isfinite(value) or value < 0.0 or value > 1.0 for value in ratios):
            raise ValueError("UV geometry evidence ratios must be finite values in [0, 1]")
        return self


@dataclass(frozen=True)
class UVAutoClassification:
    classifier_version: str
    resolved_algorithm: Literal["legacy_pbr", "mof_low_seam"]
    asset_profile: Literal[
        "general", "complex_non_hardsurface", "complex_multi_mesh"
    ]
    reason_codes: tuple[str, ...]
    evidence: UVGeometryEvidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "classifier_version": self.classifier_version,
            "resolved_algorithm": self.resolved_algorithm,
            "asset_profile": self.asset_profile,
            "reason_codes": list(self.reason_codes),
            "evidence": asdict(self.evidence),
        }


def classify_uv_geometry(evidence: UVGeometryEvidence) -> UVAutoClassification:
    """Resolve automatic UV routing from multiple conservative signals.

    A structurally substantial multi-Mesh FBX is routed as one MOF job under
    the user-approved multi-model policy, including complex mechanical assets.
    The original soft-surface rules remain unchanged for single-Mesh inputs.
    Polygon count alone still cannot select MOF: the multi-Mesh rule also
    requires multiple real objects/components and preservation-safe geometry.
    """

    evidence = evidence.validated()
    preservation_reasons: list[str] = []
    if evidence.modifier_count:
        preservation_reasons.append("modifiers_require_legacy_preservation")
    if evidence.shape_key_count:
        preservation_reasons.append("shape_keys_require_legacy_preservation")

    if evidence.mesh_object_count >= 2:
        multi_mesh_reasons = list(preservation_reasons)
        if evidence.face_count < 128 or evidence.manifold_edge_count < 48:
            multi_mesh_reasons.append("insufficient_multi_mesh_structural_evidence")
        if evidence.face_component_count < 2:
            multi_mesh_reasons.append("requires_multiple_face_components")
        if multi_mesh_reasons:
            return UVAutoClassification(
                classifier_version=UV_AUTO_CLASSIFIER_VERSION,
                resolved_algorithm="legacy_pbr",
                asset_profile="general",
                reason_codes=tuple(dict.fromkeys(multi_mesh_reasons)),
                evidence=evidence,
            )
        return UVAutoClassification(
            classifier_version=UV_AUTO_CLASSIFIER_VERSION,
            resolved_algorithm="mof_low_seam",
            asset_profile="complex_multi_mesh",
            reason_codes=("complex_multi_mesh_asset",),
            evidence=evidence,
        )

    rejection_reasons = list(preservation_reasons)
    if evidence.mesh_object_count != 1:
        rejection_reasons.append("requires_exactly_one_mesh_object")
    if evidence.face_component_count < 2:
        rejection_reasons.append("requires_multiple_face_components")
    if evidence.face_count < 64 or evidence.manifold_edge_count < 48:
        rejection_reasons.append("insufficient_structural_evidence")
    hard_surface_evidence = (
        evidence.authored_sharp_edge_ratio >= 0.12
        or evidence.very_steep_edge_ratio >= 0.20
        or evidence.near_planar_edge_ratio >= 0.70
        or (
            evidence.near_planar_edge_ratio >= 0.55
            and evidence.very_steep_edge_ratio >= 0.06
        )
    )
    if hard_surface_evidence:
        rejection_reasons.append("hard_surface_edge_pattern")

    soft_surface_evidence = evidence.smooth_face_ratio >= 0.55 or (
        evidence.curved_edge_ratio >= 0.36
        and evidence.near_planar_edge_ratio <= 0.48
        and evidence.very_steep_edge_ratio <= 0.12
    )
    if not soft_surface_evidence:
        rejection_reasons.append("soft_surface_evidence_not_strong_enough")

    structurally_complex = evidence.face_component_count >= 3 or (
        evidence.face_component_count >= 2
        and evidence.face_count >= 200
        and evidence.curved_edge_ratio >= 0.30
    )
    if not structurally_complex:
        rejection_reasons.append("component_structure_not_complex_enough")

    if rejection_reasons:
        return UVAutoClassification(
            classifier_version=UV_AUTO_CLASSIFIER_VERSION,
            resolved_algorithm="legacy_pbr",
            asset_profile="general",
            reason_codes=tuple(dict.fromkeys(rejection_reasons)),
            evidence=evidence,
        )
    return UVAutoClassification(
        classifier_version=UV_AUTO_CLASSIFIER_VERSION,
        resolved_algorithm="mof_low_seam",
        asset_profile="complex_non_hardsurface",
        reason_codes=("complex_multi_component_soft_surface",),
        evidence=evidence,
    )
