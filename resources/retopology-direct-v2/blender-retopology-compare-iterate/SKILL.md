---
name: blender-retopology-compare-iterate
description: Generate sparse next-generation game low-poly meshes directly from the current Blender high-poly source, using learned high-shape, component, silhouette, topology-density, and bake decisions. Use for Blender 自动或辅助重拓扑、次世代游戏低模、高模减面、机械分件重建、有机一体模型直接减面、RetopoFlow/QuadriFlow 辅助、批量高模生成低模和高低模分排展示。Generation ends immediately after saving and arranging the result for user inspection; this skill never starts automatic post-generation review, scoring, comparison, reopening, correction, retry, or acceptance.
---

# Blender Direct-output Game Retopology

Use the current high-poly as the only shape authority. Produce one planned low-poly candidate per high, save it, arrange it for the user, report counts, and stop.

Read [references/direct-output-construction-rules.md](references/direct-output-construction-rules.md) completely before every modeling task.

For any batch, unfamiliar asset, mechanical assembly, integrated organic object, or previously failed category, also read [references/learned-asset-lessons.md](references/learned-asset-lessons.md) completely before creating geometry.

## Non-negotiable output behavior

- Generate exactly one planned candidate for each requested high.
- After geometry is created, perform only presentation and persistence actions: assign the low material, enable low wire, disable high wire, place the requested row, save, and report object/face/triangle counts.
- Stop immediately after those actions and wait for the user.
- Never launch automatic post-generation review, audit, comparison, overlay, scoring, rendering, reopening, correction, retry, density variants, or acceptance.
- Never run six-view checks, silhouette IoU, topology-flow analysis, manifold analysis, layout verification, shading inspection, density ablation, or automatic repair after generation.
- Never set or claim final_pass, accepted, validated, game_ready, or equivalent status.
- Mark generated objects generated_for_user_inspection.
- Treat any later user feedback as a new explicit correction request. Do not anticipate it or continue automatically.

The only allowed automatic checks are mutation-safety guards:

- Confirm the current Blender PID, bridge, exact open filepath, and expected source object.
- Refuse to overwrite an unrelated object or mutate the high.
- Confirm that the builder created a non-empty named mesh before saving.
- Confirm that the save command returned successfully.

These guards protect the scene; they must not become geometry-quality review or trigger another modeling pass.

## Required input and output

Input:

- The user's current Blender scene, or a model file, containing one or more high-poly meshes.
- Optional platform, LOD, deformation, texture, or bake constraints.

Output:

- One separately named low for every requested high.
- Every high unchanged and visible.
- High wire disabled; low solid yellow and wire enabled unless the user specifies another display.
- A translation-only high/low presentation row when requested.
- A saved versioned Blend.
- Per-object face and triangle counts and actual plugin-use record.
- Explicit UV, cage, material, and bake status.

Do not require or use an old low-poly reference. Preserve unrelated old lows as user data unless the user explicitly asks to remove them. Never use a rejected yellow low, previous Decimate result, generic proxy, or historical accepted mesh as shape authority.

## Secure the live Blender session

1. Re-query the Blender process at the start of the work period; never trust an old PID.
2. Confirm responsiveness and the bridge listener.
3. Guard every mutation inside Blender with both expected PID and exact bpy.data.filepath.
4. Use the already open Blender unless the user explicitly authorizes reopening.
5. Save a new version before destructive rebuilding.
6. Save after each completed asset and before any expensive operator.
7. Use bounded per-asset operations and send short progress updates.
8. If Blender becomes unresponsive, stop bridge mutations. Do not assume data is lost and do not reopen without permission.
9. If earlier work appears missing, inspect the exact file version, collections, view layers, exclusions, and hide flags before rebuilding.

## Establish shape authority before geometry

Build a minimal per-asset shape_authority plan from the current high:

- authority: high_poly_only
- High-local profile sections for every main volume.
- Orthographic controlling contours for boxy or asymmetric assets.
- Openings and negative-space boundaries.
- Feature controls for silhouette-visible protrusions, handles, latches, hinges, feet, brackets, roots, rims, cuffs, fingers, straps, spouts, and supports.
- Component evidence for every proposed joined or separated part.
- Surface correspondence method.
- Template constants with provenance.
- Method decision and a non-binding face band.

Do not generate geometry from AABB, object center, extrema, percentiles, category labels, an earlier low, or remembered fixed proportions alone. These may assist registration but cannot define the object.

All visual observation and measurement belongs here, before low-poly geometry exists. Multi-angle contours may be sampled as construction input, but must never become a post-generation review trigger.

Before generation, lock coordinate and size authority:

- Record the source high's object transform and the local coordinate space used by every measurement.
- Construct in high-local coordinates or copy the exact source transform; never repair a size mismatch with a later presentation scale.
- Give every silhouette-changing handle, bail, strap, latch, hinge, foot, spout, finger, wing, bracket, or support an explicit root, path/axis, section, and maximum extent derived from the high.
- Give every opening an explicit boundary, depth direction, and wall/rim relationship.
- If these inputs cannot be established, stop before generating geometry and report the missing shape evidence. Do not emit a generic proxy.

For server or unattended batch execution, write the plan JSON and run:

    python scripts/guard_shape_authority_plan.py <execution_plan.json>

