# Production retopology runbook

Use this runbook for batch assets, plugin-assisted work, live Blender control, candidate iteration, comparison-row assembly, and final delivery. The high-poly remains the only modeling authority; a previous low may be preserved as user data but is never an input or dependency.

## Contents

- [Acceptance model](#acceptance-model)
- [Training-calibrated formal execution](#training-calibrated-formal-execution)
- [End-to-end workflow](#end-to-end-workflow)
- [Candidate manifest and quality gates](#candidate-manifest-and-quality-gates)
- [Asset-class lessons](#asset-class-lessons)
- [Geometry-versus-bake decisions](#geometry-versus-bake-decisions)
- [Metric interpretation](#metric-interpretation)
- [Plugin evidence](#plugin-evidence)
- [Batch assembly invariants](#batch-assembly-invariants)
- [Empirical calibration](#empirical-calibration)
- [Failure patterns](#failure-patterns)
- [Delivery record](#delivery-record)

## Acceptance model

Resolve every decision in this order:

1. Identity and primary-view silhouette.
2. Dimensions, center, orientation, and ground contact.
3. Openings, negative space, thickness, and overlap order.
4. Major construction, attachments, and cross-sections.
5. Face distribution and predictable shading.
6. Polygon economy.
7. Bakeable surface detail.

A manifold mesh can still be a failed low. Do not keep a clean topology if it reads as another object. Do not spend faces on a groove while a handle, rim, finger, lid, or controlling profile is wrong.

Store these gates independently for every candidate:

- `topology_integrity_pass`
- `silhouette_6view_pass`
- `construction_pass`
- `density_pass`
- `wire_distribution_pass`
- `shading_pass`

Set `final_pass` only when every required gate is true. A layout audit proves saved arrangement and dependency hygiene; it does not prove visual game-readiness.

## Training-calibrated formal execution

Use rejected examples and repeated trials only to train and freeze the method. The formal target is first-candidate accuracy:

- Finish high analysis, local measurements, feature controls, construction decisions, topology-flow planning, profile/radial counts, and geometry-versus-bake assignment before formal geometry.
- Create one authoritative formal low for each high and apply the learned method correctly from the first operation.
- Do not plan three formal attempts, three colored versions, or a global seed/ratio search in the user's production file.
- Use comparison and audit to prove the one formal candidate is correct. If an unexpected miss appears, it remains unfinished work on the same authoritative object; correct the cause and update the training lesson rather than starting routine blind variants.
- Keep training candidates hidden or superseded and exclude them from the formal review row and export manifest.

## End-to-end workflow

### 0. Secure the session and output scope

1. Identify the exact live Blend, high objects, dirty state, output directory, and requested Blender-window behavior.
2. Create a new versioned destination and a persistent work directory next to it. Do not make a temporary directory the only copy of accepted candidates.
3. If the live file is dirty, save a copy before loading another file. Preserve the original filepath and user edits.
4. Guard every live mutation with the exact expected `bpy.data.filepath`. When several Blender sessions expose the same port, perform the guard inside Blender code.
5. Snapshot mode, active object, selection, active collection, view layer, hidden/isolation state, camera, frame, frame range, fps, playback, and Auto Key. Also snapshot source-high Actions, NLA tracks, drivers, and transforms. Disable Auto Key during scripted layout and restore the user's state except for the requested final review arrangement. Never clear source-high animation while sanitizing a generated low.
6. Never silently open a second visible Blender. Use background workers only on copies or versioned outputs.
7. Give every worker a unique staging directory and output. Use one writer per Blend, fail if a destination already exists unless an explicit overwrite was requested, and do not clean shared candidates while Blender or another worker may still read them.

### 1. Inventory and baseline

For every asset, record:

- Asset ID and exact high object name.
- World bounds, center, dimensions, orientation, and ground contact.
- High topology and component count.
- High boundary-edge count, true openings, thin layers, intersecting shells, and whether product-level continuity differs from technical mesh connectivity.
- Asset class and primary visible parts.
- High fingerprint from `scripts/audit_pair.py`.
- Existing generated lows as preserved but non-authoritative user data.

Build an explicit high-to-low manifest. Do not infer pairs from screen order alone.

The manifest must be machine-verifiable and contain the expected ordered `asset_id` set. For every candidate record exact `source_library`, `source_object`, `candidate_version`, `status`, mesh fingerprint, method, and quality gates. Require exactly one accepted candidate per asset. Reject missing IDs, duplicate accepted candidates, missing objects, unexpected Blender `.001` renames, and fuzzy prefix matches. The newest version is not automatically the accepted version.

### 2. Write a topology plan

For each asset, write:

- `method_decision`: `controlled_direct_reduction`, `semantic_reconstruction`, or `hybrid_per_component`, with the selected region strategy and any user-approved method instruction. Mixed assets also record a `region_method_map` with high-derived boundaries.

- Primary silhouette volumes.
- True openings and negative spaces.
- Real steps, thickness, attachments, and separate parts.
- Details assigned to normal baking.
- Initial radial and lengthwise segment counts.
- Planned triangles, quads, poles, caps, and loop terminations.
- Planned connected components and the construction reason for every separate shell.
- A target face band and hard maximum.
- `geometry_decisions` for disputed details, using only `keep_geometry`, `bake`, or `omit`, plus `authority: user_approved` where applicable.
- Named intentional omissions and the views they affect.

Do this before running a plugin or creating dense geometry.

### 3. Build by the region map

Choose the method by construction:

- For a structurally complex, genuinely integrated continuous object whose original high already has the correct macro surface, silhouette, openings, and attachment placement, duplicate the untouched high and run one controlled asset-specific reduction. Saddles, complex boots, cloth and leather regions are eligible when a coarse proxy would visibly lose identity. Do not rebuild a guessed primitive shell, reuse a rejected low, or decimate one reduced candidate again.
- For dense aggregate regions such as stacked wood, rocks or debris, build a sparse polygon envelope from the high-derived outer contour when individual members and gaps do not control the primary read. Do not treat every disconnected island as an object.
- For cloth/leather covering an aggregate, account for export-split duplicate vertices: when duplicate ratio exceeds 20 percent or component count is implausibly high, rebuild classification-only adjacency across coincident coordinates within `high diagonal * 1e-6`. Do not mutate the source or result with this weld. Copy whole recovered surfaces and never spatially clip faces. Connectivity only proves whether a region split is safe and never proves that the asset is complex. Reduce deeply folded soft regions to an absolute silhouette-driven density rather than a fixed retained percentage, preserve boundaries, and use smooth shading without shape-changing smoothing. Exclude soft surfaces from aggregate samples. Build one sparse non-circular contour extrusion from paired 8–24 point exposed-end loops with up to three intermediate loops. Never use cylinders, capsules, ellipses, cones, boxes or generic lobes as aggregate delivery geometry; conservatively reduce the aggregate source region if reliable contour extrusion cannot be built. If the recovered graph is one fused soft-and-hard surface, do not run repeated region-splitting scripts: use the packaged adaptive server whole-source reduction without a fixed ratio, retaining source export seams, then stop.
- Manual primitives, profile rings, patches, and separate closed shells for box-like, rotational, and multipart props.
- RetopoFlow surface drawing for shapes that genuinely benefit from interactive strips or contours.
- QuadriFlow only as an initial continuous cage for suitable organic or rounded forms.

Create large forms first. Build openings, handle gaps, attachment roots, and rim thickness before decoration. Keep logical mechanical parts separate when that gives cleaner construction and baking.

Voxel remesh and raw QuadriFlow remain disposable diagnostics. Controlled Decimate may be promoted only for the classified complex continuous region from a fresh high-derived copy; record its responsibility boundary and reduction settings, preserve the source, and clean zero-area faces. Structured and aggregate regions must still follow their own methods.

### 4. Fit globally before fitting locally

1. Match world bounds, center, orientation, and ground contact.
2. Match front, side, and top controlling profiles.
3. Fit attachments and openings.
4. Use Shrinkwrap or nearest-surface projection only with part masks and distance limits.
5. Inspect thin walls, parallel layers, fingers, handles, rims, and nested parts for projection onto the wrong surface.

Do not use a final assembly scale operation to repair a modeling mismatch.

### 5. Run a deliberate sparse pass

Reduce in this order:

1. Broad planar centers.
2. Straight walls and uninterrupted spans.
3. Planar interiors and hidden undersides.
4. Lengthwise rings that do not mark a profile change.
5. Bakeable relief and surface noise.

Protect:

- Primary-view silhouettes.
- Fingers, handles, rims, and corner radii.
- Openings and negative-space boundaries.
- Attachment roots and ground contact.
- Real steps, thickness changes, and profile breaks.

Ask what every loop does. Dissolve it if it does not support silhouette, curvature, construction, attachment, deformation, shading, UV separation, or bake-cage control.

Use region ablation instead of intuition alone:

1. Record faces before the test for the named component or region.
2. Dissolve one ring, segment set, or planar subdivision group.
3. Re-run six-view silhouette and shading checks.
4. Keep the reduction only if all controlling views and highlight transitions remain acceptable.
5. Record faces after the test and its accepted or rejected reason.

Fail the denser candidate when the test removes at least 25 percent of a named region or 10 percent of the asset, the worst-view IoU drop is at most 0.005, and target-distance shading gains no new crease. Do not accept density merely because it came from an all-quad or plugin-generated result.

Run a curvature-density audit. Flag rings crowded on nearly constant curvature, repeated rings on long straight spans, and abrupt density changes without a profile or attachment reason. For rotational assets, rebuild from radius and slope events; budget cap, rim, wall, shoulder, and base independently instead of propagating one dense ring count everywhere. Any asset above its working face band needs a per-view or construction reason, not a generic "quality" justification.

Run `scripts/audit_topology_flow.py` before showing a review-ready candidate. For visible explicit triangles, use 10 degrees minimum angle and aspect ratio 6 as warnings; reject below 5 degrees, more than 2 percent below 10 degrees, more than 5 percent above aspect 6, any unexplained aspect above 20, or a visible smooth-region pole above valence 10. Flag adjacent polygon-area jumps above 6:1. Exact hidden or thin-cap exceptions require named faces or regions plus close wire and shading proof.

### 6. Review six orthographic views and perspective

Keep the candidate aligned over the high. Capture front, back, left, right, top, bottom, and perspective at matched framing. Create silhouette overlays and record the exact residual region for every weak view.

Use this loop:

1. Render.
2. Measure.
3. Name the visible cause.
4. Move only the responsible control points or rebuild the responsible component.
5. Render again.

Do not add blanket density or rebuild unrelated regions to improve one view.

For the first asset of a new batch, add a mandatory representative stop gate:

1. Show high solid with wire disabled and low solid with wire enabled.
2. Include six orthographic views, perspective, and at least one useful close wire view.
3. Report faces, evaluated triangles, component and boundary changes, silhouette results, and topology-flow results.
4. Stop and wait for user confirmation before starting the remaining assets.

Do not treat manifold status, high IoU, a low face count, or an orderly material preview as representative approval.

### 7. Run strict structural audits

Require:

- High fingerprint unchanged.
- No unintended N-gons.
- No loose, duplicate, or zero-area geometry.
- No multi-face non-manifold edges.
- Intentional boundary policy.
- Consistent orientation and outward closed shells.
- No unapplied production modifiers or hidden dependencies.
- Reported faces and evaluated triangles.
- A passing topology-flow report plus close wire inspection.

Keep visual and structural gates separate. Both must be reviewed.

Component count is evidence, not a universal target. Compare it to the topology plan and real construction. Many intentional closed parts can be valid; one fused component can still be wrong when it destroys a gap.

### 8. Assemble the batch

1. Run a full preflight without mutating the aggregate: validate the ordered asset IDs, one accepted candidate each, exact library and object names, mesh type, fingerprint, self-contained state, quality gates, and destination availability.
2. Append only the exact accepted objects into a new aggregate file. Never link a library, append a whole collection, select by fuzzy prefix, or rely on Blender's automatic name suffixing.
3. Rename them deterministically and store `asset_id`, `source_high`, source library, candidate version, method, plugin evidence, and stage status. Remove inherited role/status/source custom properties before writing the final controlled metadata.
4. Clear object and mesh animation data for static lows.
5. Reject parents, constraints, modifiers, shape keys, drivers, and library links.
6. Normalize role metadata to `LOW`.
7. Capture each accepted low's mesh fingerprint, dimensions, full world 3x3 matrix, determinant, rotation, scale, and shear state before layout. Translate it to the review row from evaluated world AABBs. Do not scale or rotate it. After layout, assert the fingerprint, dimensions, and full 3x3 matrix are unchanged.
8. Keep every high unchanged in the high row.
9. Keep asset IDs and row labels in a separate review-only collection; never join them to asset meshes or include them in export.
10. When earlier completed low rows exist, preserve them as review-only history and place the new accepted version on another row. Give every version row its own collection, row index, version label, and translation-only offset. Never overwrite or delete an earlier row without explicit user instruction.
11. Use explicit manifest order such as H01 through H15, not lexical name order. Put one high-low pair per column and calculate both column and row spacing from AABBs plus a safety gap so objects and labels do not collide or occlude.
12. Keep exactly one export-authoritative accepted low per asset. Older row objects are display copies or superseded candidates and must be excluded from game export and from the current accepted manifest.
13. Confirm collection visibility, object visibility, current view layer, camera framing, and review material/display state. Save with the requested review arrangement visible.
14. Render one image that visibly contains every pair and every requested comparison row.

### 9. Reopen and verify

Rendering can evaluate animation and dependencies after an earlier audit. Therefore:

1. Save the aggregate Blend.
2. Reopen the saved file in a separate background verification process.
3. Run `scripts/audit_batch_layout.py`.
4. Confirm exact count, names, manifest order, roles, mesh fingerprints, dimensions, full transforms, center offsets, ground relation, and high fingerprints.
5. Confirm object and mesh Actions, NLA tracks, drivers, shape keys, parents, constraints, modifiers, and library links are absent from static lows.
6. Confirm collection/view-layer visibility and inspect the rendered review. Count every pair visually and check that no object is occluded, overlapped, or outside frame.
7. Keep layout/mechanical audit results separate from the six visual-product quality gates.
8. Only then load the final into the user's existing Blender window, preserving a dirty session copy first.

## Candidate manifest and quality gates

Use one immutable candidate record per version. A compact record can look like this:

```json
{
  "asset_id": "H03",
  "row_index": 3,
  "high_object": "HIGH_H03",
  "low_object": "NEXTGEN_H03_FINAL",
  "source_library": "candidate_H03_v004.blend",
  "source_object": "NEXTGEN_H03_v004",
  "candidate_version": 4,
  "status": "accepted",
  "mesh_fingerprint": "sha256-or-audit-fingerprint",
  "method": "manual_profile_rings",
  "face_budget": {"target_min": 160, "target_max": 240, "hard_max": 280},
  "geometry_decisions": [
    {"region": "shallow_bottom_grooves", "decision": "bake", "authority": "high_only_analysis"}
  ],
  "intentional_omissions": [],
  "density_ablation": [
    {"region": "broad_center", "faces_before": 180, "faces_after": 110, "max_iou_drop": 0.003, "accepted": true}
  ],
  "plugin": {
    "attempted": ["QUADRIFLOW_REMESH"],
    "selected_initial_generator": null,
    "final_correction": "manual shoulder, neck and cap rebuild"
  },
  "quality": {
    "topology_integrity_pass": true,
    "silhouette_6view_pass": true,
    "construction_pass": true,
    "density_pass": true,
    "wire_distribution_pass": true,
    "shading_pass": true,
    "final_pass": true
  }
}
```

Rules:

- Candidate selection uses the worst six-view result, construction accuracy, density economy, and shading—not modification time or largest version number.
- Preserve rejected candidates and record a short rejection reason such as fused opening, wrong side silhouette, crowded cap, or planar over-density.
- Do not mutate the aggregate until every manifest entry passes preflight.
- Append in one controlled transaction after preflight. If any append or post-append invariant fails, abandon that aggregate version and rebuild from accepted sources rather than partially patching an uncertain assembly.
- Connected-component count must match the planned construction. Report unexpected extra shells and accidental fusion separately.
- The automated visibility audit proves scene/view-layer membership and render eligibility. It does not prove that the object contributed unoccluded pixels. Inspect the saved render, or produce an Object Index/Cryptomatte pixel-count sidecar for machine-verifiable batch visibility.

## Asset-class lessons

### Static organic bodies

- Preserve the large gesture and every appendage root before wrinkles or lobes.
- Remove rings from broad torso centers before wings, legs, necks, tails, or contour crests.
- Permit controlled triangles on static internal regions; do not force a uniform all-quad grid.
- A QuadriFlow cage can be useful, but rebuild fused roots, misplaced poles, and uniform-density centers.
- For chicken-like static props, use 450-650 faces with about 550 as the first pass. Treat more than 650 as a density failure unless hero scale or deformation is proven.

### Hands and finger-like forms

- Protect each finger, thumb, web space, fingertip, and palm-to-wrist outline.
- Reduce the broad palm center first.
- Reject finger fusion, wrong spacing, or a thumb that only matches from one view.

### Bowls, cups, buckets, and bottles

- Choose radial sides from silhouette distance; choose vertical rings only at radius or slope changes.
- Keep flat bottoms and broad interiors sparse with controlled triangles/quads.
- Give visible rims real thickness.
- Build handles and pivots from the negative-space outline, then tune tube thickness.
- A correct body does not excuse a wrong handle in side, front, or top view.

### Kettles, pumps, and necked containers

- Separate body, shoulder, neck, cap, spout, and handle decisions.
- Use staged profile rings at the shoulder and top. Avoid a crowded, collapsed radial fan.
- Keep cross-section density independent from height density.
- Fit the top, cap, and nozzle in side and top views; these regions reveal pinching quickly.
- For handled necked containers, use one continuous macro shell, 12-16 body sides, and about 6-7 true silhouette or profile events. Do not turn shallow base channels into independent feet. Target 160-240 faces and fail above 280 without explicit silhouette evidence.

### Boxes, coolers, crates, and lanterns

- Start from the minimum silhouette shell.
- Model real lid/body steps, thickness, corner radii, feet, latches, openings, and handle gaps.
- Use one broad face or a few controlled faces on a large panel.
- Bake ribs, stamped marks, grooves, logos, buttons, shallow frames, screws, and surface noise unless they alter silhouette or create true negative space.
- For a lantern or louvered box, keep only the base, macro chamber or frame, eaves, roof, and finial. Set individual louver geometry count to zero when the strips create no true opening. Target 110-170 faces and fail above 200.

### Thin boards and clip-like props

- Match thickness, edge radius, clip opening, forward curvature, and bottom transition. Match support angle only when the support is retained and affects the required silhouette or ground contact.
- Do not recreate a rear support after the user approves its omission. For a comparable board, target 45-85 faces and fail above 110.
- Inspect pixel overlays because a few pixels can produce a low percentage score on an extremely thin projection.
- Do not use metric sensitivity to excuse a visibly wrong thickness or opening.

### Multipart assemblies

- Preserve overlap order and contact surfaces.
- Use separate closed cages for logical pieces.
- Do not create hidden tunnels merely to connect parts.
- Verify 2D negative space, not only 3D component count.

## Geometry-versus-bake decisions

Use geometry for:

- Outer silhouette and ground contact.
- True openings and visible gaps.
- Real thickness and profile steps.
- Large corner radii and highlight bands.
- Handles, fingers, rims, latches, feet that change ground contact or silhouette, and attachment roots.
- Supports only when retained by the topology plan and required by silhouette, contact, or construction.
- Details that alter shadow or silhouette at the intended viewing distance.

Use normal or texture baking for:

- Grain, wrinkles, dents, scratches, and noise.
- Logos, stamped marks, panel graphics, shallow embossing, and recesses.
- Screws, ribs, buttons, grooves, seams, and micro-bevels that do not change silhouette.
- Dense high-poly triangulation on an otherwise planar surface.

If a raw surface-distance metric penalizes an intentionally bakeable detail, document it instead of adding wasteful topology.

## Metric interpretation

- Target at least 0.97 silhouette IoU in each orthographic view.
- Treat 0.95–0.97 as a review band. Name the residual region and accept only when the controlling outline remains visually correct.
- Reject below 0.95 by default. An extremely thin projection may justify an exception only when the pixel overlay proves the real thickness, opening, and profile are correct.
- Compare high-to-low and low-to-high distances. One direction alone can hide an oversized or undersized cage.
- Do not require raw point-to-surface distance to pass when the difference is intentionally bakeable relief.
- Do not optimize component or hole counters blindly. Separate valid closed parts can alter 2D counts while preserving the actual visible gap.
- Numeric acceptance never overrides a visible silhouette failure.
- Target each principal dimension within 2 percent, normalized center offset within 1 percent, and contact height within 1 percent of high height.
- Reject an unapproved controlling-outline residual above `max(2 px, 1 percent of projected maximum size)`.
- Allow a named omission mask only for the exact user-approved component. Keep and report the unmasked comparison so a mask cannot hide body-shape errors.

## Plugin evidence

Record one of these states per accepted asset:

- Plugin generated the initial candidate; list the plugin and the manual corrections.
- Plugin was used interactively for a named cleanup or drawing operation.
- Plugin was attempted and rejected; state why it was rejected.
- No plugin was used for the final mesh.

Installed, enabled, mentioned in startup logs, or skipped in background initialization does not count as use. Never call automatic output hand-retopology. Do not keep a plugin result when semantic manual construction is cleaner and sparser.

Track plugin history in three separate phases:

- `attempted`: every operator actually invoked, including rejected trials.
- `selected_initial_generator`: the plugin/operator that produced the base of the accepted candidate, or null.
- `final_correction`: manual or plugin-assisted corrections that created the final mesh.

Where available, record add-on version, Blender operator identifier, material parameters, execution log, output object name, and an object custom-property stamp. An attempted and rejected plugin still belongs in history, but it must not be presented as the final method.

## Batch assembly invariants

The final aggregate must satisfy:

- Exactly one accepted low per manifest asset.
- Deterministic names and correct `source_high` mapping.
- Translation-only presentation offset.
- Original low dimensions preserved during assembly.
- Full low 3x3 transforms, determinants, and mesh fingerprints preserved during assembly; translation is the only permitted presentation change.
- Static lows have no object or mesh Action, NLA track, driver, or other animation data.
- No parent, constraint, modifier, shape key, driver, or library link.
- Normalized `LOW` role metadata.
- Every high fingerprint unchanged.
- Explicit manifest order, one pair per column, AABB-derived spacing, and no collision or occlusion.
- Preserve versioned historical review rows when requested; use one labeled collection per row and mark all non-current rows review-only.
- Exactly one export-authoritative accepted low per asset even when multiple comparison rows are visible.
- All review objects and collections visible in the saved view layer and final file opens on the intended review arrangement.
- Every low visible in the saved review image.
- Post-render, post-save, and post-reopen layout verification passed.

## Empirical calibration

The older dense results below are rejected intermediate calibrations, not success targets:

| Asset behavior | Earlier faces | Accepted faces | Lesson |
|---|---:|---:|---|
| Dense organic body | 2,579 | 1,147 | Rejected as uniformly dense; rebuild toward 450-650 and reduce torso centers first. |
| Open bowl | 640 | 425 | Reduce radial and interior subdivisions while preserving the opening and rim. |
| Necked/handled container | 1,476 | 542 | Rejected as overbuilt; rebuild a continuous envelope toward 160-240. |
| Underbuilt silhouette | 120 | 268 | Add faces when necessary to restore identity, thickness, attachment, or controlling profile. |
| Mixed batch total | 12,581 | 7,300 | A roughly 42% interim reduction was achieved by region-specific decisions, not a uniform ratio. |

The lesson is asymmetric: remove aggressively where faces do not affect the product read, and add deliberately where a sparse cage fails the high silhouette or construction. Never treat a batch-wide percentage as a per-asset target.

The later V08R user-density revision reduced the 7,300-face interim batch to 6,285 faces and 11,312 evaluated triangles with zero N-gons and zero hard topology failures. It changed only four named assets instead of reprocessing the batch: chicken-like organic 558 faces, thin board 68 faces with the approved rear support omitted, macro lantern 108 faces with all individual louvers removed, and handled container 230 faces with a continuous body envelope and no separate feet. Treat these numbers as comparable-case anchors, not universal quotas. Read `validated-batch-retrospective.md` for the complete per-asset table and the N06 topology-flow failure.

## Failure patterns

Reject or repair these patterns:

- Uniform remesh density across broad planar or straight regions.
- Dense high-poly triangulation copied onto a low panel.
- Long sliver triangles or high-valence fans on visible highlights.
- Crowded top vertices that pinch a cap, neck, or shoulder.
- Missing handle gaps, finger webs, rim openings, or feet clearance.
- Shrinkwrap projection onto a neighboring parallel surface.
- Final scaling used to hide a shape mismatch.
- Auditing a presentation-offset low as though it were aligned.
- A static candidate carrying a transform Action that resets its row during render.
- Auto Key or an NLA strip silently writing or evaluating layout transforms.
- Inherited metadata labeling a low as high.
- Fuzzy object selection, automatic `.001` renaming, or accepting the newest candidate without comparison.
- A mechanical `passed: true` being reported as proof of six-view visual quality.
- Lexical row order, fixed spacing that causes overlap, or saved collections hidden from the current view layer.
- Multiple workers writing one Blend or deleting a candidate another process still reads.
- Declaring plugin use without operator evidence.
- Declaring success from topology validity while the silhouette is wrong.
- Adding bakeable detail merely to game a surface-distance score.
- Modeling every louver, grille strip, or shallow rib despite no silhouette or true-hole contribution.
- Recreating a support or other component after a user-approved omission.
- Interpreting shallow molded bottom grooves as independent feet.
- Keeping a uniform organic grid across broad non-silhouette torso regions.
- Applying Decimate outside the classified complex region, changing the source, or using one whole-asset collapse across otherwise distinguishable mixed regions.
- Welding a technically multi-shell/open source into one closed component without an explicit construction reason and opening review.
- Starting the rest of a batch before the first representative asset has passed close wire review and received user confirmation.

## Delivery record

Report:

- Final Blend path and accepted object names.
- Per-asset faces and evaluated triangles.
- Total reduction and any asset that intentionally gained faces to repair shape.
- Plugin evidence per asset.
- Six-view overlay and named residual regions.
- Topology-flow audit, close wire evidence, and any exact named hidden/thin-cap exceptions.
- Face budget, geometry decisions, user-approved omissions, and density-ablation evidence.
- Strict topology and source-preservation results.
- Final layout audit and visible pair count.
- Live-session backup path when one was created.
- Explicit UV, cage, material, LOD, and bake status.

Do not call the asset game-ready for export if topology is complete but UV, cage, materials, or baking remain undone.
