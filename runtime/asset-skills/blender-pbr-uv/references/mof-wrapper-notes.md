# MOF Blender wrapper notes

## Known safe behavior

- The add-on exposes `bpy.ops.object.auto_uv_operator()` and stores settings in `scene.mof_properties`.
- The wrapper copies a mesh, exports OBJ, runs the external MinistryOfFlat executable, imports the result, and transfers UV loop data back by topology.
- The target UV layer must exist on every selected mesh before calling the operator.

## Loose-component topology hazard

Do not send one mesh containing many disconnected components through the wrapper as one temporary OBJ. OBJ import may reorder disconnected components while preserving global counts. Topology-based Data Transfer can then attach correct UV coordinates to the wrong faces.

Temporarily separate every original mesh by loose parts, run MOF once on all
face-bearing parts, and validate every face part. Join only the parts belonging
to the same original mesh back together. Never join across original object
boundaries.

## Wrapper warning behavior

- Point/edge-only components can export but produce no unwrapped OBJ because they have no faces. Preserve them and exclude them from UV success counts.
- The operator may finish its full object loop and still raise a `RuntimeError` containing per-part import warnings. Do not infer total failure from the exception alone; validate each face-bearing part.
- If any face-bearing part has zero usable UV area, stop. Do not silently substitute a non-MOF unwrap unless the user explicitly allows it.

## Packing and export

- Disable intentional MOF overlap for identical and mirrored parts unless the user explicitly requests shared UVs.
- Disable MOF stretch-to-fill and keep relax enabled for low-distortion output.
- Repack all restored objects together once at the target resolution and
  padding so their UVs cannot overlap across objects.
- Verify the original mesh object count, per-object geometry digest, material
  order and transform after restoration. Reject linked mesh datablocks until a
  proven identity-preserving transfer path is available.
- Check cross-object triangle overlap in addition to the normal per-object QA.
- Put the corrected UV in the first and ultimately only UV channel because FBX consumers may ignore Blender's active-layer choice.
- Always reopen the exported FBX and run the same UV hard checks used on the `.blend`.
