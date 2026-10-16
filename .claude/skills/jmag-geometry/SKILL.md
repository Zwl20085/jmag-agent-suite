---
name: jmag-geometry
description: Writes scripts that draw electrical-machine and magnetic-device geometry outside JMAG (DXF for 2D, STEP for 3D) plus a labels JSON of inside-points, so JMAG imports it with predictable, nameable parts. Covers parametric templates (SPM, IPM V / flat-bar with bridges and pockets, induction cage, SynRM barriers, EI/C inductor, solenoid plunger), symmetry sectors and the import rules. Use whenever the user wants to create or parametrise a motor, generator, inductor, transformer or actuator cross-section or 3D model for JMAG, convert CAD for JMAG, or asks why imported parts are missing, merged or unnamed - even if they only say "draw the stator" or "make the geometry".
---

# Geometry for JMAG, drawn outside JMAG

Drawing in JMAG's Geometry Editor through its API is slow, version-specific and hard to review.
A small Python script that writes DXF/STEP is reviewable, parametric and testable, and the bridge
imports it in one call. The script is the single source of truth for dimensions.

## What the script must produce

1. **The geometry file**
   - 2D: DXF with LINE / ARC / CIRCLE entities. Use the bundled zero-dependency writer
     `jmag_bridge.dxf.Dxf`: `polyline(points, closed=True)`, `arc(center, r, a0, a1)` (CCW, deg),
     `line`, `circle`, `annular_sector(r_in, r_out, a0, a1)`.
   - 3D: STEP via CadQuery/build123d/gmsh-OCC/FreeCAD; name each solid so JMAG part names are
     readable.
2. **A labels JSON** with one point strictly inside every part, plus everything the simulation
   needs later (winding layout, magnet polarity/direction, rotor part list, air parts to
   suppress, key dimensions). Schema: `references/labels-schema.md`. The agent then calls
   `jmag_rename_parts(renames=[{"part": point, "name": name}, ...])`.

Reference implementation: `examples/spm_motor/make_geometry.py` (12-slot / 4-pole SPM, verified
end-to-end on JMAG 23.0). Templates for other devices: `references/templates.md`.

## Rules that make JMAG import cleanly

- **Every part is a closed loop.** JMAG turns each closed face into a part; an open outline
  silently produces no part (or merges two).
- **Draw shared edges exactly** (same coordinates): a coil fills its slot because slot and coil
  outlines coincide. Tiny gaps or overlaps create sliver parts and bad meshes.
- **Every enclosed face becomes a part - including air.** Closed rotor and stator outlines
  enclose the air gap, so it becomes a part; IPM pockets, flux barriers, slot openings and
  shaft bores do too. Label every one of them. The air gap is suppressed
  (`jmag_suppress_parts`); pockets/barriers inside the rotor stay as rotor parts with `Air`
  (or are suppressed) and must move with the rotor.
- **Nothing user-drawn may bridge rotor and stator** in a rotating machine: the slide mesh lives
  in the gap. Keep at least ~1/3 of the air gap (and >= 2 element layers) free on each side of
  the slide radius; do not let magnets, pockets or tooth tips cross the gap mid-radius.
- **Inside points, not centroids.** The centroid of a C-core, an L-shaped magnet, a V-pocket or
  a yoke lies outside the part. Use mid-radius / mid-angle points, checked in the script.
- **Units:** JMAG reads DXF numbers in the model unit (mm by default). Keep the script in mm.
- **Keep radii exact.** Use ARC entities next to the air gap, not polylines, so the slide mesh
  and torque integration see a true circle.
- **Thin features are real geometry.** IPM bridges (typically 0.5-1.5 mm) and ribs must be
  drawn as their own thin regions of the rotor core outline, then meshed with local sizes
  (`jmag_mesh_size`, >= 2-3 elements across).
- **Self-check in the script:** compute expected part areas (sector:
  `(r2^2-r1^2)*pi*span/360`) and the expected part count; compare with what `jmag_import_dxf`
  returns. Area or count mismatch = broken loop.

## Symmetry sectors

Draw the full machine unless runtime matters. A sector model needs a periodic boundary and
correct result scaling, which is more to get wrong:

- Smallest sector: let `t = gcd(Q, p)` (Q slots, p pole pairs). If `Q/t` is even, a sector of
  `360/(2t)` degrees with an **anti-periodic** boundary works; otherwise `360/t` degrees with a
  **periodic** boundary. `gcd(Q, 2p) = 1` (e.g. 9s8p) means only the full model is valid.
  `jmag_winding_plan` reports the periodicity; table in `jmag-machine-design`.
- The sector edges must be radial lines through the origin and cut every part at the same
  angle on both sides (the two edges are images of each other).
- JMAG side: condition `RotationPeriodicBoundary` (via `jmag_add_condition`) and study
  properties `ConversionFactor` / `FullModelConversion` exist under those names on 23.0
  (the properties reference of `jmag-simulation`). The boundary's property values, the
  anti-periodic flag and whether results are auto-scaled to the full machine are
  **unverified on 23.0**: list them with `jmag_get_properties(kind='condition', with_help=true)`
  and prove the sector by comparing torque/flux linkage against a full-model run once.

## Typical part naming

`StatorCore`, `RotorCore`, `Shaft`, `Magnet<k>`, `Slot<kk>_<phase><+|->` (sign = current
direction; double layer adds the layer: `Slot01a_U+`, `Slot01b_W-`), `Bar<k>` (cage),
`Pocket<k>` / `Barrier<k>` (rotor air), `GapAir`. These are the names `jmag_winding_plan`
suggests in its slot map, so its `labels_coils` output plugs straight into `jmag_winding` /
`jmag_add_fem_coil`. A double-layer slot must be drawn as two parts (one per layer).

## Workflow

1. Write/modify the script, run it, plot it (matplotlib) or open the DXF if unsure.
2. `jmag_new_project` -> `jmag_import_dxf` -> compare part count and areas with the script ->
   `jmag_rename_parts` from the labels JSON -> `jmag_list_parts` (nothing left as
   `<layer>/Region.N`) -> `jmag_model_image` for a visual check.
3. Suppress the parts the labels mark as `suppress` (air gap) in each study.
4. Parametric changes: edit parameters, regenerate, import into a new model or project (keep the
   old one for comparison). Geometry variables in sweeps: `jmag-parametric-optimisation`.
5. 3D: `jmag_import_cad(path, unit="mm")` -> compare volumes with the CAD -> rename by points or
   ids. 3D coil and face conditions are covered in `jmag-experiments` (actuator reference).
