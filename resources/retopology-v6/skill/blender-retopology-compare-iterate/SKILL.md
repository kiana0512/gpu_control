---
name: blender-retopology-compare-iterate
description: Rebuild and validate production game-ready Blender low-poly meshes directly from high-poly models without requiring a low-poly reference, using learned construction rules to make the first formal candidate correct rather than relying on repeated production trials. Use for Blender automatic or assisted retopology, high-only topology design, RetopoFlow-assisted workflows, bake-oriented polygon budgeting, high/low silhouette and protrusion matching, rejected-method training, live Blender batch work, or comparison-row assembly; also use for 次世代游戏低模拓扑、高模重拓扑、一次拓扑到位、布线纠错、高低模多视角对比、机械分件重建和有机一体模型结构化重拓扑。
---

# Blender High-only Game Retopology

Use the high-poly as the only modeling input and shape authority. Derive a sparse game-prop topology from the learned construction, silhouette, shading, deformation, and bake rules in this skill. Do not ask for, inspect, or depend on a low-poly reference. If unrelated low-poly objects already exist in the scene, preserve them as user data but do not use them to drive the result.

Read [references/high-only-game-topology.md](references/high-only-game-topology.md) completely before every modeling task.

For batch assets, plugin-assisted work, a live Blender session, comparison-row assembly, or final delivery, also read [references/production-runbook.md](references/production-runbook.md) completely before acting.

Before starting a new batch or accepting automatic topology, also read [references/validated-batch-retrospective.md](references/validated-batch-retrospective.md) completely. It contains the verified H01-H15 production history and the N06 case proving that silhouette IoU alone cannot validate an unconstrained reduction, plus the user-approved controlled-reduction route for complex identity-critical surfaces.

Before training on rejected candidates or starting a formal batch after training, also read [references/n01-n08-training-lessons.md](references/n01-n08-training-lessons.md) completely. It separates training trials from formal production and defines the N01-N08 first-candidate method, fixed-view comparison, feature controls, and freeze/recovery safeguards.

## Make the first formal candidate correct

- Treat repeated training trials as method calibration only. Never convert their count into a formal multi-pass requirement.
- Before formal geometry, finish the asset classification, method decision, high-derived measurements, feature-control table, component plan, profile events, radial/axial counts, topology-flow plan, face band, and geometry-versus-bake split.
- Create one authoritative formal low per high. Do not use the user's formal file as a seed/ratio experiment and do not manufacture several colored alternatives.
- Route by asset region before the first modeling operation. Use semantic multipart reconstruction for mechanical regions, sparse silhouette envelopes for dense aggregate regions, and controlled reduction from a fresh high-derived copy for complex continuous surfaces whose identity would be lost by proxy reconstruction.
- Validate the one authoritative result in aligned fixed orthographic views, canonical perspective, and close wire review. Validation proves that the first-pass plan worked; it is not a strategy of producing many candidates until one happens to pass.
- If an unexpected miss remains, correct the same unfinished authoritative object and add the cause to the training knowledge. Do not call the miss a completed pass or resume blind variant generation.
- Treat automated green flags as invalid when their evidence does not cover the claimed gate. Object existence, successful rendering, AABB equality, topology integrity, low face count, and wire-audit success cannot independently prove silhouette or construction.
- Treat explicit user visual rejection as a failed product gate. Record the visible cause and update the training rule; do not defend the candidate with unrelated metrics.

## Input and output

Required input:

- The user's current Blender scene, or a model file, containing one or more high-poly meshes.

Optional constraints:

- Target platform, viewing distance, LOD, triangle budget, deformation needs, texel density, or bake pipeline.

Do not make a low-poly reference an input.

Required output:

- Separately named generated low-poly meshes that leave every high-poly source unchanged.
- A high/generated inspection arrangement, matched front/back/left/right/top/bottom/perspective captures, and audit JSON files.
- Per-object face and triangle counts, plugin-use record, source-preservation result, and explicit UV/cage/material/bake status.
- For a batch, a final aggregate Blend, an object-pair manifest, a post-render layout verification, and one visible low for every high.

