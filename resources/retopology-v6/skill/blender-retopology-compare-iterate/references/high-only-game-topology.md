# High-only game topology

Use this guide for every task. The only modeling input is the high-poly. The goal is a sparse, bake-oriented static game prop that preserves the high-poly's recognizable construction and silhouette. This is not an all-quad subdivision workflow.

## Authority order

Resolve decisions in this order:

1. High-poly silhouette, proportions, openings, negative space, ground contact, and major part placement.
2. Real construction: which forms are molded together and which are separate pieces.
3. Expected motion or deformation. Assume a static prop unless the asset clearly deforms or the user says otherwise.
4. Shading and bake requirements.
5. Polygon economy.

Never reduce faces by changing the object's identity. Never spend faces on a detail that belongs in the bake while a major outline or opening remains inaccurate.

## Form a topology plan before generating geometry

For each asset, write a compact plan containing:

- Class: planar/box-like, rotational, rounded hard surface, organic static, or multipart assembly.
- Primary volumes and visible attachments.
- Silhouette-critical turns in front, side, top, and perspective.
- Openings and negative spaces that require geometry.
- Parts that may remain separate closed shells.
- Details to bake: grooves, grain, dents, logos, seams, rivets, shallow recesses, and small surface noise that do not materially change the silhouette.
- Initial face band and cross-section segment counts.
- Cap and loop-termination strategy.
- A hard face ceiling and one `keep_geometry`, `bake`, or `omit` decision for every disputed feature.

Do not run a remesher before this plan exists.

## Select the region strategy before building

Classify the complete asset and each visually distinct responsibility region before creating geometry.

- Use semantic multipart reconstruction for mechanical, hard-surface, planar, repeated, and assembled regions.
- Use controlled direct reduction on a fresh high-derived copy for complex integrated or soft regions—such as a saddle body, cloth, or leather—when a coarse semantic proxy would visibly lose identity. Preserve the original high, openings, boundaries, silhouette and major folds.
- Use RetopoFlow-assisted surface drawing, deliberate local cages/patches, contour rings, and bounded Shrinkwrap fitting when a stable structured flow can be established efficiently.
- Use a sparse silhouette envelope for a dense aggregate region such as stacked wood, rocks, or debris when individual members do not control the primary outline or required negative space.
- Inventory source components, boundaries, openings, thin layers, silhouette controls, attachment roots, deformation zones, and highlight flow before building. A disconnected island is evidence only, never a semantic region by itself.
- Voxel remesh, QuadriFlow output, automatic remesh, and undifferentiated whole-asset collapse remain diagnostics, not formal output.
- Compare the authoritative reconstruction against the untouched high in six orthographic views and perspective; rebuild any region that damages a controlling outline, opening, attachment, or construction relationship.

## Start with a face band, not a fixed target

Use these polygon-face bands only as first-pass calibration for small and medium static props:

| Asset class | Initial band |
| --- | ---: |
| Flat panel or simple box | 80–250 |
| Simple rotational container | 150–450 |
| Rounded box or multipart hard surface | 250–800 |
| Static organic prop | 300–1,200 |

Adjust for screen size, viewing distance, platform, deformation, and the number of silhouette features. Count final triangles as well as modeling faces before export.

Allocate most geometry to silhouette and cross-section changes. Use the next largest share at openings, attachment roots, rims, and hard construction breaks. Spend little or no geometry on broad internal planes.

Use these learned hard calibrations for comparable small static props:

| Learned case | Target faces | Hard maximum | Required construction decision |
| --- | ---: | ---: | --- |
| Chicken-like static organic body | 450-650 | 650 | Keep appendage roots and silhouette peaks; reduce broad torso centers first. |
| Handled necked container | 160-240 | 280 | Use a continuous body envelope with about 6-7 real profile events; shallow base grooves are not separate feet. |
| Lantern or louvered box | 110-170 | 200 | Keep base, outer chamber, eaves, roof, and finial; individual louver geometry count must be zero unless it creates a true opening. |
| Thin board or clipboard | 45-85 | 110 | Keep board thickness, clip, and true hanging hole; omit a rear support when explicitly approved. |

