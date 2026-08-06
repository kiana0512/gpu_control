# Retopology v2.3 generation-report compatibility hotfix

Date: 2026-08-06

## Scope

This change is limited to the GPU Control Codex launcher adapter. The approved
v2.3.0 retopology package, Skill, Blender builder, geometry, and output objects
remain unchanged.

## Production symptom

Some successful agent builds wrote the documented delivery identity under the
v2.3 `objects` alias but omitted one or more optional diagnostic values
(`faces`, `triangles`, or `actual_plugin_use`). GPU Control consequently left
`assets` absent and the upstream wrapper rejected the already-created Blend at
92% with `generation report has no asset records`.

## Compatibility rule

- `high_object`, `low_object`, and an approved `method_decision` remain
  mandatory and fail closed.
- Missing diagnostic values are represented as JSON `null` and listed under
  `gpu_control_compatibility.missing_diagnostics`.
- The raw agent report is preserved as `generation_report.original.json`.
- No geometry is generated, changed, retried, or replaced by this adapter.

## Verification

- Python compilation passed.
- Direct compatibility check passed for an identified delivery with omitted
  diagnostic counters.
- Existing fail-closed coverage remains for a missing low-object identity.
- Before this patch, production job
  `ef33930b-9a4c-46ed-b9a8-50eb773c5d3c` completed successfully with the
  standard v2.3 alias conversion, confirming the delivery path itself remains
  healthy.

