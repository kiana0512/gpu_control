---
name: blender-align-bake-models
description: Align mismatched high- and low-poly FBX/OBJ/GLB models before baking by solving only position, rotation, axis orientation, and scale in Blender, then export transform-only bake-ready copies and a validation report. Use for 高低模不重合、导入一键烘焙后大小/位置/旋转不同、自动拓扑或自动 UV 后坐标丢失、烘焙前匹配高低模、模型轴向错位，或把原始低模恢复到高模坐标。Never retopologize, rebuild, remesh, decimate, triangulate, edit UVs, or replace the user's low model as part of an alignment request.
---

# Blender Align Bake Models

Treat the high-poly model as the only coordinate authority. Match the low to the current high pose, preserve the source files, bake the solved transform into duplicate geometry, and export identity-transform bake files.

## Enforce transform-only scope

- Interpret `align`, `match`, `对齐`, `匹配`, and `烘焙前对齐` as transform-only operations.
- Change only translation, proper rotation, axis orientation, and user-permitted scale. Baking that solved transform into duplicate vertex coordinates is allowed.
- Never run retopology, remesh, voxel reconstruction, decimation, triangulation, dissolve, subdivision, shrinkwrap, mesh replacement, UV generation, or UV editing.
- Never substitute a generated low for the user's low, even when the original low is extremely sparse or cannot reproduce high-poly details.
- Preserve object count, mesh count, vertex/edge/polygon counts, face connectivity, UV layer names/counts/data, material slots, vertex groups, shape keys, and custom normals.
- Record a topology-and-UV fingerprint before alignment and compare it after Blender save and FBX readback. Reject delivery on any unexpected change.
- If transform-only alignment cannot pass, stop with a failure report and explain the geometric mismatch. Do not change topology to make validation pass.

## Inspect before matching

1. Confirm that the files represent the same asset. Stop if components are missing, proportions differ materially, or one file contains several unrelated assets.
2. Prefer the final UV low as `--low`; applying a rigid/similarity transform preserves its UVs.
3. Record whether the high is already in the desired bake pose.
4. Never copy the high object's Location/Rotation/Scale values directly onto the low. The mismatch may already be baked into vertices; direct copying can apply the transform twice.

## Run automatic alignment

Use Blender 4.2 or newer with [scripts/align_bake_models.py](scripts/align_bake_models.py):

```powershell
& '<blender.exe>' --background --python '<skill>\scripts\align_bake_models.py' -- `
  --high 'C:\assets\asset_high.fbx' `
  --low 'C:\assets\asset_low_uv.fbx' `
  --output-dir 'C:\assets\bake_aligned' `
  --require-low-uv
```

The solver samples both vertices and complete triangle surfaces, searches proper axis rotations, translation, and one uniform scale, then refines the best candidates with trimmed ICP. Surface sampling is mandatory for extremely sparse lows; vertex-only fitting can align corners while leaving large polygons intersecting the high. It writes no bake files unless the geometry, orientation, and ambiguity gates pass.

If a visually confirmed pair still misses only because its XYZ proportions differ slightly, rerun with `--allow-axis-scale`. This refines the already-solved orientation by matching the low bounds to the high in high-world XYZ axes, bakes that scale into the duplicate low, and preserves UVs. Keep the default 10% per-axis limit; do not use it to force unrelated shapes together.

Use `--straighten-high` only when the imported high has one traceable common object rotation. This applies the inverse rotation to both bake copies around the high center. If the tilt is baked into vertices, do not infer upright from PCA or a bounding box; ask for a user-approved manual rotation and pass it with `--manual-high-rotation X Y Z` in degrees.

Use `--rigid-only` when scale must not change. Never use `--allow-mirror` unless the user explicitly confirms that handedness should change; mirrored alignment affects winding and tangent-space baking.

Use `--prefer-current-orientation` for rotationally symmetric assets only when the current high and low are already visibly oriented the same way and the mismatch is mainly scale/translation. It resolves tied axial rotations by choosing the passing candidate closest to the low's current world orientation.

Use `--prefer-source-local-axes --match-bounds-center` for a one-high/one-low pair when the meshes came from the same asset axes but the low object received a random scene transform. This is especially important for sparse, nearly symmetric boxes: allow a near-best surface candidate within the default 10% score gap, preserve the semantic local-axis relationship so handles remain on corresponding sides, then center the bounds exactly.

## Enforce safety gates

Reject automatic delivery when any of these is true:

- the best surface match exceeds the configured normalized error;
- center or dimensions remain outside tolerance;
- two materially different orientations have nearly equal scores;
- a reflected solution is substantially better but mirroring was not authorized;
- `--require-low-uv` is set and the low has no active UV layer;
- any topology, UV, material-slot, vertex-group, shape-key, or custom-normal fingerprint changes;
- export readback no longer preserves the prepared bounds.

For a failed or ambiguous run, return only `bake_alignment_report.json` and request the user's high/low pair or a manual orientation choice. Do not enlarge bake ray distance or alter the low mesh to hide failed alignment.

## Validate and deliver

Require `pass: true` and render seven locale-neutral overlay views:

```powershell
& '<blender.exe>' --background --python '<skill>\scripts\render_alignment_views.py' -- `
  --blend 'C:\assets\bake_aligned\bake_alignment.blend' `
  --output-dir 'C:\assets\bake_aligned\validation_views'
```

Inspect front, back, left, right, top, bottom, and perspective. Show the high as a blue solid and the low as a strongly contrasting opaque solid with a dark wire overlay; never make either mesh transparent. Use viewport/object display colors only for inspection and do not replace the user's material slots. Confirm that distinctive asymmetric parts face the same direction and that the low surface and wire follow the high surface as intended.

Require fresh-FBX readback and verify that the original low's topology and UV fingerprint is unchanged. Numeric success never overrides a failed seven-view inspection.

Deliver:

- `bake_high.fbx`
- `bake_low.fbx`
- `bake_alignment.blend`
- `bake_alignment_report.json`

Then pass only `bake_high.fbx` and `bake_low.fbx` to the baking skill. Keep any cage as a separate later step derived from the aligned low.