An accepted 15-asset production pass reduced 12,581 faces to 7,300 faces (42 percent) while improving several silhouettes. One previously underbuilt asset deliberately increased from 120 to 268 faces because its shape was wrong. Treat this as evidence that density must be allocated per asset: reduce broad non-silhouette regions aggressively, but add control where the old low fails the outline. Never impose one percentage on every object.

## Learned sparse-revision cases

Treat these as reusable construction lessons learned from high-poly comparison, not as low-poly reference dependencies:

| Case | Before to accepted | Reusable decision |
| --- | ---: | --- |
| Chicken-like organic body | 1,147 to 558 faces | Remove roughly half the density from broad chest, abdomen, back, and other non-silhouette centers. Preserve wing, leg, neck, tail, and silhouette-crest control. Mixed triangles and quads are preferable to a uniformly dense all-quad grid. |
| Thin board or clipboard | 172 to 68 faces | Remove a user-approved rear support as a complete connected component. Preserve board thickness, outer radius, top clip, and true hanging hole; keep the broad board faces sparse. |
| Louvered lantern | 382 to 108 faces | Replace 24 individual louvers and four nonessential posts with one macro chamber or inset panel. Preserve base, eaves, roof, and finial; bake internal slats and shallow frame detail. |
| Handled necked container | 278 to 230 faces | Delete four false pointed feet and extend one continuous body envelope to the real contact line. Keep body, neck or mouth, and handle negative space; bake shallow side ribs and bottom channels. |

Apply this revision sequence:

1. Classify each questioned feature by outer silhouette, true opening, ground contact, construction, or bakeable relief.
2. Remove false components before applying general decimation. A separate foot, support, post, or louver must justify itself independently.
3. Rebuild the surviving macro shell from the minimum profile events. Do not preserve dense topology merely because it is clean or quad-based.
4. Reduce broad centers and straight spans, then re-check six aligned orthographic silhouettes. Revert a collapse that damages a controlling outline; retain reductions whose difference is only approved omission or bakeable relief.
5. Record the before and after faces, omitted components, six-view residual regions, and actual plugin contribution.

Use the measured overlays as evidence, not a universal waiver. In this batch the organic body kept every view at or above about 0.970 after a 51 percent reduction. The macro lantern and continuous container deliberately left some 0.95-0.97 review-band residuals where high-only louvers, shallow ribs, or grooves were assigned to baking. Inspect those residuals visually; a macro outline error still fails even when the face budget passes.

## Choose geometry by asset class

### Planar and box-like props

Begin with the minimum outer shell that wraps the high-poly silhouette. Preserve real thickness, lid/body steps, openings, handle gaps, feet, latches, and any corner or profile break that changes a primary-view outline. Do not copy the high-poly's dense planar triangulation. A broad side or lid may be a single quad or a few controlled triangles/quads. Reserve shallow panels, embossing, grooves, screws, buttons, ribs, stamped marks, and surface noise for normal-map baking unless they change silhouette or create true negative space.

- Build broad planar quads first.
- Add edges only for silhouette corners, real panel breaks, openings, or required shading transitions.
- Use one bevel segment for a normal low-poly highlight; add a second only when the inspection distance exposes faceting.
- Do not carry a loop across an entire panel when it serves one local feature. Terminate it on a stable plane.
- Use controlled triangles to end local loops or divide a non-warping plane.
- Replace repeated louvers or grille strips with one inset chamber or panel when the repetition lies inside the macro silhouette and creates no true through-hole.

### Rotational props

- Build radial rings only where radius, slope, rim thickness, or shading changes.
- Begin with 8–12 sides for small or background cylinders, 12–16 for common props, and 16–24 for large openings or hero silhouettes.
- Exceed 24 sides only after the matched-distance silhouette comparison shows a visible need.
- Keep vertical density independent from radial density. A tall straight wall does not need extra height loops.
- Give visible rims and openings real thickness; bake small knurling and grooves.
- Build neck, shoulder, lid, and cap transitions as staged profile rings. Do not crowd many vertices into a collapsed top fan; keep the top readable from side and top views.
- Build handles and pivots from their negative-space outline first, then fit tube thickness. Do not let an accurate vessel body hide a visibly wrong handle.
- For handled necked containers, start from one continuous 12-16-side body shell and place rings only at contact, maximum body width, shoulder start or turn, neck root, and rim. Do not create independent pointed feet from shallow molded base channels.

