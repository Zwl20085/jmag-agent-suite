---
name: jmag-geometry
description: Write scripts that draw electrical-machine and magnetic-device geometry outside JMAG (DXF for 2D, STEP for 3D) plus a JSON of labelled inside-points, so JMAG imports it with predictable, nameable parts. Use this whenever the user wants to create or parametrise a motor, generator, inductor, transformer or actuator cross-section/3D model for JMAG, convert CAD for JMAG, or asks why imported JMAG parts are missing, merged or unnamed - even if they only say "draw the stator" or "make the geometry".
---

# Geometry for JMAG, drawn outside JMAG

Drawing in JMAG's Geometry Editor through its API is slow, version-specific and hard to review.
A small Python script that writes DXF/STEP is reviewable, parametric and testable, and the bridge
imports it in one call. The script is the single source of truth for dimensions.

## What the script must produce

1. **The geometry file**
   - 2D: DXF with LINE / ARC / CIRCLE entities. Use `jmag_bridge.dxf.Dxf` (no dependencies), e.g.
     `polyline(points, closed=True)`, `arc(center, r, a0, a1)`, `circle`, `annular_sector`.
   - 3D: STEP via CadQuery/build123d/gmsh-OCC/FreeCAD; give each solid a name so JMAG part names
     are readable.
2. **A labels JSON** with one point strictly inside every part, plus anything the simulation needs
   later (winding layout, magnet polarity, rotor part list, key dimensions):
   ```json
   {"dxf": "...", "parameters": {...},
    "parts": [{"name": "StatorCore", "point": [52.5, 0.0]}, ...],
    "magnets": [{"name": "Magnet1", "center_deg": 0, "polarity": "outward"}],
    "coils": {"U": {"up": ["Slot01_U+"], "down": ["Slot04_U-"]}, ...},
    "rotor_parts": ["RotorCore", "Magnet1", ...]}
   ```
   The agent then calls `jmag_rename_parts(renames=[{"part": point, "name": name}, ...])`.

A complete reference implementation is `examples/spm_motor/make_geometry.py` (12-slot/4-pole SPM).

## Rules that make JMAG import cleanly

- **Every part is a closed loop.** JMAG's `CreateRegions` turns each closed face into a part; an
  open outline silently produces no part (or merges two).
- **Draw shared edges exactly** (same coordinates): a coil fills its slot because the slot outline
  and coil outline coincide. Tiny gaps or overlaps create sliver parts and bad meshes.
- **Every enclosed face becomes a part - including air.** Closed rotor and stator outlines enclose
  the air gap, so it becomes a part (often with inter-magnet pockets). Label it (e.g. `GapAir`) so
  the agent can suppress it; rotating machines must not have a user part bridging rotor and stator.
- **Inside points, not centroids.** The centroid of a C-core, an L-shaped magnet or a stator yoke
  lies outside the part. Put points safely inside (e.g. mid-radius, mid-angle of a sector).
- **Units:** JMAG reads DXF numbers in the model unit (mm by default). Keep the script in mm.
- **Keep radii exact.** Air gap of 0.5-1 mm next to arcs: use ARC entities rather than many short
  lines, so the slide mesh and torque integration see a true circle.
- **Symmetry:** draw the full machine unless you also set periodic boundaries in JMAG; a partial
  model needs `RotationPeriodicBoundary` + `ConversionFactor`, which is more to get wrong.
- **Self-check in the script:** compute expected part areas (sector: `(r2^2-r1^2)*pi*span/360`)
  and compare with the `area` values `jmag_import_dxf` returns. Area mismatch = broken loop.

## Typical part naming

`StatorCore`, `RotorCore`, `Shaft`, `Magnet<k>`, `Slot<kk>_<phase><+|->` (sign = current
direction), `Bar<k>` (cage), `GapAir`. Phase/direction in the name makes FEM-coil setup and later
review trivial.

## Workflow

1. Write/modify the script, run it, look at the DXF (or a quick matplotlib plot) if unsure.
2. `jmag_new_project` -> `jmag_import_dxf` -> compare the returned part count and areas with the
   script's expectations -> `jmag_rename_parts` from the labels JSON -> `jmag_list_parts` to confirm
   nothing is left unnamed -> `jmag_model_image` for a visual check.
3. Parametric changes: edit parameters, regenerate, import into a new model/project (keep the old
   one for comparison).