Use [references/execution-plan-schema.md](references/execution-plan-schema.md) as the required plan shape.

This is a pre-generation shape-input guard. It never inspects generated geometry and never starts a correction loop.

## Choose the construction method once

Use controlled direct reduction from a fresh high duplicate only when:

- The object is genuinely integrated and structurally complex.
- The high already contains the correct macro surface, proportions, openings, and silhouette.
- Semantic proxy reconstruction would lose identity.

Typical cases include an integrated boot, glove/hand-like form, poultry or similarly irregular organic prop, and other one-piece scans where the surface itself is the design.

A single source mesh is not enough evidence for direct reduction. Mechanical or hard-surface construction remains semantic reconstruction even when the high happens to be joined into one object.

Use semantic reconstruction when:

- The object is mechanical, hard-surface, planar, rotational, repeated, or clearly assembled.
- Main components, openings, pivots, latches, handles, panels, rails, ribs, or negative spaces need deliberate construction.
- Whole-object reduction would fuse gaps, distort openings, destroy part logic, or retain uniform noise density.

Use a per-component hybrid only when the high proves which components are integrated and which are mechanical.

Record method_decision as controlled_direct_reduction, semantic_reconstruction, or per_component_hybrid. Do not propagate one asset's method to the rest of a batch by category alone.

## Build from the high, not from a template

- Measure local cross-sections, contour events, attachment roots, gap boundaries, and protrusion locations from the current high.
- Use primitives and procedural profiles only as topology construction tools. Fit every shape-defining constant to high-derived evidence.
- Treat remembered scripts as construction grammar only. Re-measure the current high and instantiate new geometry.
- Block a generic template when it reads as a different product, even if it is clean and low-poly.
- Preserve major proportion, orientation, ground contact, main volumes, openings, and multi-angle silhouette.
- Preserve small protrusions only when they change silhouette, negative space, attachment logic, or functional read.
- Bake shallow embossing, grain, dents, wrinkles, grooves, seams, ribs, screws, buttons, stamped graphics, and micro-bevels when they do not control silhouette.

## Allocate faces deliberately

- Make planar and non-silhouette regions extremely sparse.
- Use one broad face or a small controlled triangle/quad layout on large flat panels.
- Add rings only at real profile changes, curvature changes, openings, attachments, or silhouette events.
- Keep cross-section density independent from lengthwise density.
- Use the lowest radial count that preserves the intended round silhouette.
- Allow triangles and quads together. Do not force all-quads on static props.
- Avoid long slivers, random triangulation, center-vertex fans across visible surfaces, crowded rings, uniform grids, and abrupt unexplained density changes.
- Do not add small decorative bevels to the low.
- Let the per-asset structure determine the final count. A few hundred faces is normal for many props, but count is never shape authority.

## Work quickly without weakening shape authority

- Analyze and measure each asset once, then run one planned builder once.
- Reuse topology grammar—lathe, section loft, profile extrusion, path sweep, repeated-part instancing, or bounded direct reduction—but never reuse another asset's geometry ratios, component count, coordinates, or face count.
- Cache current-high measurements and component evidence, not rejected low meshes or template dimensions.
- Limit expensive remesh or projection work to the eligible integrated object or component instead of the whole batch.
- Use bounded per-asset bridge calls and save immediately after each generated asset.
- Do not spend time on screenshots, renders, overlays, audits, scoring, density trials, or automatic correction after generation; the user performs that judgment.

## Decide continuity correctly

- Keep one continuous macro envelope for molded, pressed, cast, wrapped, anatomical, or blended forms.
- Do not split merely because the high has mesh islands, material slots, shallow seams, contact lines, grooves, or shading breaks.
- Split only for a true visible gap, independent motion/pivot, detachable overlap with its own silhouette, required material/shading discontinuity, or independent occlusion order.
- Preserve true openings even inside one continuous shell. Continuous does not mean filling holes.
- Avoid doubled rims, proxy-like gaps, floating parts, hidden tunnels, and detached appendage roots.
- Build visible roots and transitions for handles, wings, legs, straps, brackets, and supports.

## Use tools honestly

- Use RetopoFlow for deliberate surface drawing when available and appropriate.
- Use QuadriFlow or another remesher for qualified integrated organic objects only when driven by the current high and bounded fitting.
- Use Decimate only as a controlled direct-reduction operator on a fresh high duplicate, never as a uniform batch recipe.
- Never use whole-object voxel/remesh or raw Decimate as the final construction method for mechanical/hard-surface assemblies.
- Record a plugin as used only when its operator actually generated or modified the delivered mesh.
- Do not open another Blender to run a plugin when the user required the current session.

## Arrange and hand off

1. Keep the authoritative low aligned while constructing it.
2. Create the requested presentation row with translation only; never scale it to hide mismatch.
3. Keep highs solid and without wire.
4. Keep lows opaque, yellow, solid, and with wire.
5. Select/frame the requested rows without changing mesh geometry.
6. Save the current version.
7. Report the file path, object names, face/triangle counts, method, actual plugin use, and unfinished UV/cage/material/bake work.
8. State only that generation is complete for user inspection.
9. Stop. Do not inspect, grade, reopen, or modify the result again until the user responds.

Do not claim RetopoFlow use, bake readiness, validation, acceptance, or game-ready completion without a separate explicit user request and direct evidence.