### Rounded hard-surface props

- Preserve primary corner radii and large highlight bands.
- Use uneven density deliberately: more around changing curvature, less across stable faces.
- Do not apply a blanket bevel or uniform subdivision.
- Keep panel inserts separate when this produces cleaner shading, simpler UVs, and an accurate construction break.

### Static organic props

- Trace the large gesture, profile, and attachment roots before secondary lobes or wrinkles.
- Use rings or patches that follow volume changes; do not force an evenly spaced quad grid.
- Allow triangles on non-deforming, low-visibility regions and at clean loop terminations.
- Keep poles away from silhouette crests and important smooth highlight paths.
- Model lobes or appendages separately only when their root and overlap still read correctly in all six orthographic views and perspective.
- Reduce broad torso, palm, and other non-silhouette centers before touching fingers, wings, legs, lobes, or attachment roots.
- For a static chicken-like prop, begin near 550 faces and keep the accepted result within 450-650 unless the user specifies hero or deformation needs. Use roughly 12-16 body cross-section points and 7-9 longitudinal silhouette events; avoid a uniform all-body grid.

### Multipart assemblies

- Reconstruct logical pieces separately instead of collapsing the entire object into one remeshed shell.
- Preserve visible gaps, overlap order, hinge/handle clearance, and contact surfaces.
- Use simple closed cages for each part.
- Do not create hidden tunnels merely to make disconnected parts technically connected.

## Use triangles and quads deliberately

Use quads for:

- Directional curved flow.
- Rims and openings.
- Attachment transitions.
- Important highlight paths.
- Areas likely to deform.

Use triangles for:

- Flat caps and panels.
- Hidden or underside regions.
- Local loop termination.
- Small non-deforming transitions.
- Removing a redundant loop without affecting silhouette or shading.

Avoid:

- A single center vertex connected to many narrow triangles on a visible cap.
- Long sliver triangles.
- Warped quads that will triangulate unpredictably.
- Poles on a silhouette, bevel highlight, rim, or deforming joint.
- N-gons in the accepted deliverable.

Run the explicit wire-distribution gate, not only a manifold check:

- For visible explicit triangles, warn below 10 degrees minimum angle and reject below 5 degrees unless an exact hidden or thin-cap exception is documented.
- Reject when more than 2 percent of explicit triangles fall below 10 degrees.
- Warn above aspect ratio 6; reject when more than 5 percent exceed 6 or any unexplained face exceeds 20.
- Warn at vertex valence above 8 and reject a visible smooth-region pole above valence 10.
- Flag adjacent polygon-area jumps above 6:1 and require a curvature, attachment, thickness, or construction reason.
- Inspect close wire views for slivers, radial fans, wrinkle-following density, abrupt size changes, and poles on controlling highlights. Numeric thresholds cannot see whether a face is hidden or whether its flow follows the form.

Interpret explicit triangles separately from the invisible render triangulation of a quad. A long quad on a thin board's hidden thickness wall is not evidence that a visible sliver triangle across a curved boot shaft is acceptable.

Prefer a center quad with corner transitions, or several small local fans, when closing a visible rectangular or rounded cap.

## Fit without losing construction

- Fit in world space; do not alter the source high-poly transforms.
- Match bounds, center, orientation, and ground contact before local projection.
- Use Shrinkwrap or nearest-surface fitting only with controlled distance and part exclusions.
- Inspect nearby parallel surfaces, thin walls, handles, fingers, rims, and layered parts for projection onto the wrong surface.
- Re-center the candidate from its evaluated world bounds after plugin operations; object origins may not represent the visible geometry.
- Pin the ground-contact region after fitting.
- Run bounds and center audits while the generated low is aligned over the high. Use a display proxy for the later two-row arrangement instead of auditing the offset presentation copy.

## Use plugins as assistants

