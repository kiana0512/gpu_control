---
name: blender-auto-retopo-align
description: Generate one sparse next-generation Blender low-poly model from a high-poly FBX, GLB, GLTF, OBJ, or Blend source, then restore the generated low to the high model's original position, rotation, axis orientation, and scale and export high/low FBX files. The generated-low server policy delivers no UV, performs no direction render, and performs no FBX reimport. Use for 服务器自动拓扑替换、一键拓扑后高低模不重合、自动拓扑后恢复原坐标、拓扑完成后强制对齐、批量高模生成低模并输出模型文件。Also supports transform-only alignment of an externally supplied existing low model. Never use the alignment stage to retopologize, remesh, decimate, triangulate, edit UVs, or replace the user's low model as part of transform-only alignment.
---

# Blender Auto Retopo Align

## Purpose

Run one controlled retopology build and one coordinate-restoration/export stage as a single workflow. The current high model is both the shape authority and coordinate authority. The low model is generated once, then alignment may change only transforms or equivalent vertex-space transforms that preserve world shape and topology.

## Read Before Building

For the routine server **generated-low** path, this `SKILL.md`, the server task prompt, and
the immutable source manifest form the complete fast-path instructions. Read this file once,
then act on those task-local inputs. Do not reopen all four long references, inspect the guard
script source, or rediscover package rules unless the task evidence is missing or contradictory.
The references remain bundled, hash-verified, and authoritative for exceptional diagnosis and
the external-low mode:

- `references/direct-output-construction-rules.md`
- `references/learned-asset-lessons.md`
- `references/execution-plan-schema.md`
- `references/coordinate-restoration-contract.md`

For a prepared FBX/OBJ/GLB/GLTF source, consume `semantic_measurements` from the immutable
source manifest. Do not launch another Blender measurement pass or render measurement/direction
images when those measurements are present. Direction rendering is disabled for generated-low
server jobs. A direct Blend input without a source manifest may run one bounded text-only
measurement pass, but still must not render direction images.

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
- For every server delivery, generate front, back, left, right, top, bottom, and perspective evidence with `scripts/render_alignment_views.py`. Keep the low opaque orange with a dark wire overlay; never use transparency.
- Run `scripts/validate_bake_pair.py` against freshly exported high/low FBX files before publication. A maximum-axis size difference above 3%, low-to-high P95 surface distance above 4% of the high diagonal, or high-to-low P95 above 4% is a visual/shape mismatch and must reject the candidate even when matrix, center, UV, and topology gates pass.
- Never substitute retopology for a failed alignment.

## Complete Generated-Low Workflow

### 1. Preserve the source

- Work on a duplicate or task-local copy.
- For FBX/GLB/GLTF/OBJ input, run `scripts/prepare_fbx_source.py`; use its single joined `SOURCE_HIGH` and source manifest.
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
- Do not create, delete, unwrap, repack, or otherwise modify UVs in the automatic-retopology stage. Preserve existing low UVs byte-for-byte when present; a low with no UV is also valid and UV work belongs to the separate UV stage.
- Give the low an opaque yellow/orange display material or object color. Do not use transparency.
- Do not add a presentation offset in unattended/server mode.
- After applying generated modifiers, converting curves, and joining the fresh low, remove only zero-area or degenerate faces and require finite vertex coordinates. This finish applies only to the new low, never to `SOURCE_HIGH`.
- Boundary/open, non-manifold, loose, duplicate, and orientation measurements remain diagnostics. They do not block generated-low delivery under the user-selected `no_broken_faces` gate.
- Execute the Blender build; writing a plan or build script without producing the requested Blend is a failed generation.
- Write `generation_report.json` with the normal generation fields, including the measured `uv_layers` count, plus:
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

It may not change vertex count, edge connectivity, polygon connectivity, polygon count, UV layers or coordinates, vertex groups, shape keys, custom normals, or modifiers. It never creates, deletes, or unwraps UVs, and never runs Decimate, remesh, triangulation, projection, or geometry repair.

### 5. Stop at the defined terminal

Coordinate restoration and saved-Blend topology preservation are fixed persistence checks. Generated-low delivery does not render direction views and does not reimport FBX. Do not score, revise, retry, or generate another low automatically after the builder has produced a gate-passing Blend. An early builder rejection for an empty, non-finite, or degenerate-face candidate may be returned to the queue for at most one fresh attempt.

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
- a topology fingerprint changes after the user-selected no-UV policy is applied or during Blend readback;
- handedness changes or a mirror is introduced.
- the generated or saved Blend contains zero-area/degenerate faces or non-finite coordinates.

Never retry modeling automatically after a coordinate or saved-Blend topology-preservation failure. An early empty, non-finite, or degenerate-face rejection may use the server's single bounded fresh attempt; preserve both attempts' logs and never publish the rejected candidate.

On that single bounded fresh attempt, do not repeat the same low-detail semantic proxy. Re-read the immutable source topology. When the plan guard permits whole-object controlled reduction, prefer a fresh `SOURCE_HIGH` duplicate with enough retained density to inherit the measured high silhouette and dimensions. Otherwise change to measured per-component hybrid construction. The retry never modifies `SOURCE_HIGH` and still requires the `no_broken_faces` gate.

## Server Compatibility

Keep the legacy one-file server entrypoint and success status so existing HTTP/queue code can replace the old worker package without changing its main contract. The requested `.blend` becomes the aligned result. Publish bake sidecars in `<output-stem>.bake/` and expose their paths in `result.json`.
