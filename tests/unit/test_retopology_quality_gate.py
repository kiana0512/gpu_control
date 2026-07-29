from gpu_control_blender_worker.main import retopology_quality_gate


def _evidence() -> tuple[dict, dict, dict]:
    audit = {
        "audit_passed": True,
        "objects": {
            "low": {
                "topology": {
                    "faces": 1200,
                    "triangles": 120,
                    "quads": 1080,
                    "ngons": 0,
                    "nonmanifold_edges": 0,
                    "loose_edges": 0,
                    "loose_vertices": 0,
                    "duplicate_vertices": 0,
                    "duplicate_faces": 0,
                    "zero_area_faces": 0,
                    "inconsistent_orientation_edges": 0,
                }
            }
        },
        "comparison": {
            "dimension_relative_error": [0.01, 0.02, 0.015],
            "normalized_center_offset": 0.004,
        },
    }
    report = {
        "source_preserved": True,
        "source_topology": {
            "high": {"face_components": 3},
            "reference": {"face_components": 3},
            "current": {"face_components": 3},
        },
        "candidate_topology": {
            "faces": 1200,
            "triangles": 120,
            "quads": 1080,
            "ngons": 0,
            "quad_ratio": 0.9,
            "face_components": 3,
        },
    }
    options = {
        "topology_mode": "mixed",
        "allow_triangles": True,
        "allow_ngons": False,
        "preserve_components": True,
    }
    return audit, report, options


def test_retopology_quality_gate_accepts_measured_mixed_topology() -> None:
    audit, report, options = _evidence()

    result = retopology_quality_gate(audit, report, options)

    assert result["passed"] is True
    assert result["failures"] == []


def test_retopology_quality_gate_rejects_shape_drift() -> None:
    audit, report, options = _evidence()
    audit["comparison"]["dimension_relative_error"] = [0.01, 0.04, 0.015]

    result = retopology_quality_gate(audit, report, options)

    assert result["passed"] is False
    assert "DIMENSION_RELATIVE_ERROR=0.040000>0.03" in result["failures"]


def test_retopology_quality_gate_rejects_component_loss_and_ngons() -> None:
    audit, report, options = _evidence()
    audit["objects"]["low"]["topology"]["ngons"] = 2
    report["candidate_topology"]["face_components"] = 2

    result = retopology_quality_gate(audit, report, options)

    assert result["passed"] is False
    assert "NGONS=2" in result["failures"]
    assert "FACE_COMPONENTS_LOST=1" in result["failures"]
