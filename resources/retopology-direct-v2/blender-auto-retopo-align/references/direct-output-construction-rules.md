# Direct-output construction rules

## Contents

1. Shape authority
2. Per-asset planning
3. Method boundaries
4. Face allocation
5. Component logic
6. Class-specific construction
7. Tool use
8. Live-session safeguards
9. Direct handoff

## Shape authority

The current high-poly is the only source of:

- Overall dimensions, center, orientation, and ground contact.
- Main-volume proportions and local cross-sections.
- Front, side, and top contours.
- Openings, negative spaces, gaps, overlaps, and attachment roots.
- Silhouette-visible protrusions and functional hardware.
- Joined-versus-separated component evidence.

Use AABB, extrema, percentiles, and object centers only to register or search the high. Never turn those summaries into the geometry itself. A low that shares bounds with the high can still be a different product.

Do not use:

- A rejected yellow low.
- An older low from another file.
- A fixed generic template.
- An accepted face count or component count as shape evidence.
- A category label such as bottle, lantern, box, or pump as a proportion source.

Historical scripts may provide topology grammar, such as ring stitching or sparse cap layouts. Re-measure every shape-defining value from the current high before instantiating the grammar.

## Per-asset planning

Before creating vertices, identify:

- Asset class: planar/box-like, rotational, rounded hard surface, repeated construction, integrated organic, or multipart assembly.
- Main volumes and their high-local profile events.
- Silhouette-critical contours and ground-contact points.
- True openings and their boundaries.
- Functional protrusions, their local centers, dimensions, roots, and paths.
- Details assigned to geometry, normal-map baking, or omission by user authority.
- Continuous macro envelopes and proven independent components.
- Radial segments, lengthwise profile rings, cap strategy, attachment strategy, and a non-binding face band.
- The surface correspondence method: measured local sections, bounded surface projection, fresh high-derived cage, or per-component hybrid.

For rotational construction, require several high-derived cross-sections that share a stable center and axis. Do not turn a boxy, handled, ribbed, asymmetric, or lopsided object into a lathe because its AABB is tall.

For a handle or negative-space loop, identify both roots, the center path, section size, opening boundary, and controlling high views. A one-sided extreme point is not handle evidence.

For a proposed component split, record one real reason:

- Visible open gap or negative space.
- Independent motion or pivot.
- Detachable overlap with its own silhouette.
- Required material/shading discontinuity that cannot be baked safely.
- Independent occlusion order.

If the evidence is only a seam, groove, source island, material slot, or shading line, preserve the continuous macro form and bake the line.

### Coordinate and size authority

Size mismatch is a construction-input failure, not a presentation problem.

- Record the high object's matrix and the coordinate space of every section or feature measurement.
- Build in high-local space or preserve the exact high transform.
- Use global bounds only for coarse registration and sanity limits, never as the sole body profile.
- Keep the authoritative low aligned with the high during construction.
- For the inspection row, translate only. Never rotate, scale, or non-uniformly compensate the low.

### Minimum feature correspondence

For every silhouette-changing or functional feature, record enough current-high evidence before building:

- Handle or bail: both roots, centerline/path, opening, section, and protrusion depth.
- Opening: boundary, depth direction, rim/wall relationship, and whether it passes through.
- Protrusion: root footprint, axis/direction, section, and maximum extent.
- Rotational body: proven axis plus local sections at every real profile event.
- Boxy or asymmetric body: controlling contours at multiple depths/heights, not one bounding box.
- Integrated organic appendage: root transition, outer silhouette, gap/valley boundary, and terminal extent.

When required evidence is missing, stop before geometry. Emitting a generic proxy is not a fast result; it is a failed result.

## Method boundaries

### Controlled direct reduction

Use for structurally complex, genuinely integrated forms whose surface identity would be lost by semantic proxy reconstruction. Also use it as the preferred fallback for an exceptionally complex whole asset when component separation, semantic reconstruction, or a hybrid build would be unreliable or would visibly lose identity.

The FBX preparation step joins imported meshes into `SOURCE_HIGH`; that joined object is transport normalization, not integration evidence. Before selecting direct reduction, inspect disconnected mesh islands and macro structural regions. For ordinary assets, reject whole-asset direct reduction when a planar, rotational, vessel-shell, mechanical, or assembled region can be reconstructed deliberately. For an exceptionally complex asset, permit one whole-asset controlled reduction when the immutable source manifest's measured fragmentation gate passes. If the only fragmentation is exact duplicate-position triangle soup and the server manifest provides a qualified `SOURCE_HIGH_NORMALIZED_WORK`, reduce that work copy instead; never weld or reduce `SOURCE_HIGH`. Otherwise use semantic reconstruction or a per-component hybrid.

