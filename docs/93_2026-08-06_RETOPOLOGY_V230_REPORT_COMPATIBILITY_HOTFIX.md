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

## Follow-up: evidence reconstruction v3

Three post-deployment jobs (`35ac6b94-4abd-4381-a9f3-9efb59faa734`,
`a6ca8b91-8a15-41b1-86cc-1119c7a967b7`, and
`08b6b492-898e-432a-85ec-f41338e891a0`) showed that agents can also omit the
whole alias list or report the pre-import high name. The v3 adapter therefore:

- treats `source-manifest.json` and its `SOURCE_HIGH` identity as authoritative;
- derives the method only from the guarded construction plan when absent;
- opens the generated Blend read-only to recover the single low-object name and
  mesh counters when the report has no usable records;
- refuses zero or multiple generated low Mesh objects;
- never saves, modifies, regenerates, or retries the Blend.