## Preserve the source scene

1. Work in the user's current Blender file unless asked otherwise.
2. Identify the exact high and generated low object for each asset.
3. Save a new version before rebuilding.
4. Never overwrite, rename, reshape, re-topologize, replace materials on, or delete the high.
5. Run `scripts/audit_pair.py` before work to store a baseline fingerprint for the high.
6. Keep every generated candidate as a separately named, versioned object. Hide rejected versions in a clearly named superseded collection.
7. Do not add text geometry to asset meshes. Batch review labels may exist only in a separate review-only collection and must be excluded from game export.
8. Keep authoritative candidates in a persistent work directory adjacent to the deliverable, not only in a temporary folder.
9. When controlling a live Blender session, guard every mutation with the exact expected `bpy.data.filepath`; preserve a dirty session with a copy before loading the final file.
10. Maintain a machine-verifiable manifest with the ordered asset IDs, exact source library/object, candidate version, status, and mesh fingerprint. Require exactly one accepted candidate per asset; never select by fuzzy prefix or assume the latest version is best.

## Establish the high-poly authority

- Follow the **high-poly** for silhouette, proportions, orientation, ground contact, large planes, major volumes, openings, and appendage placement.
- Use the built-in sparse-prop rules for topology density, acceptable triangles, edge-flow style, component separation, and geometry-versus-bake decisions.
- Form an explicit topology plan from the high-poly before creating geometry.
- Reject a technically clean mesh if its silhouette, proportions, openings, negative space, or construction reads as a different object.

Before modeling, inspect front, side, top, and perspective views at matched scale and orientation. Record:

- Asset class: boxy hard surface, rounded hard surface, organic, or repeated construction.
- Silhouette-critical regions and major parts that require geometry.
- Details that should be baked instead of modeled.
- Logical joined and separated components.
- Per-asset face-density range.
- Planned use of quads, triangles, poles, caps, and flat panels.
- Planned cross-section counts, cap strategy, attachment strategy, and geometry-versus-bake decisions.
- A `face_budget` with target minimum, target maximum, and hard maximum.
- A `geometry_decisions` entry for every disputed detail. Use only `keep_geometry`, `bake`, or `omit`; record `authority: user_approved` for explicit user removals.
- A `feature_controls` table for every silhouette-visible protrusion, opening, handle, latch, hinge, foot, bracket, and attachment root. Record the high-derived local center, local dimensions, controlling views, and whether geometry is required. Do not place these parts by eye from one perspective view.

Never regenerate a `user_approved` omission in a later automatic pass. Keep an unmasked comparison so the omission stays visible in the audit record.

## Choose the structured reconstruction strategy before modeling

Make and record this method decision before creating a candidate:

- Use **semantic reconstruction** for mechanical and hard-surface multipart assets, broad planar forms, repeated construction, and assets assembled from distinct parts.
- Use **controlled direct reduction** for complex integrated or soft assets and regions when a deliberate coarse proxy would visibly change identity. Duplicate or isolate from the untouched high, apply asset-specific Decimate only to the fresh copy, preserve openings/boundaries/silhouette, clean zero-area faces, and record the source and ratio. Never run it on `SOURCE_HIGH`.
- Use **structured organic reconstruction** when a stable cage/patch plan can preserve the form better than reduction: establish primary silhouette rings and structural flow, then use RetopoFlow-assisted drawing, deliberate local cages/patches, and bounded Shrinkwrap fitting.
- Use **silhouette envelope reconstruction** for dense aggregate regions such as stacked wood, rocks, debris, or folded small pieces when individual members do not control the primary outline or required negative space. Preserve the aggregate outer contour with a small polygon layout instead of copying every island.
- Voxel remesh, automatic remesh, or an undifferentiated whole-asset collapse remains forbidden. Decimate is authorized only for the complex region proven by the classification and region map.
- Inventory source connected components, boundaries, openings, thin layers, construction breaks, and deformation/highlight flow before reconstruction. Preserve those relationships in the deliberate low.
- If the first perspective or primary-view comparison clearly reads as a different object, reject it immediately and rebuild the relevant cage or patch. Do not polish a clean but wrong proxy.

