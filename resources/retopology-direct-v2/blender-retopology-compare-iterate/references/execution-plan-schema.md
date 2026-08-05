# Pre-generation execution plan

Use this plan only before creating low-poly geometry. It prevents generic proxies, wrong scale, guessed protrusions, and unsupported component splits. It does not inspect or score a generated low.

Minimum example:

```json
{
  "output_behavior": "save_and_stop",
  "user_inspects_result": true,
  "automatic_post_generation_actions": [],
  "source_identity": {
    "blend_filepath": "D:/assets/current.blend",
    "object_name": "H08_HIGH",
    "mesh_data_name": "H08_HIGH_MESH",
    "measurement_space": "high_local",
    "matrix_world": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
  },
  "method_decision": "semantic_reconstruction",
  "shape_authority": {
    "authority": "high_poly_only",
    "global_registration_inputs": ["matrix_world", "coarse_bounds"],
    "local_profile_sections": [
      {
        "section_id": "body_mid",
        "coordinate_space": "high_local",
        "source": "high_measurement",
        "controlling_views": ["front", "side", "top"],
        "samples": [[-1, -1], [1, -1], [1, 1], [-1, 1]]
      }
    ],
    "feature_controls": [
      {
        "feature_id": "rear_handle",
        "authority": "high_measurement",
        "decision": "geometry",
        "controlling_views": ["side", "rear", "top"],
        "measurements": {
          "root_a": [0, 0, 0],
          "root_b": [0, 0, 1],
          "path": [[0, 0, 0], [1, 0, 0.5], [0, 0, 1]],
          "section": [0.1, 0.15],
          "maximum_extent": 1.0
        }
      }
    ],
    "openings": [
      {
        "opening_id": "handle_gap",
        "authority": "high_measurement",
        "boundary_measurement": "rear_handle_path_and_roots",
        "controlling_views": ["side", "rear"]
      }
    ],
    "component_evidence": [
      {
        "component_id": "body_shell",
        "evidence": "continuous_molded_envelope"
      }
    ],
    "surface_correspondence_method": "measured_local_sections",
    "template_constants": [
      {
        "name": "radial_segments",
        "value": 12,
        "provenance": "topology_density_only"
      }
    ],
    "uses_only_global_bounds": false,
    "fixed_geometry_proportions_from_template": false
  },
  "component_decisions": [
    {
      "component_id": "body_shell",
      "decision": "continuous",
      "evidence_id": "body_shell"
    }
  ],
  "count_evidence_policy": {
    "fixed_face_count_is_shape_evidence": false,
    "fixed_component_count_is_shape_evidence": false,
    "budget_or_count_can_satisfy_shape_gate": false
  }
}
```

For a direct-reduction asset, set `method_decision` to `controlled_direct_reduction` and add:

```json
"direct_reduction_evidence": {
  "structurally_complex": true,
  "integrated_continuous_object": true,
  "fresh_high_duplicate": true
}
```

The plan may contain additional asset-specific measurements. Shape-defining constants must come from `high_measurement`; only topology-density settings may use `topology_density_only`.
