# PBR UV seam and QA standard

## Contents

- Seam hierarchy
- Shape patterns
- Straightening rules
- Sharing rules
- Packing and density
- Checker-error diagnosis
- Acceptance thresholds

## Seam hierarchy

1. **Real hard edge:** always split UV. Otherwise normal-map baking and edge filtering can produce dark lines.
2. **Soft edge:** keep continuous unless a topological opening or severe distortion requires a cut.
3. **Visibility:** prefer back, underside, interior, base, contact patch, or permanently occluded regions.
4. **Topology:** an annular surface needs a path connecting its boundary loops; a closed surface needs at least one opening path.
5. **Count:** optimize visible seam length, not raw island count. Hard-surface assets legitimately contain many hard-edge islands.

Do not confuse a green seam overlay with an actual UV split. After projection, the two sides may have identical UV coordinates. Separate seam-defined islands before packing and verify that every hard edge is a real UV boundary.

## Shape patterns

### Box and planar hard surface

- Cut at genuine 90° or shading-hard corners.
- Preserve a large planar panel instead of fragmenting every polygon.
- Project each planar island exactly.
- If texture direction matters, align the dominant edge horizontally or vertically.

### Cylinder, tube, post, limb

- Split cap rings when they are hard edges.
- Add one longitudinal seam on the hidden side.
- Prefer a path aligned with the cylinder axis; reject diagonal or spiraling paths.
- Preserve the side as one rectangular strip when possible.

### Bent strip or cable-like hard surface

- Use one continuous strip while curvature remains soft.
- Straighten only a clean quad grid. Mixed triangles often need conformal unwrap instead.
- Inspect the checker across bends; squares may rotate with the bend but must not shear abruptly.

### Sphere or rounded organic part

- Put one main seam on the back.
- Accept mild distributed stretch instead of many visible cuts.
- Isolate poles or singular tips only when they cause concentrated distortion.

### Thin panel, tabletop, shelf, crate, machine housing

- Separate the large visible face from thickness walls at real hard edges.
- Planar-project the face and straighten the wall strip.
- Chevron or herringbone checker patterns across a 90° corner indicate a missing hard-edge split.

## Straightening rules

- Planar islands: exact planar projection is preferred over iterative relaxation.
- Mechanical strips: align a true construction edge, not an arbitrary UV bounding-box diagonal.
- Curved organic islands: do not force cardinal straightening if it adds high shear.
- Rotate only whole islands using rigid transforms after unwrapping.
- When a seam flag exists but its two sides share coordinates, translate the seam-defined islands apart before packing.

## Sharing rules

Share UVs only when parts have:

- identical topology;
- matching edge lengths within tolerance;
- matching material assignments;
- an orientation-preserving rigid mapping;
- no unique baked detail requirement.

Treat mirrored geometry as unique by default because tangent-space normals and asymmetric wear may differ. Record intentional overlaps in the QA report.

## Packing and density

- Default tile: 0–1; use UDIM only when requested.
- Normalize island scale by 3D-area-to-UV-area density.
- Default 2K exact padding: 8–16 px. Convert to normalized space as `padding_px / resolution`; Blender pack margins may represent half the pairwise gap, so measure the exported result.
- Aim for useful occupancy, but never trade required padding or correctness for occupancy.
- Keep priority surfaces larger only when the user explicitly asks for unequal density.

## Checker-error diagnosis

| Symptom | Likely cause | Fix |
|---|---|---|
| Chevron pattern around a box corner | hard edge not split | split the corner and reproject planes |
| Spiral/diagonal pattern down a tube | diagonal longitudinal seam or rotated strip | cut along the hidden axis and straighten |
| Checker changes scale between adjacent parts | inconsistent texel density | average island scale and repack |
| Black or missing checker region | flipped normal, flipped UV, degenerate UV, wrong material, or out-of-tile UV | test each cause separately |
| Large red stretch on a flat face | nonplanar unwrap used on planar geometry | exact planar projection |
| Many tiny islands on a rounded surface | angle threshold too aggressive | raise the threshold and keep soft curvature continuous |
| Hard seam visible in Blender but QA says no split | both sides have identical UV coordinates | separate seam-defined islands before packing |
| FBX imports with no visible geometry | export selection/visibility or scale problem, not necessarily UV | read the FBX back into a clean scene and inspect objects, scale, dimensions, and UV layers |

## Acceptance thresholds

Hard failures:

- out-of-tile loops: 0;
- flipped faces: 0;
- degenerate UV faces: 0;
- hard edges without UV boundary: 0;
- accidental overlap: 0;
- missing active UV layers: 0.

Quality guidance:

- normalized texel-density p10/p90: approximately 1.0;
- stretch p90: target ≤ 1.2;
- stretch p95: target ≤ 1.5;
- local stretch maxima: inspect visually; accept on hidden complex soft surfaces when an extra seam is worse;
- exact 2K padding: 8–16 px;
- straightable island axis error: near 0°;
- nonstructural soft seams: keep few and mostly hidden.