Record `method_decision` as `controlled_direct_reduction`, `semantic_reconstruction`, or `hybrid_per_component`. A mixed asset must also record a `region_method_map` whose boundaries come from the high and whose methods distinguish complex soft surfaces, structured parts, and aggregate envelopes. Never infer a region from disconnected-island count or a face-count threshold alone.

For layered mixed assets such as cloth over wood, classify complete face-connected components before geometry. A controlled-reduction region must be copied with its original adjacency intact; never cut it from a connected surface with coordinate thresholds or partial-face selection. Build an aggregate envelope only from components excluded from the soft region. Covered envelope sections stay behind the intact soft shell, while exposed ends follow a sparse irregular contour rather than rectangular AABB caps.

## Build deliberately

- Use RetopoFlow interactively for complex surface drawing when available and appropriate.
- State truthfully whether RetopoFlow was used. Never describe scripted primitives, copied meshes, or automatic output as RetopoFlow hand drawing.
- Use manual cages, PolyStrips, Contours, patches, or deliberate primitive reconstruction as appropriate.
- Use temporary Shrinkwrap only as a fitting aid; inspect every region for collapse onto the wrong body part.
- Treat QuadriFlow, AutoRemesher, and voxel remesh as disposable diagnostics only. A Decimate result may become the formal low only for a classified complex continuous asset/region, from a fresh high-derived copy, with source preservation and zero-area cleanup recorded.
- Run `scripts/audit_topology_flow.py` on every review-ready automatic or mixed-triangle candidate. A manifold mesh with high silhouette IoU still fails when explicit triangle flow, poles, or density distribution are unusable.
- Record a plugin as used only when its operator actually generated or modified the accepted candidate. Installed, enabled, or background-skipped initialization is not plugin use.
- Do not add blanket Bevel modifiers.
- Allocate geometry according to curvature and silhouette value.
- Make every loop justify at least one of silhouette, curvature, attachment, deformation, shading, UV separation, or bake-cage control. Dissolve it otherwise.
- Prove density with region ablation: record a region's face count, remove a ring or planar subdivision, rerun six-view silhouette and shading checks, then keep or reject the reduction with a named reason.
- Flag crowded rings on stable curvature, repeated rings on straight spans, and abrupt density changes without a construction reason. For rotational props, allocate cap, rim, wall, shoulder, and base density independently.
- Start reduction on broad centers, straight walls, planar interiors, and other regions that do not change any primary-view silhouette. Do not begin by collapsing fingers, handles, rims, corners, or attachment roots.
- Keep cross-section density independent from lengthwise density: a round rim may need radial segments while its straight body needs only the rings that mark real profile changes.
- Reserve shallow wrinkles, grain, embossing, seams, dents, grooves, and micro-bevels for normal-map baking when they do not alter silhouette or negative space.

### Face decisions