Treat complexity as an evidence-backed routing judgment, not a fixed triangle-count threshold. Suitable evidence includes heavily fused scans, dense irregular assemblies, many interpenetrating parts, mixed hard/organic surfaces with indeterminate boundaries, or a high-derived component plan that cannot be established safely in one pass.

Procedure:

1. Duplicate the original high without altering it.
2. Choose one asset-specific planned density.
3. Use a qualified reduction or remeshing operator.
4. Use bounded same-region fitting when needed so vertices do not jump to another body part.
5. After modifiers/conversion/join and before UV creation, finish only the fresh generated low. Derive an exact-position tolerance from the low bounds; merge only coincident generated vertices, dissolve zero-length/degenerate edges, delete zero-area faces and resulting loose geometry, then recalculate normals. Measure boundary edges before UV creation. For each accidental boundary component, require a component-local solution: cap a solid end, bridge paired section rings, or fill only a simple closed gap loop with `bmesh.ops.holes_fill` and triangulate only its newly created faces. A real opening must retain inner and outer walls joined by a rim; never place one broad face across intended negative space. If boundary vertices are not all degree two in one simple cycle, or the repair would cross components, rebuild that new low component. Update the BMesh and re-measure after repair; zero boundary edges is required. Never apply this finish to `SOURCE_HIGH`, and never substitute broad merge distances, Decimate, remesh, or whole-object reconstruction.
6. Save the single result for the user.

Do not generate conservative/aggressive/intermediate variants in formal work. Do not reduce a prior low again. Do not run a second density after generation.

### Semantic reconstruction

Use for mechanical, hard-surface, planar, rotational, repeated, or clearly assembled objects.

Build each meaningful macro part from high-derived sections:

- Broad shells and panels.
- Rims and real thickness.
- Handles and negative spaces.
- Hinges, latches, pivots, rails, feet, brackets, and supports that affect silhouette or function.
- Open chambers and true through-holes.

Do not reconstruct high-poly surface noise, repeated shallow relief, wood grain, wrinkles, micro-ribs, small grooves, screw heads, stamped logos, or tiny bevels when they do not change silhouette.

### Per-component hybrid

Use direct reduction for an integrated organic or irregular component and semantic reconstruction for clearly structured regions only when both decisions are proven from the same high.

A vessel, bowl, bucket, tray, box, or housing with irregular contents is normally a hybrid case even when the scan or imported FBX fuses the regions:

- Reconstruct the shell, rim, wall, base, cavity, handles, and other hard-surface controls from measured sections.
- Isolate the content responsibility from high-derived boundaries and use controlled direct reduction, qualified remeshing, or a fresh high-derived cage only there.
- Preserve the rim/cavity boundary, visible separation, contact/occlusion, and contents silhouette.
- Never apply one whole-object reduction to both structured shell and irregular contents.

Exception: when the complete asset is exceptionally complex and a reliable shell/content boundary cannot be established without speculation or identity loss, a planned whole-asset controlled reduction is allowed only if the measured source-topology fragmentation gate passes or the immutable manifest provides a qualified exact-weld normalized work copy. The original high is never modified.

Record the split before geometry in `component_method_map`: each entry names its component evidence, measured responsibility boundary, and construction method. A hybrid is not proven unless the plan contains at least one semantic-reconstruction region and at least one eligible high-derived organic region.

## Face allocation

Use faces where they preserve:

- Silhouette.
- Curvature change.
- Openings and negative space.
- Component junctions and attachment roots.
- Deformation where explicitly required.
- Important shading transitions.
- UV or bake-cage control where explicitly required.

Remove or avoid faces that exist only because:

- The high has dense triangulation.
- A flat panel was subdivided uniformly.
- A straight span inherited repeated loops.
- A shallow texture detail was modeled.
- An all-quad appearance was preferred over sparse topology.
- A generic bevel modifier added supporting loops everywhere.

Rules:

- Make large planar centers one broad face or a small controlled layout.
- Use one boundary loop where one preserves the outer contour; do not stack edge rings for tiny bevels.
- Add axial rings only at actual profile events.
- Allocate cap, rim, wall, shoulder, and base independently on rotational props.
- Use the lowest radial count that preserves the intended round silhouette.
- Use quads on directional curved flow and transitions.
- Use controlled triangles on flat panels, undersides, local terminations, and small caps.
- Avoid long thin triangles, random triangulation, warped quads, and center poles with many thin spokes on visible curvature.
- Terminate loops on stable planes or hidden regions.
- Do not impose a universal percentage or fixed count on a batch.

