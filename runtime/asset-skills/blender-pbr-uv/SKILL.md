---
name: blender-pbr-uv
description: "Create, repair, straighten, pack, and validate production PBR UVs for Blender mesh assets and FBX/OBJ/GLB files with two preserved algorithms: the original deterministic PBR seam workflow and an optional MinistryOfFlat (MOF) low-seam workflow for complex single- or multi-Mesh assets. Use when the user asks to 展UV、UV展开、PBR UV、自动展UV、原版UV、MOF UV、插件展UV、少切线、隐藏缝、圆柱展开、UV打直、统一Texel Density、UV排版、检查拉伸/翻转/重叠、修复导出FBX没有UV，或批量处理 Blender 模型 UV。"
---

# Blender PBR UV

Produce game-ready UVs with hard-edge correctness, controlled seams, consistent density, and verified FBX export. Preserve both algorithms; never replace or silently redirect the original workflow.

## Choose the algorithm

- Use `legacy_pbr` by default and whenever the request says 原版、传统 PBR、硬边切开、隐藏缝方向、批量展 UV, or does not explicitly select MOF. Run `scripts/unwrap_fbx.py` unchanged.
- Use `mof_low_seam` when the request explicitly selects MOF for a structurally complex non-hard-surface asset, or when the approved server policy classifies a structurally complex multi-Mesh FBX as `complex_multi_mesh`. Run `scripts/mof_unwrap.py`. General and simple hard-surface assets remain on `legacy_pbr`.
- Require the MOF Blender add-on and licensed MinistryOfFlat runtime for `mof_low_seam`. If they are unavailable, return `UV_MOF_RUNTIME_UNAVAILABLE`. Never fall back to `legacy_pbr` because the UV result would no longer match the selected method.
- MOF accepts one or more face-bearing mesh objects. The script separates every original object temporarily by loose parts, invokes MOF once across all parts, restores each original object boundary, and packs all restored objects together.
- For “只展一次 / 快速展开 / 不返工”, run the selected script, run QA once, and stop if hard failures are zero.
- For production delivery, inspect only QA outliers and repair those components locally. Never solve a few outliers by globally increasing seam count.
- For review-only requests, do not edit; run QA and report component-level problems.
- RetopoFlow is not required. Prefer Blender operators and Python for repeatability.

For Li3D server metadata, use:

```json
{"options":{"algorithm":"mof_low_seam","asset_profile":"complex_multi_mesh"}}
```

Omit `options.algorithm` to let the upgraded server classifier choose. The API and MOF Worker must both reject MOF unless `asset_profile` is explicitly `complex_non_hardsurface` or `complex_multi_mesh`. Never enable MOF from polygon count alone; multi-Mesh routing also requires multiple real objects/components and preservation-safe geometry.

## Protect the source

1. Keep the source file unchanged.
2. Write a new `.blend`, exported `.fbx`, report JSON, and QA JSON.
3. Apply object scale before unwrapping. Preserve object placement unless the user asks for a layout change.
4. Reuse one mesh datablock for exactly identical top-level objects. Overlap internal duplicate parts only after proving an orientation-preserving rigid match; never overlap mirrored parts by default.
5. FBX exporters may split one connected surface into duplicate-position vertices for normals or smoothing groups. For the Li3D Max-compatible delivery policy explicitly authorized by the user, conservatively weld only these proven export splits in the output copy before unwrapping. Preserve polygon/loop count, bounds, material assignment, and transforms; report the output vertex-count change. Reject ambiguous, degenerate, multi-material, shape-key, or non-manifold welds. Keep the source file unchanged.

For MOF, additionally preserve the original mesh object count and boundaries, material slot order, per-object geometry digests and matrices, and non-face loose components. Read [references/mof-wrapper-notes.md](references/mof-wrapper-notes.md) before changing the wrapper or its runtime setup.

## Build seams

Follow these rules in order:

