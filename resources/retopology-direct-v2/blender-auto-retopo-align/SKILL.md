---
name: blender-auto-retopo-align
description: Generate one sparse next-generation Blender low-poly model from a high-poly FBX or Blend source, then restore the generated low to the high model's original position, rotation, axis orientation, and scale and export bake-ready high/low FBX files with topology, UV, and readback validation. Use for 服务器自动拓扑替换、一键拓扑后高低模不重合、自动拓扑后恢复原坐标、拓扑完成后强制对齐、批量高模生成低模并输出烘焙文件。Also supports transform-only alignment of an externally supplied existing low model. Never use the alignment stage to retopologize, remesh, decimate, triangulate, edit UVs, or repair geometry.
---

# Blender Auto Retopo Align

## Purpose

Run one controlled retopology build and one coordinate-restoration/export stage as a single workflow. The current high model is both the shape authority and coordinate authority. The low model is generated once, then alignment may change only transforms or equivalent vertex-space transforms that preserve world shape and topology.

## Read Before Building

For every retopology job, read:

- `references/direct-output-construction-rules.md`
- `references/learned-asset-lessons.md`
- `references/execution-plan-schema.md`

For coordinate behavior, server integration, or any normalization step, also read `references/coordinate-restoration-contract.md`.

## Choose the Mode

### Generated low in the same job

Use this mode when this skill creates the low from the current high. This is the default server workflow.

- Build exactly one low candidate per high.
- Generate in the high object's local coordinate space.
- Keep the low at the high's original world matrix; do not leave a side-by-side display offset in the server result.
- Do not run ICP after generation. A semantic low is intentionally different from the high, so a surface-error ICP gate can reject or corrupt a correct source-coordinate result.
- After generation, run `scripts/finalize_generated_pair.py`. It removes presentation translation, normalizes the low to the high matrix, exports bake files, and validates preservation.

### Existing external low

Use this mode only when the user supplies a separately created low whose coordinate relationship is unknown.

- Run `scripts/align_bake_models.py` with the high and low files.
- Keep all safety gates enabled. Do not write outputs when orientation, mirror, center, dimensions, or surface-fit gates fail.
- Use `scripts/render_alignment_views.py` and inspect front, back, left, right, top, bottom, and perspective views when the task is interactive.
- Never substitute retopology for a failed alignment.

## Complete Generated-Low Workflow

### 1. Preserve the source

- Work on a duplicate or task-local copy.
- For FBX input, run `scripts/prepare_fbx_source.py`; use its `SOURCE_HIGH` and source manifest.
- Before normalization, record the high `matrix_world`, unit scale, axis convention, and world bounds.
- If a work-space transform is needed, record the full 4x4 `work_to_world` matrix. Bounds alone are not a coordinate contract.

### 2. Plan before geometry

- Measure disconnected islands, silhouettes, openings, negative spaces, section changes, and mechanical component boundaries.
- Write one shape-authority plan per high using `references/execution-plan-schema.md`.
- Run `scripts/guard_shape_authority_plan.py` before creating geometry.
- Select exactly one method: `semantic_reconstruction`, `controlled_direct_reduction`, or `per_component_hybrid`.
- Use semantic reconstruction or per-component hybrid for normal hard-surface props. Do not use blanket whole-object Decimate or remesh as a generic answer. The source manifest's measured fragmentation gate is authoritative. A triangle-soup FBX may use direct reduction only from the server-created `SOURCE_HIGH_NORMALIZED_WORK` exact-weld copy when `normalized_work_source.qualified=true`; never weld, replace, or reduce `SOURCE_HIGH` itself.

### 3. Generate once

- The fresh high is the only shape authority.
- A qualified normalized work copy is only a topology-connected sampling duplicate of that same high. It may be reduced, but it never replaces the visible/authoritative high and must be removed from final delivery.
- Produce exactly one low object for each requested high.
- Spend polygons on silhouette, openings, section changes, negative space, and key connections; keep flat non-silhouette regions sparse.
- Create the low in `source_high_local` coordinates and assign the same `matrix_world` as its high.
- Give the low an opaque yellow/orange display material or object color. Do not use transparency.
- Do not add a presentation offset in unattended/server mode.
- Before reporting success, require a closed manifold: zero boundary/open edges, loose edges/vertices, duplicate geometry, degenerate faces, multi-face non-manifold edges, and inconsistent orientation. This is a deterministic delivery gate, not a second modeling pass.
- Write `generation_report.json` with the normal generation fields plus:
  - `coordinate_space: source_high_local`
  - `coordinate_authority: high_object_matrix_world`
  - `presentation_offset_applied: false`

### 4. Finalize coordinates and bake exports

Run Blender headless:

```bash
blender --background --factory-startup --disable-autoexec \
  --python scripts/finalize_generated_pair.py -- \
  --input-blend result.blend \
  --generation-report generation_report.json \
  --output-dir aligned
```

The finalizer may:

- convert the low's current world placement into the high's local space without changing its world shape;
- remove only a center/display translation so the high and low share the source center;
- bake identical high/low world transforms into export copies and set object transforms to identity;
- add an opaque low display material only when the generated low has no material.

It may not change vertex count, edge connectivity, polygon connectivity, polygon count, UV coordinates, vertex groups, shape keys, custom normals, or modifiers. It never runs Decimate, remesh, triangulation, projection, or geometry repair.

### 5. Stop at the defined terminal

Coordinate restoration, topology/UV preservation checks, and FBX readback are fixed persistence checks, not a second topology review. Do not render, score, revise, retry, or generate another low automatically after the builder finishes. Return the generated result for user inspection with coordinate validation attached.

## Required Outputs

The combined server result must contain:

- aligned result Blend at the requested legacy `.blend` path;
- `bake_high.fbx`;
- `bake_low.fbx`;
- `bake_alignment.blend`;
- `bake_alignment_report.json`;
- original `generation_report.json` and source manifest when applicable;
- task `result.json` with `bake_alignment_status: aligned`.

The low must be visibly different from the high in the Blend, using an opaque yellow/orange material or object color. Never hide the low and never use X-ray or alpha transparency as the default inspection state.

## Failure Rules

Return `RETOPOLOGY_COORDINATE_MISMATCH` and publish no final result if any of these occur:

- the generated low does not declare `source_high_local`;
- high and low cannot be resolved unambiguously;
- high/low matrix equality cannot be established after restoration;
- center or size gates fail;
- a topology or UV fingerprint changes during finalization or Blend readback;
- FBX fresh-import bounds or structure differ from the exported scene;
- handedness changes or a mirror is introduced.
- the generated Blend or fresh-imported low FBX contains boundary/open, loose, duplicate, degenerate, multi-face non-manifold, or inconsistently oriented geometry.

Never retry modeling automatically after this failure. Preserve logs and reports for diagnosis.

## Server Compatibility

Keep the legacy one-file server entrypoint and success status so existing HTTP/queue code can replace the old worker package without changing its main contract. The requested `.blend` becomes the aligned result. Publish bake sidecars in `<output-stem>.bake/` and expose their paths in `result.json`.
