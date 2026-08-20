# N01-N08 training and formal-pass lessons

Read this reference before correcting a rejected topology method, training on representative assets, or starting a formal live-Blender batch after training. It records what the N01-N08 trials proved. Treat all candidate counts as training evidence, not accepted production targets.

## Contents

- [Separate training from formal production](#separate-training-from-formal-production)
- [Required product quality](#required-product-quality)
- [Failures observed during training](#failures-observed-during-training)
- [Trial-by-trial error and correction ledger](#trial-by-trial-error-and-correction-ledger)
- [Method boundary learned](#method-boundary-learned)
- [Per-asset construction lessons](#per-asset-construction-lessons)
- [Comparison and camera rules](#comparison-and-camera-rules)
- [Live-session and performance safeguards](#live-session-and-performance-safeguards)
- [Formal one-pass checklist](#formal-one-pass-checklist)
- [Failure-response table](#failure-response-table)

## Separate training from formal production

Use two distinct stages.

### Training and calibration

Training may contain several trials. Its purpose is to expose mistakes, compare approaches, and freeze a reliable method before formal production.

For every training trial, record:

```text
previous_trial:
visible_failures:
controlling_views:
root_cause:
new_hypothesis:
regions_or_method_changed:
expected_visual_change:
observed_result:
lesson_kept_for_formal_work:
```

A new color, row, seed, face target, or object name is not a new lesson. Do not continue producing near-duplicate trials when the root cause is unchanged. A useful trial must connect an observed failure to a changed decision and a verified result.

### Formal topology

The purpose of training is first-candidate accuracy. After training is summarized and the representative method is confirmed, perform one formal topology pass per asset and aim to get that model correct on the first generated candidate:

- Create one authoritative low per high.
- Finish classification, high-derived measurements, feature controls, component plan, radial/axial counts, topology-flow plan, and geometry/bake split before creating geometry.
- Apply the trained per-class method correctly from the first modeling operation instead of using production as another experiment.
- Compare and validate the authoritative candidate before calling the asset complete.
- Treat any rare local correction exposed by validation as completion of the same formal object, never as an expected trial loop or another colored deliverable row.
- Show one final low row paired with the highs unless the user explicitly requests training history.
- Do not manufacture three formal versions or three near-identical rows.
- Do not present training trials as formal completed assets.

The expected result is that the first formal candidate passes because the training knowledge was applied before building. If an unexpected gate failure occurs, it remains unfinished work on the same authoritative object; it is not permission to resume blind trial-and-error.

## Required product quality

The result is a next-generation product/game low, not merely a smaller mesh:

- Match the high's proportion, major volumes, orientation, ground contact, openings, protrusions, attachment roots, and silhouette in several fixed views.
- Reduce planes, straight spans, broad centers, hidden interiors, and non-silhouette regions aggressively.
- Send wrinkles, shallow carvings, seams, dents, micro-connectors, surface grain, and other non-silhouette details to normal baking.
- Use triangles and quads deliberately. Do not force all quads, random triangulation, slivers, crowded rings, fragmented faces, or uniform density.
- Retain loops only for real turns, arcs, openings, load-bearing structure, profile changes, attachment roots, shading transitions, and outer contours.
- Route every asset by region. Do not deliver whole-object voxel remesh or QuadriFlow output; allow controlled Decimate only for a classified complex continuous asset/region from a fresh high-derived copy.
- Compare each low with its own high before moving to the next asset.
- Keep high wire disabled and low wire clearly visible.
- Prove the method on one representative high-risk asset and obtain confirmation before formal batch production.

## Failures observed during training

1. A clean primitive proxy was used for the integrated boot. It did not match the cuff, shaft, toe, sole, heel, straps, or overall identity. The method was wrong before local topology quality was considered.
2. A later high-derived boot collapse matched the silhouette better but produced random triangles, slivers, radial fans, wrinkle-following density, and poor editing flow. Fidelity did not make raw generator output acceptable.
3. Some mechanical candidates matched approximate world bounds but not the real profile. Small handles, latches, hinges, tabs, pivots, supports, and other protrusions were guessed, undersized, or misplaced.
4. Opaque low surfaces hid high-poly regions in aligned inspection. Perspective row spacing also distorted apparent size, making high/low matching harder to judge.
5. Cumulative viewport orbiting created uncomfortable, inconsistent views. Different framing made comparison unreliable.
6. Several colored trials had different counts but nearly the same topology hypothesis, feature placement, and visible result. They did not demonstrate learning.
7. Long monolithic scripts made Blender appear frozen. After reopening, earlier work seemed missing because exact file version, save milestone, hidden collections, and view-layer exclusions were not verified first.

Historical training counts demonstrate that a changed number alone proves nothing:

| Asset | Trial 1 tris | Trial 2 tris | Trial 3 tris | Training interpretation |
| --- | ---: | ---: | ---: | --- |
| N01 weight rack | 5,744 | 4,608 | 5,056 | Density changed; acceptance still depends on rack/weight alignment and useful flow. |
| N02 bed/cot | 2,976 | 2,464 | 2,720 | Bevel/tube balance matters only when cushion and frame silhouettes improve. |
| N03 equipment case | 1,944 | 1,144 | 1,200 | AABB can match while handle and latch controls remain wrong. |
| N04 chair | 1,156 | 860 | 1,024 | Tube segments are secondary to seat, back, frame gaps, and attachments. |
| N05 bench vise | 2,400 | 2,112 | 2,208 | Small count changes cannot replace semantic cast-body/jaw/screw/base construction. |
| N06 integrated boot | 2,522 | 1,414 | 1,730 | High-derived reduction is allowed, but openings and wire flow remain independent gates. |
| N07 log bundle | 1,760 | 920 | 1,160 | Each log and cut end must remain legible; repetition does not justify one fused remesh. |
| N08 chimney assembly | 2,020 | 1,132 | not executed | Training stopped before a third candidate existed. An edited script is not Blender progress. |

These are not budgets and not proof of final acceptance.

## Trial-by-trial error and correction ledger

Keep this chronology as negative and positive training evidence. Do not relabel any rejected trial as accepted merely because later automation produced a green audit flag.

### Trial stage 0: inherited yellow automatic reductions

**Observed error:** The batch looked like uniform automatic simplification. Hard-surface components were collapsed without construction logic, planar areas still carried waste, and complex items could contain random triangulation. The boot proxy differed greatly from the high.

**Root cause:** The workflow selected a batch simplification technique before classifying each asset. It also treated a clean or low-count mesh as a product solution.

**Correction learned:** Discard rejected yellow lows as modeling inputs. Return to each untouched high, classify construction, and choose semantic reconstruction or a component-wise cage/patch strategy. Complete one representative asset and obtain confirmation before formal batch work.

### Trial stage 1: first boot semantic proxy

**Observed error:** The shaft, cuff, toe, heel, outsole, straps, proportion, and pose read as a different boot even though the proxy wire looked organized.

**Root cause:** A generic clean cage replaced the high's integrated identity. Topology neatness was reviewed before macro likeness.

**Correction learned:** Reject at the first primary-view mismatch. For a structurally complex integrated boot, derive deliberate contour rings and local patches from the untouched high. Do not copy or reduce the high, and do not spend time polishing a clean but wrong proxy.

### Trial stage 2: raw very-low boot reduction

**Observed error:** Silhouette improved, but the wire showed long thin triangles, random fans, high-valence poles, wrinkle-following density, and abrupt face-size changes.

**Root cause:** Raw Decimate output was mistaken for finished topology because it was very low and close to the high.

**Correction learned:** Unclassified raw collapse is not enough. For normal audited production, rebuild or regularize flow; for the user-approved fast topology route, controlled reduction may be promoted only for the classified complex continuous region, while preserving the high and removing zero-area faces. Never use that exception on structured or aggregate regions.

### Trial stage 3: first orange/blue/green training rows

An earlier three-row training report itself contains proof that most candidates were not ready, even though the scene arrangement existed:

| Asset | Recorded result | Error exposed | Correction for formal work |
| --- | --- | --- | --- |
| N01 | Three-view mean silhouette about `0.533 -> 0.557 -> 0.570` | Rack-side placement and proportions remained far below production likeness. | Measure rack OBB, every support, shaft, plate center, diameter, thickness, and spacing before building. Reject below target instead of calling the upward trend sufficient. |
| N02 | Orientation was wrong, then rebuilt around an estimated `21.1°` OBB | Repeated rails and cushion/frame relation were not derived completely from the high. | Freeze high-local axes, frame manifest, cushion envelope, and gap measurements before formal geometry. |
| N03 | Best three-view mean about `0.944` | AABB agreement hid wrong shell edge, handle footprint, latches, hinges, and small hardware. | Use per-feature local controls and six-view overlays. `0.944` remains a rejection, not a near-pass. |
| N04 | Weakest view about `0.425 -> 0.460 -> 0.461` | Back height, rear-foot contact, top tube, cushion/frame silhouette, and gaps remained grossly wrong. | Rebuild from measured frame paths and cushion boundaries. A tiny metric increase does not justify continuation. |
| N05 | Top view about `0.781 -> 0.797` | Base outline, mounting holes, cast-body profile, and functional parts were incomplete. | Measure the concave base, real holes, jaws, screw axis, hub, and handle before formal construction. |
| N06 | Weakest view about `0.973` | Product fidelity became plausible, but reduction still required a separate wire-flow decision. | Keep high-derived method, preserve openings/sole/straps, and accept only after wire and shading pass. |
| N07 | Weakest view about `0.751 -> 0.760 -> 0.770` | Log diameters, lengths, top-row irregularity, and bundle silhouette remained inaccurate. | Measure all ten log axes/endpoints/radii. Do not confuse repeated procedural logs with a matched bundle. |
| N08 | Mean about `0.829` | Side outlet shape, body width, U-frame, seams, clamp, and cap relationship remained wrong. | Build a high-derived component/feature manifest before geometry. Reject the whole candidate at this score. |

**Root cause:** The process treated incremental metric improvement and successful row assembly as completion. Several scores were far below the skill's own silhouette threshold.

**Correction learned:** A trend is training evidence, not acceptance. Any wrong controlling outline fails. Do not advance to another asset when the current representative is still visibly wrong.

### Trial stage 4: later R1 reconstruction set

**Observed error:** R1 reports contained good topology counts, no N-gons, small AABB deltas, and `wire_distribution_pass=true`, but the user still found major high/low size, outline, and protrusion mismatches. The equipment-case screenshot showed that the low and high read differently even when both were box-like.

**Root cause:** `pass_gate=true` was inferred from structure, bounds, and generated comparison files. Small hardware was not fully controlled, and visual likeness was not independently proven. In some assets the reported AABB delta was small because macro bounds were fitted while internal proportions and feature placement remained wrong.

**Correction learned:** Never set `silhouette_6view_pass`, `construction_pass`, or `final_pass` from topology validity, bounds, object existence, or screenshot generation. Require measured aligned silhouettes plus visual inspection of feature controls. A user-visible mismatch overrides an automated green flag.

### Trial stage 5: R2 density reductions

**Observed error:** R2 reduced triangle counts by roughly 12-48 percent, and several reports showed exactly zero bounds delta. Nevertheless, the user rejected the assets because overall matching and small protrusions still looked wrong.

**Root cause:** The workflow optimized density and reused approximate component layouts without first eliminating the R1 likeness errors. Exact bounds were treated as if they proved shape, while internal profile, hardware placement, negative space, and construction remained insufficiently measured.

**Correction learned:** Do not optimize a rejected shape. Before reducing density, fix the highest-priority silhouette and construction error. Regard exact AABB equality as registration only and audit feature residuals separately.

### Trial stage 6: R3 intermediate-density variants

**Observed error:** R3 often selected segment counts between R1 and R2. The user observed that the first, second, and third topologies looked almost the same and did not satisfy the instruction to learn from the earlier errors. N08 R3 was only prepared in a local script and was never executed in Blender.

**Root cause:** Changing global segment counts did not change the root modeling hypothesis or repair the visible mismatch. Color-coded rows gave the appearance of iteration without enough semantic change. Prepared code was at risk of being reported as scene progress.

**Correction learned:** Training variants must change a responsible decision, such as method, measured profile, component decomposition, hardware center, opening, or topology flow. Do not create a variant only by choosing an intermediate density. Report a candidate only after it exists in Blender and the file is saved.

### Trial stage 7: comparison-view failure

**Observed error:** The chosen rotated view was uncomfortable; opaque low geometry hid parts of the high; separated perspective rows made apparent size unreliable; matching became harder to inspect.

**Root cause:** Presentation and diagnostic views were conflated. Cumulative orbit state replaced deterministic cameras, and solid overlap caused occlusion.

**Correction learned:** Use fixed orthographic aligned overlays for decisions, low wire over high solid for visibility, a separate low solid/wire row for topology, and one canonical saved perspective only for orientation.

### Trial stage 8: live-session stall and apparent loss

**Observed error:** Blender stopped responding. After reopening was authorized, previously completed lows appeared missing.

**Root cause:** Too much work was sent as long operations without bounded checkpoints, and recovery started before verifying the exact saved file and hidden scene state.

**Correction learned:** Save after each asset and before expensive work; maintain a milestone manifest; stop bridge mutations when Blender is unresponsive; on reopen, verify file version, object names, collections, view-layer exclusions, and hide flags before rebuilding.

### Final training conclusion

The formal goal is not to reproduce these trials more efficiently. It is to prevent their errors before the first formal candidate is built:

- Decide the method correctly before geometry.
- Measure every macro profile and silhouette-visible feature.
- Plan component logic and topology density per region.
- Use deterministic diagnostic views.
- Keep product fidelity, construction, density, wire flow, and shading as separate gates.
- Create one formal authoritative low and get it right the first time.

## Method boundary learned

### Integrated complex assets

Integrated boots, saddles, cloth and similar complex surfaces may use controlled direct reduction from a fresh untouched-high copy when semantic proxy reconstruction would lose identity. Preserve openings, rims, pose, major volumes, straps/attachments and silhouette-visible tabs; send stitching, grain, dents and shallow seams to baking. When structured editing or deformation flow is required, use RetopoFlow-assisted drawing, contour rings, local cages or patches instead.

### Semantic reconstruction

Use semantic reconstruction for mechanical, hard-surface, planar, rotational, repeated, and multipart assets:

- Rebuild logical pieces separately according to real construction.
- Measure each silhouette-visible part from the high in local space.
- Preserve visible gaps, overlap order, pivots, openings, clearances, and attachment roots.
- Keep broad planar panels and long straight spans sparse.
- Choose radial sides from the target-distance silhouette; add axial rings only where radius, slope, thickness, or construction changes.
- Bake shallow fasteners, grooves, ribs, stamped details, dents, weld noise, and micro-bevels when they do not affect silhouette or negative space.

Never use whole-object voxel remesh, uniform QuadriFlow, or undifferentiated pure Decimate as the final topology for mechanical assemblies. Mixed assets require a region map: complex soft regions may be reduced, structured parts reconstructed, and aggregate piles represented by a sparse outer envelope.

## Per-asset construction lessons

### N01 weight rack

- Reconstruct frame beams, supports, shafts, hubs, and plates as logical parts.
- Derive visible plate count, center, spacing, diameter, and thickness from the high.
- Keep long beam faces broad. Spend radial segments on plate silhouettes, not along straight shafts.

### N02 bed or cot

- Separate tubular frame, rails, cushion/fabric, pillow, and supports.
- Preserve frame clearances and negative spaces below and around cushions.
- Use the high-derived cushion envelope and bake small cloth folds.
- Add bevel/profile bands only when target-distance silhouette or shading proves the need.

### N03 equipment case

- Treat AABB agreement as registration only.
- Build lid, base, seam, handle, latches, hinges, tabs, and side hardware from measured local controls.
- Measure asymmetric hardware separately; do not mirror unless the high proves symmetry.
- Validate handle footprint and every protrusion in orthographic overlays.

### N04 chair

- Separate seat/back cushions from the tubular load-bearing frame.
- Preserve rail gaps, leg spread, feet, cushion thickness, and attachment order.
- Use enough tube radial sides for the viewing-distance silhouette, with no redundant lengthwise rings on straight spans.
- Bake soft cushion wrinkles that do not change the outer contour.

### N05 bench vise

- Reconstruct cast base, rotating body, jaws, slide, screw, hub, and handle as semantic parts.
- Preserve jaw opening, screw axis, handle extent, mounting base, and cast-body outline.
- Allocate radial sides to priority round parts and keep flat jaw/base surfaces sparse.
- Never simplify the whole vise as one shell.

### N06 integrated boot

- Use fresh-high controlled reduction because a coarse proxy loses identity.
- Preserve every controlling opening, strap, outsole, and heel layer.
- Evaluate topology flow independently from silhouette fidelity.
- Do not accept random all-triangle collapse merely because the count is low.

### N07 log bundle

- Keep individual logs or deliberate overlapping shells when their count and gaps are visible.
- Preserve cut-end diameter, length, placement, and bundle silhouette.
- Use few axial stations on straight logs and modest radial counts on visible cut ends.
- Bake bark grain and shallow cracks.

### N08 chimney assembly

- Separate pipe spans, rolled seams, clamp, hinge/lever, side inlet, flange, lower support, collar, rain-cap posts, and roof.
- Allocate round silhouette density to pipe, inlet, clamp, collar, and roof while keeping straight axial spans sparse.
- Preserve the real gap under the rain cap and the side-inlet transition.
- Do not claim a candidate exists until its script has run in Blender, the object is verified, and the Blend is saved.

## Comparison and camera rules

Use two complementary review displays:

1. **Aligned diagnostic overlay:** keep the high solid neutral gray with wire disabled. Display the aligned low as colored wire or translucent/wire overlay so high geometry remains visible. Use this view for silhouette, dimensions, openings, protrusions, and construction matching.
2. **Separated topology row:** use a translation-only low display copy with colored solid and wire. Use this row for topology readability, not apparent-size judgment.

Capture fixed front, back, left, right, top, and bottom orthographic views. Use one saved canonical perspective only for orientation. Do not use cumulative orbit operations for validation, and never judge size from separated perspective rows at different camera distance.

Maintain a `feature_controls` record for every silhouette-visible protrusion, opening, handle, latch, hinge, foot, bracket, cap, support, and attachment root. Record the high-derived local center, dimensions, controlling views, and geometry/bake decision. Do not place these features by eye from one perspective.

Reject a candidate when any controlling view shows a wrong size, center, profile, opening, or protrusion even if bounds and topology audits pass.

## Live-session and performance safeguards

- Re-verify the Blender PID, responsiveness, exact open filepath, and bridge connection at the start of each work period. Never trust an old PID.
- Use only the current Blender when requested. Open another instance only with explicit user authorization.
- Work one asset and one bounded validation operation at a time. Avoid monolithic batch calls that block feedback or freeze Blender.
- Save after each accepted asset and before expensive remesh, render, join, or batch-layout operations.
- Maintain a milestone manifest containing exact saved filepath, completed asset IDs, object names, collections, counts, and last confirmed save.
- Send short progress updates at useful milestones. Long automatic silence is not evidence of progress.
- If Blender stops responding, stop issuing mutations and do not queue more bridge calls. Record the last confirmed save and process state.
- If reopening is authorized, preserve or close the old session safely, open the latest confirmed file, and inventory object names, hidden collections, excluded view layers, and superseded rows before rebuilding anything.
- If completed lows appear missing, first verify exact file version, collections, view layer, hide flags, and manifest. Do not recreate duplicates blindly.
- Treat an edited local script as preparation only. Report Blender progress only after execution succeeds, expected objects are verified, and the Blend is saved.

## Formal one-pass checklist

Before formal production:

- Training lessons are summarized and method rules are frozen.
- One representative asset has passed silhouette, construction, density, wire, and shading review and received user confirmation.
- Every asset has a written method decision, feature-control table, geometry/bake split, face band, construction plan, profile-event plan, cap strategy, and expected topology distribution before geometry is created.
- The live session, exact file, PID, bridge, save destination, and high manifest are verified.

For each formal asset:

1. Build one authoritative low from the high using the fully planned trained method; do not create a throwaway formal first attempt.
2. Save it immediately.
3. Compare it with the high in six fixed orthographic views and one canonical perspective.
4. Inspect aligned high-solid/low-wire overlay and a separate close low-wire view.
5. Confirm size, silhouette, openings, protrusions, construction, and topology flow before advancing. If an unexpected miss appears, correct the same authoritative object and update the training lesson; do not create a routine sequence of alternatives.
6. Run structural and topology-flow audits.
7. Mark it accepted only when all gates pass.

After all assets:

- Arrange one final high row and one formal-low wire row in one-to-one order.
- Keep highs wire-off and formal lows wire-on.
- Save, reopen, audit the layout, and confirm that every high/low pair is visible.
- Keep training trials superseded or hidden unless the user asks to inspect them.

## Failure-response table

| Failure | Required response |
| --- | --- |
| Candidate reads as another object | Reject immediately and change the method or macro construction. |
| Low/high size or contour differs | Use aligned orthographic overlays and rebuild the responsible profile; do not scale the review copy. |
| Small protrusion is missing or misplaced | Measure its high-local center/dimensions, add it to `feature_controls`, and reconstruct it separately. |
| High is hidden in an overlay | Switch aligned low to wire/translucent diagnostic display; keep solid/wire only for the separated topology row. |
| View rotation is uncomfortable | Restore fixed axis views and one canonical perspective; stop cumulative orbiting. |
| Training trials look the same | Stop, diagnose the unchanged root cause, and define a materially different hypothesis. |
| Count changes but product read does not | Do not call it improvement; compare controlling views and density placement. |
| Boot matches but raw wires are poor | Reject the generator output, keep high-derived fidelity, and regularize or locally rebuild the flow. |
| Mechanical object is globally remeshed | Discard it and reconstruct real detachable components. |
| Blender becomes unresponsive | Stop mutations, preserve the last confirmed milestone, and reopen only with explicit permission. |
| Earlier lows seem missing | Verify file version, collections, view layer, hide flags, and manifest before rebuilding. |
| Script is edited but not executed | Report pending preparation, not completed Blender work. |
| Training history is mistaken for formal output | Hide/supersede trials and build one authoritative formal low per high. |
