# Learned asset and failure lessons

This file preserves construction knowledge from earlier H01-H15 and N01-N08 work. It is input to the first build, not an instruction to generate trials or inspect the generated result afterward.

## Global lessons

### Failure-to-rule map

| Observed failure | Root cause | Mandatory correction before the next build |
| --- | --- | --- |
| Low and high differ in overall size or proportion | Global bounds, wrong coordinate space, or presentation scaling controlled shape | Preserve the high transform, measure high-local sections, construct aligned, and translate only for presentation |
| Small protrusions, handles, fingers, straps, or openings do not match | Main body was built first and feature geometry was guessed | Record feature roots, path/axis, section, opening boundary, and maximum extent from the current high |
| A continuous product was fragmented or a mechanical assembly was fused | Source islands, materials, or category labels decided component count | Split only from real gaps, motion, overlap, occlusion, or independent construction evidence |
| Flat regions contain uniform grids, bevel stacks, or center fans | Face count was distributed uniformly or all-quads were treated as the goal | Use broad faces on planes and spend topology only on silhouette, profile events, openings, and junctions |
| Direct reduction produced noisy hard-surface topology | Direct reduction was applied by batch or source-object count | Restrict it to genuinely integrated complex objects or the evidence-backed exceptionally-complex identity-loss fallback |
| A vessel and its contents became one uniformly decimated triangle field | FBX preparation joined the source and the joined state was mistaken for one integrated organic form | Reconstruct the vessel structure and limit organic reduction/cage work to the proven content region |
| Correcting one handle or attachment makes an already approved body worse | The whole asset was rebuilt instead of isolating the rejected feature | Treat explicit user approval as a scope lock; preserve approved components unchanged and replace only the responsible independent component |
| A handle aligns in one overlay but misses in depth or on the opposite side | One projected offset or a symmetric template was used as a 3D path | Measure both roots, grip axis/endpoints, tube section, and multiple high-local centerline stations from front, side, and top evidence |
| The result is hard to inspect | Transparency, high wire, or display changes obscured shape | Keep the high solid with wire off and the low opaque yellow with wire on |
| Production time is lost to repeated reviews and rebuilds | Training-style iteration leaked into formal execution | Make all decisions before geometry, generate once, arrange/save/report, and stop for user inspection |

### Shape mismatch is a planning failure

The most serious historical failure was a clean low whose size, silhouette, opening, protrusions, or construction no longer matched the high. The usual causes were:

- Using only global bounds or object center.
- Reusing fixed proportions from a generic template.
- Scaling a presentation copy instead of rebuilding the responsible profile.
- Matching the main body but guessing handles, latches, feet, brackets, spouts, hinges, fingers, straps, or other small silhouette controls.
- Treating a category name as identity.

Prevent this before geometry with current-high local sections, contour events, feature controls, opening boundaries, and attachment roots.

### Clean topology can still describe the wrong object

Low face count, regular quads, manifold arithmetic, and matching AABB are not shape authority. A generic pump, board, lantern, bottle, case, chair, or boot proxy remains wrong when the actual high has different proportions or components.

### Count is density evidence only

Historical face counts can suggest scale but never define geometry. Do not preserve or invent parts to reproduce a previous number. Do not force a fixed percentage on a batch.

### One product is not always one mesh

Use one continuous envelope for molded, cast, pressed, wrapped, anatomical, or blended surfaces. Keep real moving pieces, true gaps, independent overlaps, and mechanically separate hardware distinct. A seam or source island alone does not justify splitting.

### Direct reduction has a narrow role and a complex-asset fallback

Direct reduction worked best for integrated, irregular, one-piece objects where semantic reconstruction destroyed identity. It failed when used as a blanket solution for mechanical or repeated hard-surface assets.

User preference adds one fallback: when the whole asset is exceptionally complex and component separation, semantic reconstruction, or a hybrid build would be unreliable or lose identity, use one whole-asset controlled reduction from a fresh high duplicate when the measured source-topology fragmentation gate passes. A duplicate-position triangle soup may instead use the server-qualified exact-weld work copy, which has unchanged faces and bounds and clean manifold adjacency. This never authorizes changing the authoritative high or bypassing the final topology gate.

### Presentation must not change geometry

Rows are translation-only. Never scale or rotate a low to hide a mismatch. High wire stays off; low wire stays on and opaque.

### One build means one build

Historical colored rows and repeated passes were training artifacts. Formal execution uses the learned method once, saves once, and waits for the user. Do not generate variants or automatically correct.

## H01-H15 construction lessons

The exact object-to-ID mapping may change between files. Identify the current high by object evidence, not by remembered category alone.

### Integrated organic bodies

- Use a fresh-high derived cage, qualified QuadriFlow, or controlled reduction with bounded surface fitting.
- Preserve the outer body, limbs, wings, fingers, cuff, heel, sole, straps, and openings before folds or wrinkles.
- Do not replace the object with a generic smooth organic proxy.
- Do not let vertices project through to a nearby opposing surface.