- For boxy hard-surface props, begin with the minimum outer shell that wraps the high-poly silhouette. Add geometry only for real thickness, stepped layers, openings, negative space, feet, handles, latches, or profile changes that remain visible from a primary view.
- Treat panel graphics, shallow insets, stamped marks, screws, ribs, grooves, buttons, dents, surface waviness, and edge micro-detail as normal-map material unless they measurably change silhouette, negative space, or the bake cage.
- On a large planar panel, use one broad face or a small controlled triangle/quad layout; never reproduce the high-poly triangulation or distribute vertices merely because surface detail exists there.
- Prefer quads on curved directional flow, transitions, rims, and important highlight paths.
- Allow controlled triangles on flat panels, hidden areas, undersides, tiny caps, or where a triangle removes a redundant loop without changing the silhouette.
- Do not force a static organic prop to be all quads.
- Avoid long thin triangles, warped quads, high-valence radial fans across curved highlights, and all unintended N-gons.
- On visible explicit triangles, warn below 10 degrees minimum angle or above aspect ratio 6. Reject below 5 degrees, more than 2 percent below 10 degrees, more than 5 percent above aspect 6, any unexplained aspect above 20, or a visible smooth-region pole above valence 10. Allow only exact named hidden/thin-cap exceptions with wire and shading evidence.
- Flag adjacent polygon-area jumps above 6:1. Require a real curvature, attachment, thickness, or construction reason; otherwise regularize the local density.
- Keep box-like panels broad and mostly quad-based.
- Do not subdivide a planar face merely to imitate a regular grid.
- Terminate loops on stable planes or hidden regions; keep poles away from silhouettes, bevel highlights, openings, and deforming joints.
- Prefer a small local corner fan or center-quad cap over a single center vertex connected to many thin triangles.
- Use one continuous macro envelope for a vessel or molded body unless a true opening, contact break, or silhouette gap proves that separate shells are required. Do not interpret shallow bottom grooves as feet.
- Collapse repeated louvers, grille bars, ribs, and panel relief into one chamber or panel envelope when they do not change the outer silhouette or create a true hole. Bake the repetition; do not model every strip.
- Preserve a foot only when it changes ground contact or the primary silhouette. Preserve a support only when the user has not approved its omission and it affects the required construction read.

### Component decisions

- Join anatomical or molded continuities when the high-poly construction shows one continuous form.
- Keep mechanically separate pieces separate when doing so preserves construction, shading, or negative space.
- Do not fake one component with hidden tunnels while visible parts remain detached.
- Model visible roots and transitions so wings, legs, handles, and supports attach to the correct surface.
- Preserve openings and negative spaces before surface decoration; a correct hole or handle gap matters more than embossed detail.
- Allow intersecting closed parts only when the intended bake, shading, and engine pipeline support them and the intersection is hidden.

## Validate the authoritative candidate

Use the version-comparison loop below during training or when an unexpected validation failure must be diagnosed. In formal production, plan from the learned rules and create one authoritative low per high; do not schedule several alternative passes. Any rare correction remains unfinished work on that same formal object.

1. During training, give each materially different candidate a versioned name. During formal production, keep one authoritative object name and milestone saves without displaying several alternatives as formal passes.
2. Keep the audit candidate aligned over the high-poly and run `scripts/audit_pair.py` before adding any presentation-row offset.
3. Capture a clearly labeled two-row comparison: high-poly solid and generated low with topology. Use a display duplicate or a reversible display transform so the row offset does not contaminate bounds and center metrics.
4. Capture front, back, left, right, top, bottom, and perspective views with matched framing. Use orthographic projection for all size, center, and silhouette decisions. A perspective row is orientation-only evidence because near/far row offsets change apparent size.
5. Overlay the aligned high and low silhouettes in every orthographic view. Review silhouette, dimensions, center, orientation, ground contact, feature placement, cross-sections, component logic, face distribution, and shading.
6. Compare against the written topology plan and high-poly, not only the numeric audit.
7. If any orthographic view shows a visibly wrong controlling outline, thickness, opening, gap, attachment, or cross-section, reject the candidate even when topology checks pass; record the specific cause and rebuild it.
8. Treat 0.97 silhouette IoU as the default production target. A 0.95–0.97 view needs a written visual explanation and must not contain a wrong controlling outline. Reject a lower score by default unless the metric is demonstrably unstable for an extremely thin projection and the overlay is visually correct.
9. Do not add geometry merely to make raw surface-distance metrics pass when the measured difference is intentionally bakeable relief, grain, grooves, embossing, or micro-detail.
10. Store `topology_integrity_pass`, `silhouette_6view_pass`, `construction_pass`, `density_pass`, and `shading_pass` separately. Set `final_pass` only when all required gates pass; a mechanical audit alone is never proof of visual game readiness.
11. Store `wire_distribution_pass` separately. It requires a close wire review plus the topology-flow audit; never infer it from topology integrity, silhouette IoU, or face count.
12. Reject a denser version when a named non-silhouette region can lose at least 25 percent of its faces, or the whole asset can lose at least 10 percent, while every view loses no more than 0.005 IoU and no new target-distance shading crease appears.
13. Target per-axis dimension error at or below 2 percent, normalized center error at or below 1 percent, and ground-contact error at or below 1 percent of high height. Treat a larger error as a rebuild signal, not permission to scale-fit during assembly.
14. Measure unapproved controlling-outline residuals as well as IoU. Reject residuals greater than `max(2 px, 1 percent of projected maximum size)`. Apply a named omission mask only to the approved component and always retain the unmasked result.
15. Treat matching AABB dimensions and center as registration prerequisites only. Never infer silhouette, proportion, feature placement, or acceptance from AABB equality.
16. For high meshes above 200,000 faces, create the full high fingerprint/topology baseline once. During iterative edits use bounded local measurements, orthographic overlays, and lightweight silhouette captures; rerun the full high/low audit only on the selected final candidate or after any suspected source mutation.