Face count is an output of shape and construction decisions. Many small props should land in the low hundreds, but a complex integrated organic object may need more, while a planar board may need far fewer.

## Component logic

Keep continuous:

- Molded or pressed casings.
- Cast housings with shallow seams.
- Wrapped or blended soft shells.
- Anatomical continuities.
- A vessel body with a true opening cut into the same shell.

Keep separate:

- Independent moving or pivoting parts.
- Handles whose gap and two roots remain visibly independent.
- Lids, levers, latches, hinges, caps, brackets, rails, feet, or supports with distinct construction.
- Overlapping pieces whose silhouette and occlusion require separation.

Do not:

- Split every high mesh island.
- Merge everything because the product is semantically one object.
- Close a real opening to simplify construction.
- Create hidden tunnels to fake continuity.
- Leave floating appendages or detached roots.
- Add false feet from shallow lower grooves.
- Duplicate rims at an incorrect split.

Intersecting closed parts are allowed only when the intersection is hidden and compatible with the intended bake and engine pipeline.

## Class-specific construction

### Planar and box-like props

- Start from the minimum outer shell that wraps the high contour.
- Preserve real thickness, stepped layers, open slots, handle gaps, hinges, latches, feet, and profile changes.
- Keep panel centers broad.
- Bake graphics, stamps, shallow insets, grooves, grain, and micro-bevels.

### Rotational props

- Derive several local sections from the high.
- Separate radial silhouette density from vertical profile-event density.
- Preserve true open rims and inner cavities.
- Build handles from their measured roots and path rather than attaching a generic loop.
- Do not model every repeated rib when the outer envelope is unchanged.

### Rounded hard-surface props

- Use sparse section rings at shoulder, neck, lid, base, and other real contour events.
- Keep functional pieces separate only when construction evidence supports it.
- Avoid uniform vertical and radial grids.

### Integrated organic props

- Prefer fresh-high direct reduction or a high-derived cage.
- Preserve fingers, cuffs, straps, heels, soles, wings, limbs, and other silhouette controls before wrinkles.
- Keep surface folds only when they change the outline; bake minor folds and pores.
- Use bounded correspondence so nearby surfaces do not collapse into each other.

### Repeated construction

- Model repeated members only when they create visible gaps, outer silhouette, or functional structure.
- Collapse shallow louvers, ribs, grille bars, and panel repetition into one envelope when there is no true opening.
- Preserve spacing and support logic for racks, frames, beds, benches, and similar assemblies.

## Tool use

- RetopoFlow: use for deliberate PolyStrips, Contours, patches, and manual cage flow.
- QuadriFlow or similar: use for qualified integrated organic surfaces with current-high correspondence.
- Decimate: use only as a controlled direct-reduction operator on a fresh high duplicate for an eligible integrated region, or on the manifest-qualified exact-weld normalized work copy for a duplicate-position triangle soup; never run it on `SOURCE_HIGH` or use the joined state alone to justify it.
- Shrinkwrap or projection: use temporarily and bound it to the responsible high region.
- Primitive/profile builders: use for mechanical and hard-surface macro geometry after current-high measurement.
- Boolean: use only for an isolated, proven construction. Prefer explicitly authored openings and stitched profiles.
- Bevel: do not use blanket small bevels on the low.

State actual operator use. Installed or enabled plugins do not count as used.

## Live-session safeguards

- Work in the current authorized Blender.
- Reconfirm PID, responsiveness, bridge, and exact filepath before mutations.
- Use one writer per Blend.
- Save a new version before rebuilding and save after each asset.
- Use short bounded commands to avoid freezes.
- Preserve every high unchanged.
- Do not delete unrelated objects or collections.
- Do not scale a presentation copy.
- Clear low-only accidental animation data before arranging when construction created it; do not turn this into a scene-wide cleanup.
- Stop if the target file or source object does not match the expected guard.

## Direct handoff

After the builder creates the mesh:

1. Confirm only that the expected non-empty low object exists.
2. Assign the requested opaque low material and wire display.
3. Disable high wire.
4. Place the low row with translation only.
5. Save once.
6. Report counts and actual method/plugin use.
7. Mark generated_for_user_inspection.
8. Stop.

Do not inspect geometry, calculate quality metrics, capture validation views, render, reopen, score, correct, retry, or generate alternatives after step 1.