### Open bowls and vessels

- Build a real outer shell, rim, inner wall, and cavity.
- Derive radial samples and section changes from the high.
- Treat loose contents, pebbles, food pieces, and surface noise as bake or separate content according to silhouette.
- Do not close the opening merely to obtain a simpler shell.

### Vessel with irregular contents routing failure

A server result that turns a dense vessel-plus-contents high into one lower-density all-triangle mesh is still whole-object reduction, even when the face target is met. The visible clues are random triangles across straight shell walls and rims, loss of deliberate rings, and the same reduction texture on both hard shell and irregular contents.

Correct the method before the next build:

- Do not treat the prepared `SOURCE_HIGH` object count as component evidence; preparation intentionally joins imported meshes.
- Prove the vessel using its axis/sections, rim, inner wall, cavity, base, and any handles.
- Reconstruct that structured region with sparse radial/profile topology.
- Bound controlled reduction, qualified remeshing, or a high-derived cage to only the irregular content region when that surface affects silhouette.
- Preserve the high-derived contact and occlusion boundary between shell and contents.
- Do not try to fix this routing error by raising or lowering one global target face count.

If the vessel-plus-contents asset is exceptionally complex and the responsibility boundary itself cannot be established reliably, stop trying to reconstruct guessed regions and use the exceptional whole-asset controlled-reduction fallback.

### H03-style pump or handled container failure

The failed version used a generic pump/lever body and missed the actual large lowered U-shaped bail and front structure.

Build from:

- Actual body and head sections.
- Measured large U-bail path and both roots.
- Front spout/nozzle or other silhouette-changing hardware.
- Real pivots and attachment centers.

Do not infer a small side handle from one asymmetrical extent.

### H05-style hanging board failure

The failed version invented an X panel and rear stand from a generic board template.

Build:

- The measured board contour and real thickness.
- Only the actual top hanger or hole proven by the high.
- Broad front/back faces.

Bake wood grain, printed or stamped graphics, and shallow surface markings. Do not invent braces, stands, or X patterns.

### H06-style lantern failure

The failed version used fixed ratios and lost the actual base, chamber, eave, roof, and finial proportions.

Build:

- Runtime high-derived vertical change points.
- Open chamber posts and real negative space.
- Sparse roof and base profiles.
- Finial only when silhouette-visible.

Bake dense shallow slats and decorative ribs that do not create true holes.

### H07-style broad container failure

The failed version became a narrow bottle with a side handle and false feet.

Build:

- Broad asymmetric body sections.
- Shoulder, neck, and cap from current-high profiles.
- The actual top U handle and both roots.
- Only ground-contact features proven by the high.

Do not interpret lower grooves as feet.

### H08-style molded bin or tray

- Keep the tapered body and rim as one continuous shell when the high shows a molded casing.
- Preserve the real downward-open rear U/channel as negative space.
- Keep a truly separate inset panel only when its overlap is constructionally independent.
- Bake shallow relief.

### H09-style bucket

- Build an open shell with inner cavity and a sparse rim.
- Derive the bail path, roots, and grip from the high.
- Use radial density for the round silhouette and very few wall rings on straight spans.
- Do not model every shallow circumferential mark.

Correction lessons from the user-reviewed bucket:

- Do not rebuild the shell, rim, cavity, or wall topology merely to fix the bail. Rebuilding the whole bucket distorted an already preferred body, over-expanded the upper wall, added unnecessary rings, and narrowed the lower body.
- Do not repair a three-dimensional bail by widening only its projected X position. That can improve one overlay while leaving both side spans wrong in Y/Z and making the path turn toward the grip too early.
- On explicit user feedback that the previous body was better, preserve that approved body as a correction-scope lock. The current high remains the only authority for every changed bail vertex.
- Before the correction build, measure the current high in high-local coordinates from front, side, and top views: both roots, tube section, grip axis and endpoints, and multiple intermediate centerline stations on each side.
- When the high is one fused mesh and the bail lies outside a rotational shell, sample the left and right outer radial surfaces in narrow height bands. Average each tube cross-section to estimate its centerline; do not mistake the outermost surface point for the center.
- Measure both sides independently unless the current high proves symmetry. Never store the resulting coordinates as reusable bucket constants.
- Replace only the independent bail sweep when the shell, grip, and pivot housings are already approved. Keep the approved components vertex-for-vertex unchanged and preserve rejected versions as hidden backups.

### H10-style rotational bottle

- Prove the axis from several high-derived sections.
- Keep rings only at base, wall, shoulder, neck, lip, and other real profile changes.
- Avoid uniform vertical subdivisions.
- Add non-rotational attachments separately only when proven.

### H11-style mug

- Build a real open cup shell and rim.
- Construct a two-root C/U handle from the measured negative-space path.
- Avoid a floating block, diamond root, or handle attached from one guessed point.

### H12-style repeated-panel housing

- Preserve the measured outer shell and main stepped layers.
- Replace dense repeated ribs or surface relief with a broad chamber/panel envelope when they do not create openings.
- Keep side or top frames only when they change silhouette or construction.