Never open or present a rejected candidate as the final file.

## Run the bundled audit

Before modeling, create a high-only source baseline:

```text
<blender-executable> -b <project.blend> -P <skill>/scripts/audit_pair.py -- \
  --high <high_object> \
  --output <baseline.json> \
  --strict
```

After generating a low, audit the high/low pair against that baseline:

```text
<blender-executable> -b <project.blend> -P <skill>/scripts/audit_pair.py -- \
  --high <high_object> \
  --low <generated_low_object> \
  --output <audit.json> \
  --baseline <baseline.json> \
  --strict
```

Add `--require-closed` only when the asset is intended to be watertight. Intentional openings are otherwise reported but allowed.

For later audits, add `--baseline <earlier-audit.json>` to verify that the high fingerprint is unchanged.

The audit reports:

- Vertices, edges, faces, triangles, quads, N-gons, boundaries, loose geometry, and connected components.
- Multi-face non-manifold edges separately from intentional open boundaries.
- Duplicate vertices/faces, zero-area faces, inconsistent winding, and suspicious inward-facing closed shells.
- World-space bounds, dimension ratios, center offset, materials, modifiers, and source-preservation fingerprints.

The script does not validate silhouette quality, self-intersections, UV distortion, or bake quality. Perform the six-view and perspective visual review and state these limitations explicitly.

Run the topology-flow audit for every review-ready low that contains explicit triangles or came from an automatic generator:

```text
<blender-executable> -b <project.blend> -P <skill>/scripts/audit_topology_flow.py -- \
  --objects <generated_low_object> \
  --output <topology-flow.json> \
  --strict
```

This reports explicit-triangle angles and aspect ratios, vertex valence, local polygon-area jumps, and degenerate faces. It does not know whether a face is hidden, a thin-cap exception is legitimate, or the visible flow follows the asset's form. Inspect close wire views and document any exact exception; never waive an entire object by category.

For a final comparison-row Blend, run the bundled layout audit after rendering and reopening the saved file:

```text
<blender-executable> -b <final.blend> -P <skill>/scripts/audit_batch_layout.py -- \
  --low-prefix NEXTGEN_ \
  --low-suffix _FINAL \
  --expected <count> \
  --expected-asset-ids <id-1> <id-2> \
  --row-offset 0 <offset-y> 0 \
  --baseline <aligned-layout-audit.json> \
  --strict-final \
  --output <layout-audit.json>
```