1. Make every genuine hard edge a UV boundary. Remove false sharp flags only when adjacent faces are coplanar.
2. When imported shading metadata is unreliable, derive additional hard edges from geometry, starting near 75°. Do not use a low global angle merely to reduce stretch.
3. Do not treat exact duplicate-position FBX export splits as authored seams. Under the authorized Li3D Max-compatible policy, safely weld them in the delivered copy so 3ds Max receives real connected UV shells instead of coincident but topologically separate shells.
4. Keep soft surfaces continuous. Add the minimum topological cut required to open an annular or closed surface.
5. Put longitudinal cuts on the back, underside, inside, bottom, or occluded side relative to the intended camera/front direction.
6. Treat cylinders and tubes as one longitudinal cut plus separate cap rings. Do not use diagonal seams.
7. Treat planar hard-surface panels as large islands bounded by real hard edges.
8. Allow limited stretch on complex soft/organic pieces when another seam would be more visible than the distortion.

Read [references/pbr-uv-standard.md](references/pbr-uv-standard.md) when selecting seams manually, diagnosing checker errors, or deciding whether duplicate parts may share UVs.

## Unwrap, straighten, and pack

1. Use conformal/angle-based unwrap for curved surfaces.
2. Project truly coplanar seam islands exactly in their plane.
3. Use Follow Active Quads only on clean quad strips; validate afterward because it can damage mixed triangle/quad regions.
4. Rotate islands to cardinal axes. Keep long strips and mechanical panels horizontal or vertical.
5. Average island scale for consistent texel density.
6. Pack into 0–1 with no accidental overlap. At 2048 px, target 8–16 px exact padding.
7. Repack verified duplicate overlaps with overlap merging enabled.

For a generic one-pass FBX workflow, run:

```powershell
& $blender --background --python scripts/unwrap_fbx.py -- `
  --input "C:\path\model.fbx" `
  --output-blend "C:\path\model_PBR_UV.blend" `
  --output-fbx "C:\path\model_PBR_UV.fbx" `
  --hard-angle 75 --hidden-axis y+ --padding-px 10 --resolution 2048
```

Interpret `hidden-axis` as the direction pointing away from the main camera. For a front camera at negative Y looking toward positive Y, use `y+`.

For the MOF low-seam workflow, run:

```powershell
& $blender --background --python scripts/mof_unwrap.py -- `
  --input "C:\path\model.blend" `
  --output-blend "C:\path\model_MOF_UV.blend" `
  --output-fbx "C:\path\model_MOF_UV.fbx" `
  --report "C:\path\model_MOF_UV_report.json" `
  --resolution 2048 --padding-px 10
```

Do not use `--factory-startup` for MOF because that can disable the installed add-on. The script rejects missing add-on properties/operators before touching the model.

## Validate before delivery

Run:

```powershell
& $blender --background --python scripts/qa_uv.py -- `
  --input "C:\path\model_PBR_UV.blend" `
  --output "C:\path\model_PBR_UV_QA.json"
```

Require:

- zero faces outside 0–1;
- zero flipped or degenerate UV faces;
- zero hard edges missing a UV split;
- zero accidental overlap unless explicitly allowed for verified duplicates;
- every mesh has an active UV layer;
- the MOF result has exactly the same mesh object count and object boundaries as the input;
- the MOF per-object geometry, material slots, object transforms, and non-face loose parts are unchanged;
- multi-object UVs are globally packed with zero cross-object overlap;
- FBX readback preserves visible mesh objects and the UV layer;
- any Max-compatible split-vertex repair preserves polygon, loop, material, transform, and bounds contracts after FBX readback, while its reported output vertex count may be lower;
- Max-compatible QA reports identical visual and topological UV-island counts with zero virtual welded UV edges;
- texel-density p10/p90 remains close to 1 after normalization;
- stretch p90 is preferably at most 1.2 and p95 at most 1.5; review local maxima instead of adding global seams;
- axis-alignment error is visually negligible on straightable islands.

If QA fails, fix the named component or island, repack, and rerun QA. Do not claim completion from a checker screenshot alone.

## Deliver

Return clickable paths to the `.blend`, `.fbx`, report JSON, and QA JSON. State the hard-edge, flip, overlap, padding, density, and stretch results briefly. Mention intentional shared UV overlap explicitly.