### H13-style cooler or box

- Measure the rounded lower box, lid steps, handle gaps, feet, and panel offsets separately.
- Keep broad side panels sparse.
- Do not make all corners and lid layers one uniform bevel grid.

### H14-style one-piece handled tool

- Preserve the continuous relationship between rounded head, broad rear wing/root, neck, and long grip.
- Use broad caps instead of a high-valence center fan.
- Bake shallow front emblem or logo relief.
- Do not split the object when the high shows one continuous molded piece.

### H15-style glove or complex organic

- Use fresh-high direct reduction or a current-high cage.
- Preserve fingers, thumb, cuff, and gaps before surface wrinkles.
- Use bounded same-side fitting so finger valleys do not collapse or bridge.
- Expect a higher count than a flat prop when required by silhouette.

## N01-N08 training lessons

These are construction lessons only. Do not recreate the historical training rows.

### N01 weight rack

- Preserve the rack frame, supports, weight spacing, axles, and visible gaps as mechanical construction.
- Use reusable low-density parts for repeated weights while retaining the current high's dimensions and positions.
- Do not merge the whole rack into one remeshed mass.

### N02 bed or cot

- Separate structural rails, legs, guard bars, and mattress/cushion volumes according to true gaps.
- Keep large fabric or mattress planes sparse.
- Preserve the outer frame silhouette and meaningful rail spacing.

### N03 equipment case

- Global bounds were insufficient and produced wrong shell edges, handle footprint, latches, hinges, and hardware.
- Measure lid/body step, corner radii, handle opening, latch centers, hinge positions, and protrusion depths locally.
- Keep molded casing continuity; separate only real moving hardware.

### N04 chair

- Preserve frame, legs, guard rails, cushions, and real gaps.
- Keep cushions continuous soft envelopes when appropriate.
- Do not replace a structured chair with stacked generic boxes.

### N05 bench vise

- Reconstruct base, rotating body, fixed/moving jaws, screw hub, handle bar, and stops as meaningful mechanical parts.
- Preserve jaw gap, pivot/slide logic, and handle orientation.
- Use sparse cylinders and broad planar jaw faces.
- Do not rely on a whole-object automatic remesh.

### N06 integrated boot

- This is the reference direct-reduction case.
- The failed semantic proxy lost the cuff opening, shaft/toe/heel proportions, sole outline, straps, and integrated surface identity.
- Start from the fresh high, preserve the open cuff and sole silhouette, use one asset-specific reduction, and keep bounded local correspondence.
- Small wrinkles and stitching belong in normal maps.

### N07 log bundle

- Preserve the bundle outline, individual log ends that affect silhouette, binding pieces, and meaningful inter-log gaps.
- Reuse sparse cylindrical topology but fit each visible log to its current-high position and diameter.
- Bake bark texture and shallow cracks.

### N08 chimney assembly

- Preserve stacked housing sections, cap/roof, supports, open gaps, and major flue shapes as separate construction where proven.
- Use sparse planar and rotational parts rather than one fused remesh.
- Bake repeated surface relief and tiny fasteners.

## Component-decision corrections

Past errors included both excessive splitting and excessive merging.

Keep joined when:

- A bin, cooler, airpot casing, or molded shell is continuous despite panel lines.
- A one-piece handled tool blends head, root, neck, and grip.
- An organic body forms one integrated surface.

Keep separate when:

- A handle has a true open gap and independent roots.
- A lid, lever, latch, hinge, pivot, bail, foot, rail, or bracket has independent construction.
- Repeated members create actual negative space.

Never let a source mesh-island count choose the production component count.

## Performance and recovery lessons

- Long monolithic batch scripts made Blender appear frozen. Work per asset with bounded calls.
- Save after each asset.
- Reconfirm the current PID instead of trusting an old one.
- If Blender stops responding, stop mutations and wait for user direction.
- If a file is reopened by authorization, verify the exact version and visibility state before doing any new modeling.
- Do not assume hidden or excluded lows are missing.

## Historical closed-component lesson

Repeated builds of the same closed hard-surface FBX produced otherwise clean low candidates with 9
or 15 boundary edges because manual strips/section rings were left unbridged. Detecting the boundary
and exiting is safe but causes avoidable task failure. In the single generation build, close solid
ends, bridge paired rings, and fill only simple component-local accidental gap cycles before UV
creation. Triangulate only faces created by the gap fill, then re-measure. Never touch `SOURCE_HIGH`,
never bridge separate components, and never cap a real cavity or through-opening; those require inner
and outer walls joined at the rim. Under the current user-selected server policy these measurements
are advisory: only zero-area/degenerate broken faces block delivery, and UV is deferred to its own stage.

## Final direct-output rule

Apply these lessons before and during the single construction pass. Once the expected low exists, arrange it, save it, report counts, and stop. The user performs all visual judgment. Do not start any automatic examination, score, comparison, correction, or second attempt.