Capture an aligned audit first with row offset zero. Pass that v3 file through `--baseline <aligned-layout-audit.json>` after translation to prove the mesh fingerprint, dimensions, full 3x3 transform, object origin, and AABB center were preserved by the requested row translation. This catches inherited Actions/NLA/drivers, parents, modifiers, links/overrides, stale role metadata, missing/duplicate pairs, accidental `.001` names, render-ineligible objects, row overlap, and post-render drift. Its `mechanical_layout_pass` does not evaluate six-view visual quality or prove that an unoccluded object contributed pixels; inspect the final render or validate an Object Index/Cryptomatte sidecar.

## Batch and live-session safeguards

- Audit candidates aligned over their highs; use translation-only copies for presentation rows. Never scale a low during final assembly to hide a modeling mismatch.
- Clear object and mesh animation data on static final lows. A transform Action can evaluate during rendering and silently move a correctly arranged object back onto the high.
- Reject unresolved parents, constraints, modifiers, shape keys, drivers, or library links in accepted lows.
- Normalize role metadata after appending candidates so a copied low cannot remain labeled `HIGH`.
- Preflight the complete manifest before mutating the aggregate. Append exact, self-contained objects only; do not link whole libraries or accept automatic Blender renaming.
- Record plugin work as `attempted`, `selected_initial_generator`, and `final_correction`, with actual operator evidence. A rejected plugin trial remains history but is not the final method.
- Final row assembly is translation-only. Compare the full world 3x3 matrix, determinant, dimensions, and mesh fingerprint before and after; `scale=(1,1,1)` by itself is insufficient evidence.
- Disable Auto Key during layout, clear low-only Actions/NLA/drivers, preserve source-high animation, and restore the user's frame/playback/session state.
- Use unique staging/output paths and one writer per Blend. Do not delete a shared candidate while Blender or another worker may still read it.
- Render the comparison, save, reopen, then verify count, pair mapping, row centers, static state, and visibility. Pre-render checks alone are insufficient.
- For iterative user review, preserve existing completed low rows and append each newly accepted version as another translation-only row with its own versioned collection and visible row label. Do not overwrite an earlier review row unless the user explicitly requests replacement. Keep only the newest accepted row authoritative for export; mark older rows as review-only.
- When several Blender instances expose the same connector, keep the exact-file guard inside the executed Blender code; never rely only on the port or most recently focused window.
- Preserve any dirty live scene with `save_as_mainfile(..., copy=True)` before opening a final deliverable. Do not open another Blender when the user asked to reuse the current one.

## Accept and deliver

Deliver only when:

- The generated low reads as the same high-poly asset in all six orthographic views and perspective.
- The topology follows the learned sparse-prop rules and the written per-asset plan.
- The high fingerprint matches the baseline.
- There are no unintended N-gons, multi-face non-manifold edges, loose or duplicate geometry, zero-area faces, or inconsistent normals.
- Open boundaries are intentional; use `--require-closed` when none are allowed.
- Triangles and quads are distributed deliberately.
- `wire_distribution_pass` is true after both quantitative topology-flow audit and close visible wire review. High IoU or manifold status cannot substitute for this gate.
- Flat areas are sparse, curved cross-sections use the lowest segment count that passes silhouette inspection, and small non-silhouette detail is reserved for baking.
- The final candidate has a clear, separate object name.
- High and final low remain available for review.
- A comparison image and audit JSON are saved.
- The saved comparison Blend was reopened and passed the batch layout audit; every low remains in its intended row and no static low carries animation data.
- All six independent product gates pass for every accepted candidate: topology integrity, silhouette, construction, density, wire distribution, and shading.
- The face budget, geometry decisions, intentional omissions, and at least one density-ablation result are stored with the accepted candidate.
- UV, cage, material, and bake status are stated explicitly.

Report the accepted file path and object name, topology counts, rebuild rationale, six-view comparison image, source-preservation result, and remaining UV/cage/material/bake work.

Do not claim RetopoFlow use, bake readiness, or completion without direct evidence.
