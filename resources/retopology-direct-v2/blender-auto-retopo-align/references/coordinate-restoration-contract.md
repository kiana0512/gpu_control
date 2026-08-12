# Coordinate Restoration Contract

## Authority

The high model is the coordinate authority. Capture its full `matrix_world`, scene unit scale, axis convention, handedness, and world bounds before any normalization or presentation layout.

The generated low must use `source_high_local` coordinates. Its construction points are expressed in the high object's local coordinate system and its `matrix_world` equals the high's source matrix.

## Normalized Work Space

If a builder temporarily normalizes, centers, rotates, or scales the work copy, store the exact 4x4 matrix `work_to_world`. Restore a candidate with:

```python
candidate_to_source_local = (
    source_matrix_world.inverted()
    @ work_to_world
    @ candidate.matrix_world
)
for vertex in candidate.data.vertices:
    vertex.co = candidate_to_source_local @ vertex.co
candidate.matrix_world = source_matrix_world.copy()
```

When no normalization occurred, `work_to_world` is the identity matrix.

Do not reconstruct this matrix from AABB bounds. Bounds lose rotation, handedness, parent transforms, and axis history.

## Presentation

Interactive side-by-side layout is allowed only on temporary preview copies. The generated object delivered to the finalizer must not retain a presentation offset. The server prompt therefore requires `presentation_offset_applied: false`.

The finalizer defensively normalizes the low matrix into the high local space and removes a pure world-center offset. It does not guess a new rotation or non-uniform scale. If shape-space size or orientation is inconsistent, fail instead of applying ICP or modifying the mesh.

## Validation Gates

- High and low final object matrices: exact within `1e-5` per matrix component before export baking.
- Center error: at most `1e-5` of the high diagonal after presentation-offset removal.
- Coordinate restoration retains its broad size safety envelope from the generated Blend. The generated-low server policy does not run a direction review or an independent surface-distance gate.
- Determinant sign: identical; reflections are forbidden.
- Blend topology fingerprint: exactly unchanged after the no-UV policy is applied.
- FBX files are exported and hashed but are not freshly reimported by the automatic-retopology stage.
- Low inspection appearance: opaque yellow/orange, no X-ray, visible alongside the high.

## Front-End Integration

The uploader or web front end must not recenter the returned low or overwrite the object matrix, location, rotation, scale, axis conversion, or unit scale after the worker succeeds. Use the worker's aligned Blend/FBX files as the bake inputs.

If the downstream baker independently normalizes imports, disable that option or apply the same deterministic transform to both `bake_high.fbx` and `bake_low.fbx`.