- QuadriFlow may be used only as a disposable diagnostic for continuous organic or rounded forms; its generated mesh and cleaned derivatives cannot become the formal low.
- Use RetopoFlow for deliberate surface drawing and cleanup. Record whether it was used interactively or only for mesh cleanup.
- Treat AutoRemesher, voxel remesh, and QuadriFlow as disposable diagnostics. Decimate may be formal output only for a classified complex continuous region, from a fresh high-derived copy, with the selected region and source-preservation evidence recorded.
- Never describe a scripted or automatic candidate as hand-retopologized.
- Do not count an add-on as used merely because it is installed, enabled, or reported during Blender startup. Background mode may explicitly skip initialization.

## Review in priority order

Review every candidate in front, back, left, right, top, bottom, and perspective with matched framing. Use aligned high/low silhouette overlays for the six orthographic views:

1. Identity and silhouette.
2. Overall dimensions, center, orientation, and ground contact.
3. Openings and negative spaces.
4. Major part placement and attachment roots.
5. Cross-sections and corner radii.
6. Face distribution, poles, loop termination, and triangle quality.
7. Shading.

Do not spend time polishing topology while an earlier priority is wrong.

A clean manifold mesh is not evidence of a correct game low. If one primary view exposes the wrong thickness, profile, negative space, attachment placement, or controlling outline, the candidate fails and must be rebuilt. Percentage metrics on very thin objects can be sensitive, so inspect the overlay itself, but never dismiss a large mismatch as a metric artifact without a visual explanation.

Reduce faces from non-silhouette regions first: broad centers, straight walls, planar interiors, hidden undersides, and uninterrupted spans between real profile changes. Keep enough geometry at fingers, handles, rims, contour breaks, attachment roots, and negative-space boundaries. Let bakeable wrinkles, shallow relief, seams, grooves, surface noise, and small bevels move to the normal map.

Use dimension and center metrics as alerts, not substitutes for visual inspection. Inspect both high-to-low and low-to-high surface distance when possible so an undersized cage and an oversized cage cannot hide behind one-sided sampling.

Run a density ablation before acceptance. If a non-silhouette region loses at least 25 percent of its faces, or the asset loses at least 10 percent overall, while the worst-view IoU drops by no more than 0.005 and target-distance shading remains stable, fail the denser version.

Keep each principal dimension within 2 percent, normalized center offset within 1 percent of the high maximum dimension, and contact height within 1 percent of high height. Require unapproved controlling-outline residuals to stay within `max(2 px, 1 percent of projected maximum size)`.

Use 0.97 orthographic silhouette IoU as the normal target. Scores from 0.95 to 0.97 require visual justification and a named residual region. Do not accept a visibly wrong controlling outline at any score. Conversely, do not rebuild bakeable surface detail merely because point-to-surface distance fails while the silhouette, construction, and negative space are correct. Component or 2D-hole counts may differ when valid separate closed shells overlap; inspect the actual opening instead of optimizing the counter blindly.

When the user approves an omission, record the region, decision, authority, and affected views. A later remesher or reconstruction pass must not recreate it. A mask may exclude only that named component from a secondary metric; always retain an unmasked overlay and never hide a body-shape error with the omission mask.

## Acceptance checklist

Accept only when:

- The low reads as the same object in all six orthographic views and perspective.
- Every major opening, gap, attachment, and contact point is present.
- Each visible loop has a reason.
- Planar regions are sparse.
- Curved silhouettes use the lowest segment count that passes the intended viewing distance.
- Triangle fans do not create skinny visible wedges; use 10 degrees minimum angle and aspect ratio 6 as warning thresholds.
- `wire_distribution_pass` is true after `scripts/audit_topology_flow.py` and close wire inspection. Reject high-IoU automatic output when slivers, fans, poles, or density jumps remain.
- There are no unintended N-gons, duplicate vertices or faces, zero-area faces, loose geometry, multi-face non-manifold edges, or inconsistent normals.
- Closed parts face outward; open boundaries are intentional.
- The high-poly fingerprint remains unchanged.
- UV, cage, materials, LOD, and bake status are reported truthfully.
- Face budget, geometry decisions, user-approved omissions, and density-ablation evidence are recorded.

Arrange a two-row high/generated inspection.
