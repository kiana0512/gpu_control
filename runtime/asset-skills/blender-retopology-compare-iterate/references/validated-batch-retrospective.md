# Validated batch retrospective: H01-H15 and the N06 boot failure

Read this reference before starting a new batch or choosing an assisted reconstruction tool. It records historical trials that led to the current workflow. Direct-reduction examples below are rejected history, not production permission.

## Contents

- [Verified artifacts](#verified-artifacts)
- [What happened across the batch](#what-happened-across-the-batch)
- [Accepted H01-H15 calibration](#accepted-h01-h15-calibration)
- [What the V08 revision proved](#what-the-v08-revision-proved)
- [What the N06 boot failure proved](#what-the-n06-boot-failure-proved)
- [Mandatory method decision](#mandatory-method-decision)
- [Mandatory representative-asset stop gate](#mandatory-representative-asset-stop-gate)
- [Mandatory wire-distribution gate](#mandatory-wire-distribution-gate)
- [Reusable per-class construction lessons](#reusable-per-class-construction-lessons)
- [Batch assembly and evidence lessons](#batch-assembly-and-evidence-lessons)
- [Failure-response matrix](#failure-response-matrix)

## Verified artifacts

The conclusions below were reconstructed from the saved Blend versions, build scripts, six-view manifests, strict pair audits, topology audits, and final review images for the H01-H15 batch and the later N06 trial. The authoritative accepted H01-H15 stage is V08R.

Verified H01-H15 totals:

- Earlier clean-looking product pass: 29,092 modeling faces, 99.78 percent quads. This passed many structural checks but was far too dense to be the sparse game topology the user wanted.
- First high-only sparse batch before the last user-density revision: 7,300 modeling faces.
- Accepted V08R batch after four targeted revisions: 6,285 modeling faces, 11,312 evaluated triangles, zero N-gons, and zero hard topology failures.
- The final reduction from 7,300 to 6,285 was not a uniform ratio. It came from H01, H05, H06, and H07 region-specific corrections while the other eleven accepted assets were preserved.

The accepted V08R topology audit preserved all fifteen high fingerprints and used translation-only display copies. UVs, bake cages, materials, and texture baking were not part of that topology-stage acceptance.

## What happened across the batch

### 1. The over-dense clean-topology phase

The first product pass emphasized all-quads, manifold structure, bounds matching, and clean row presentation. It reached 29,092 faces with 29,028 quads and only 64 triangles. That result proved that structural cleanliness and a high quad percentage do not prove game-readiness. Many flat, straight, hidden, or bakeable regions still carried dense loops.

Reusable lesson:

- Never use all-quad percentage, manifold status, or a visually orderly grid as the density acceptance criterion.
- Require every loop to justify silhouette, curvature, construction, attachment, deformation, shading, UV separation, or bake-cage control.
- Reject an otherwise clean mesh when named non-silhouette regions can be reduced without measurable silhouette or shading loss.

### 2. The high-only sparse rebuild phase

The next pass rebuilt or reduced each asset according to its construction instead of applying one batch ratio. Broad planes were rebuilt from minimal shells, rotational bodies from profile events, repeated structures from macro envelopes, and organic shapes from high-fitted cages with density removed from broad centers first.

This pass reduced the batch to 7,300 faces. It also demonstrated the asymmetric rule: density sometimes had to increase. H04 had been underbuilt at about 120 faces and increased to 268 faces because the sparse proxy did not preserve the high silhouette and construction.

Reusable lesson:

- Polygon economy is subordinate to identity.
- Remove faces aggressively where they do not affect the product read.
- Add faces deliberately when a controlling profile, opening, attachment, or ground-contact shape is wrong.
- Never treat a batch-wide percentage as a per-asset target.

### 3. The user-density revision phase

The user identified four remaining overbuilt or semantically wrong assets: H01, H05, H06, and H07. They were revised independently through V08, V08B, V08C, V08D, V08F, and V08G review files. Failed versions were retained rather than silently overwritten.

The first V08 candidates were not accepted merely because they were sparse:

- H01 at 515 faces lost too much side and front silhouette; worst-view IoU was about 0.9584.
- H05 at 78 faces intentionally omitted the rear support, so unmasked side and top metrics collapsed. The omission had to remain named, user-approved, and visible in unmasked comparisons.
- H06 at 138 faces was slightly oversized in several views; the macro idea was correct but needed tighter bounds and less geometry.
- H07 at 202 faces did not preserve the handled body profile strongly enough; later versions added controlling profile events before being reduced again.

Final targeted revisions:

- H01: 1,147 to 558 faces, about 51 percent reduction. Preserve wings, legs, tail, attachment roots, and silhouette crests; remove broad body-center density and bake wrinkles.
- H05: 172 to 68 faces, about 60 percent reduction. Preserve the board shell, thickness, clip carrier, and true hanging hole; omit the rear support only because the user explicitly approved it.
- H06: 382 to 108 faces, about 72 percent reduction. Replace individual louvers and micro-frames with one chamber envelope; preserve base, eaves, roof, and finial.
- H07: 278 to 230 faces, about 17 percent reduction. Use one continuous body envelope with rings only at true profile events; preserve the handle opening; remove false pointed feet and bake shallow ribs.

### 4. The comparison-row alignment failure

An earlier comparison delivery contaminated aligned auditing with presentation offsets. The superseded file was retained with an alignment-bug label. The corrected workflow audits the low while aligned over the high, stores its fingerprint and full world 3x3 transform, then creates a translation-only review copy.

Reusable lesson:

- Never audit a presentation-offset low as though it were aligned.
- Never scale during final row assembly to hide a shape mismatch.
- Reopen the saved Blend after rendering and verify fingerprints, dimensions, transforms, roles, visibility, and row placement.

### 5. The new-asset N06 method failure

The first N06 boot attempts used a clean semantic proxy. They were structurally tidy but read as a different object. This violated the top acceptance priority and should have been rejected immediately from the first perspective comparison.

The historical method showed why a coarse proxy loses the identity of a complex integrated boot. Production policy 6.0.2 restores a bounded controlled-reduction route for this class: reduce only a fresh high-derived copy or classified complex region, preserve the source and critical openings, and never extend that permission to an undifferentiated mixed asset.

The second N06 failure was subtler. Fresh-high Decimate candidates at 6,000, 4,000, and 1,000 triangles matched the high silhouette extremely well. The 1,000-triangle candidate reached worst-view IoU about 0.9902 and mean IoU about 0.9911. It was also closed and manifold. Nevertheless, its visible wire distribution was unacceptable.

Measured comparison:

| Explicit-wire metric | Accepted H01 reference | N06 raw 1K collapse |
| --- | ---: | ---: |
| Explicit triangles | 438 | 1,000 |
| Triangles below 10 degrees | 0% | 9.8% |
| Triangles with aspect over 6 | 1.37% | 14.6% |
| Maximum vertex valence | 9 | 14 |
| Minimum triangle angle | 10.40 degrees | 1.17 degrees |
| Maximum aspect | 10.03 | 64.68 |

The denser 4K and 6K raw collapse candidates had the same failure pattern: roughly 11 percent of triangles below 10 degrees, many slivers, high-valence fans, and noisy local density.

The source boot also contained 77 mesh components and 12,306 boundary edges, while the raw reduced candidates became one closed component. Even when the product reads as one integrated boot, such a connectivity change is a construction warning and must not be dismissed as cleanup.

Reusable lesson:

- High silhouette IoU plus manifold status does not prove usable topology.
- An unclassified raw Decimate collapse is only a density stress test. A classified complex continuous asset/region may use one controlled reduction as the user-authorized final topology when the source is preserved and zero-area faces are removed.
- If reduction preserves identity but fails wire quality, preserve only the measured contour evidence and rebuild deliberate local flow. Do not retain the reduced mesh.
- Never weld across source shells or close source boundaries without an explicit construction reason and visual verification of every opening.

## Accepted H01-H15 calibration

| Asset | Final faces | Eval. tris | Components | Accepted construction lesson |
| --- | ---: | ---: | ---: | --- |
| H01 | 558 | 678 | 1 | Mixed triangle/quad organic cage; reduce body centers, protect appendage roots and silhouette crests. |
| H02 | 425 | 790 | 1 | Open bowl; keep rim thickness and radial silhouette, keep broad interior and bottom sparse. |
| H03 | 542 | 924 | 8 | Kettle/handled assembly; keep logical parts and handle gap separate, use staged rotational profiles. |
| H04 | 268 | 476 | 2 | Underbuilt silhouette case; add control until identity returns instead of defending a lower count. |
| H05 | 68 | 136 | 3 | Thin board shell, clip carrier, and true hanging hole; rear support is a named user-approved omission. |
| H06 | 108 | 200 | 7 | Macro lantern; one chamber envelope, no individual louver geometry, sparse roof and finial. |
| H07 | 230 | 440 | 3 | Continuous handled body; true profile-event rings, preserved handle opening, no false feet. |
| H08 | 372 | 600 | 7 | Multipart rounded product; preserve real steps and separate construction, bake shallow surface detail. |
| H09 | 540 | 988 | 4 | Rotational handled product; allocate body, rim, handle, and pivots independently. |
| H10 | 432 | 828 | 1 | Single rotational shell; use radial sides for silhouette and vertical rings only at profile changes. |
| H11 | 408 | 752 | 2 | Vessel plus handle; protect the handle negative space and use a sparse body interior. |
| H12 | 296 | 528 | 9 | Repeated multipart construction; preserve macro structure and true gaps, collapse decorative repetition. |
| H13 | 300 | 552 | 14 | Box-like multipart product; broad panels stay sparse while real attachments remain separate. |
| H14 | 316 | 576 | 2 | Rounded product with one major attachment; preserve the controlling side profile and contact logic. |
| H15 | 1,422 | 2,844 | 1 | Glove/hand; protect every finger, thumb, web, fingertip, wrist opening, and palm outline. |

The H15 count is intentionally much higher than nearby props because five fingers, a thumb, web spaces, and a wrist opening create many controlling silhouettes. Do not normalize it down to a batch average.

## What the V08 revision proved

- The first sparse attempt can still be wrong. H01 needed silhouette restoration after an overly aggressive first reduction.
- A named user-approved omission can invalidate an unmasked metric without invalidating the candidate. Keep both masked and unmasked evidence.
- Macro reconstruction beats modeling repetition. H06 became cleaner and sparser by replacing louvers with one chamber.
- False construction is worse than a surface residual. H07 improved by removing invented feet and preserving a continuous body envelope.
- A final low may mix triangles and quads. H01's accepted 558-face result used 438 explicit triangles and 120 quads; this was better than a uniformly dense all-quad grid.
- Automated surface distance is not a universal acceptance gate. Bakeable relief and approved omissions can raise distance while the required product read remains correct.

## What the N06 boot failure proved

Use two independent axes of judgment:

1. Product fidelity: silhouette, bounds, openings, construction, and shading.
2. Mesh usability: face distribution, triangle quality, poles, component preservation, and predictable edit/bake behavior.

Reject the candidate if either axis fails. The N06 1K raw collapse passed the first axis numerically but failed the second visibly and quantitatively.

For a complex integrated object:

1. Inventory source components, boundaries, openings, and thin layers before cleanup.
2. Preserve a fresh high duplicate for every density candidate.
3. Use raw collapse only to find the density floor.
4. Generate a regularized cage with QuadriFlow, AutoRemesher, RetopoFlow-assisted strips, or deliberate local patches when collapse topology is poor.
5. Preserve the cuff, rim, toe, sole, heel, straps, and other controlling features.
6. Send wrinkles, stitching, grain, and shallow seams to the bake.
7. Re-run six-view, wire-distribution, component, and shading gates.

## Mandatory method decision

Choose `controlled_direct_reduction`, `semantic_reconstruction`, or `hybrid_per_component`. For integrated complex forms, use a fresh-high controlled reduction when proxy reconstruction would lose identity, or record the RetopoFlow-assisted/cage strategy when deliberate flow is required. For mixed assets, record a region map and never let the complex-region reduction consume structured or aggregate regions.

## Mandatory representative-asset stop gate

Before processing a batch:

1. Select one representative asset whose method risk is high.
2. Create one review-ready candidate only.
3. Show high solid with wire disabled and low solid with wire enabled at useful close-up scale.
4. Include front, back, left, right, top, bottom, perspective, and at least one close wire view.
5. Report faces, evaluated triangles, component changes, six-view results, and wire-distribution results.
6. Stop and wait for user confirmation.

Do not start the remaining batch after a candidate that is merely manifold, numerically close, or low in face count. The representative must pass identity, construction, density, wire distribution, and shading review.

## Mandatory wire-distribution gate

Run `scripts/audit_topology_flow.py` and inspect close wire views before acceptance.

For explicit triangles on visible smooth or curved regions:

- Treat minimum angle below 10 degrees as a warning.
- Reject minimum angle below 5 degrees unless the exact face is hidden or belongs to a documented thin cap and the wire/shading review proves it harmless.
- Reject when more than 2 percent of explicit triangles fall below 10 degrees.
- Treat aspect ratio above 6 as a warning; reject when more than 5 percent exceed 6 or any exceeds 20 without an exact named exception.

For vertices and local density:

- Warn at valence above 8.
- Reject a visible smooth-region pole above valence 10.
- Reject a single high-valence radial fan spanning a visible curved highlight.
- Flag adjacent polygon-area jumps above 6:1 and require a construction or curvature reason.
- Reject abrupt density changes, dense wrinkle-following triangles, and long slivers even if global thresholds happen to pass.

Interpret thresholds by polygon type. A long quad on the hidden thickness wall of a thin board is not the same as a visible explicit sliver triangle across a boot shaft. Do not use an internally triangulated thin quad to justify poor explicit triangle flow.

## Reusable per-class construction lessons

### Organic and anatomical forms

- Preserve gesture, appendage roots, web spaces, and contour crests first.
- Remove broad torso, palm, and internal-surface density before fingers, wings, legs, tails, cuffs, or openings.
- Use mixed triangles and quads when static topology benefits; do not force a uniform grid.

### Rotational forms

- Choose radial sides from silhouette distance.
- Place vertical rings only at radius, slope, rim, shoulder, neck, or contact changes.
- Keep flat caps and broad interiors sparse.
- Build handles from the negative-space outline before fitting tube thickness.

### Box-like and repeated forms

- Start from the minimum outer shell.
- Keep broad panels as one face or a few controlled faces.
- Preserve real steps, openings, thickness, feet, latches, and handle gaps.
- Bake louvers, ribs, grooves, stamped marks, screws, logos, and shallow frames when they create no true opening or silhouette.

### Multipart assemblies

- Keep logical pieces separate when this preserves overlap order, shading, baking, or negative space.
- Do not create hidden tunnels to force one connected component.
- Do not fuse gaps or close source openings during duplicate cleanup.

## Batch assembly and evidence lessons

- Preserve the high fingerprint before any candidate work.
- Generate every candidate under a deterministic asset ID, source object, method, version, and status.
- Keep rejected candidates in a superseded collection with the reason recorded.
- Audit aligned candidates before row translation.
- Use translation-only review copies; high wire off, low wire on.
- Preserve older review rows as history; only one candidate may be export-authoritative.
- Render, save, reopen, and verify the final arrangement.
- Keep topology, silhouette, construction, density, wire distribution, and shading gates independent.
- State UV, cage, material, LOD, and bake status. Topology completion alone is not a finished PBR asset.

## N03 hard-case correction: bounds are not likeness

An N03 molded equipment case candidate matched the high AABB almost exactly and passed topology integrity, but its six-view silhouette remained near 0.93. A translated perspective review row made the nearer low appear even larger, while the folded handle and several latch positions had been estimated from a proxy.

Required response:

- Reject the candidate; AABB equality is only a registration check.
- Judge size in source-aligned orthographic overlays, not a perspective two-row view.
- Measure each silhouette-visible hardware part in the high's local frame before building it. For this case, the high-derived handle footprint was about `0.300 x 0.352`; the rejected proxy was about `0.180 x 0.282`.
- Record separate centers for asymmetric latch/hinge pairs and include small center tabs or end hardware when they affect an orthographic outline.
- For roughly 0.5-million-face highs, run the expensive fingerprint/topology scan once, use lightweight local measurements and overlays during correction, then run one final full audit.

## Failure-response matrix

| Observed failure | Correct response |
| --- | --- |
| First perspective reads as another object | Reject immediately; change method or rebuild the controlling volumes. |
| Clean all-quad mesh is uniformly dense | Run region ablation; dissolve broad-center and straight-span density. |
| Low count but wrong silhouette | Add local control at the responsible profile; do not defend the count. |
| High IoU but sliver/fan topology | Keep fidelity method, reject generator output, and regularize or locally rebuild flow. |
| Direct reduction closes openings or merges shells | Reject cleanup/reduction settings; preserve boundaries and component logic. |
| Hard-surface repetition dominates faces | Replace with one macro envelope and bake the repetition. |
| One metric fails because of approved omission | Keep unmasked evidence, apply only a named omission mask, and record user authority. |
| Review row shifts or scales the audit object | Restore aligned authoritative candidate; create a translation-only display copy. |
| Plugin output is wrong | Record the attempted plugin and reject it; do not call it the final method. |
| Mechanical audit passes but visual review fails | Candidate remains rejected; no quality gate may be inferred from another. |
| AABB matches but the low still looks larger or smaller | Switch to source-aligned orthographic overlay; reject any real silhouette mismatch and treat perspective row scale as non-authoritative. |
| Small protrusions are missing or misplaced | Build a high-derived local `feature_controls` table and reconstruct each silhouette-visible part at its measured center and size. |
