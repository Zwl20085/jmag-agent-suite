# Parametric geometry templates

Contents
1. Common building blocks
2. SPM (surface magnets) - verified reference
3. IPM: V-shape and flat-bar, bridges, ribs and air pockets
4. Induction machine: cage bars and the end-ring note
5. SynRM: flux barriers
6. EI / C-core inductor
7. Solenoid plunger (axisymmetric)
8. Checks every template script should print

Status: only the SPM template (`examples/spm_motor/make_geometry.py`) and the C-core
(`jmag_bridge.dxf.c_core_inductor`, used by `jmag_self_test`) are verified end-to-end on JMAG
23.0. The others are standard machine geometry written for the same import rules; prove each
new script with the area/count self-check and a `jmag_model_image` before simulating.

All dimensions in mm, angles in degrees, origin = machine axis, CCW positive.

---

## 1. Common building blocks

```python
import math
from jmag_bridge.dxf import Dxf

def pol(r, deg):
    return (r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)))

def rect(cx, cy, length, width, rot_deg):
    """Corners of a rectangle centred at (cx, cy), long side `length` along rot_deg."""
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    pts = [(-length / 2, -width / 2), (length / 2, -width / 2), (length / 2, width / 2), (-length / 2, width / 2)]
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]
```

- A closed region = `d.polyline(pts, closed=True)`, `d.circle`, or arcs + lines that meet
  exactly. Neighbouring parts share edges with identical coordinates.
- Record a label point for every closed face while you draw it (`parts.append(...)`), not
  afterwards; that is the only reliable way to keep labels and geometry consistent.

## 2. SPM (surface magnets)

`examples/spm_motor/make_geometry.py`. Parameters: `poles, slots, r_rotor, magnet_thickness,
magnet_arc_deg, airgap, r_slot_bottom, r_stator_out, slot_angle_deg, stack_mm`.

- Stator: outer circle + one closed bore/slot boundary; each slot closed at the bore radius so
  the coil region is its own part.
- Magnets: `annular_sector(r_rotor, r_rotor + hm, c - arc/2, c + arc/2)` per pole; the gaps
  between magnets and the air gap form one air part -> label `GapAir`, suppress it.
- Magnet orientation: `{"pattern": "Radial", "outward": true}` alternating per pole (verified).
- Variants: magnet arc ratio, bread-loaf magnets (outer arc with smaller radius, centre offset),
  axial segmentation (3D/loss only), parallel magnetisation (`Parallel` + pole-axis direction).

## 3. IPM: V-shape and flat-bar

Parameters (V): `poles, r_ro (rotor outer), r_shaft, hm (magnet thickness), lm (magnet length),
v_angle (included angle between the two magnets of a pole, typ. 100-150 deg), d_mag (depth of
the V apex below the surface), b_bridge (outer bridge, typ. 0.5-1.5 mm), w_rib (centre post
between the two pockets, typ. 0.5-2 mm), pocket_ext (air extension at each magnet end)`.
Flat-bar (I) is the special case of one magnet per pole perpendicular to the pole axis.

Construction per pole (pole axis at `c = k * 360 / poles`):
1. Two magnet rectangles from `rect(...)`, mirrored about the pole axis, long sides at
   `c +/- (90 - v_angle/2)` degrees, placed so the inner ends leave `w_rib` between them.
2. One air pocket per magnet end as its own closed polygon sharing the magnet's short edge.
   The outer pocket corner keeps `b_bridge` to the rotor circle `r_ro` (measure the minimum
   distance in the script and assert it).
3. Rotor core = circle `r_ro` (+ shaft circle). The magnets and pockets are closed loops inside
   it, so the core part is automatically "core minus holes".

Rules that matter (they cost runs when ignored):
- **Pockets become parts.** Every pocket, barrier and the shaft bore is its own region after
  import. Label each (`Pocket<k>`), assign `Air` and include it in the rotor's RotationMotion
  parts, the same way a shaft-bore region is handled (verified behaviour for the bore:
  "name it and move it with the rotor instead of suppressing it").
- **The rotor surface stays a complete circle.** A pocket that opens into the air gap merges
  with the gap region, gives the slide-mesh band an irregular boundary and creates a user part
  bridging rotor and stator. Keep `b_bridge > 0`.
- **Bridges and ribs are meshed explicitly.** `jmag_mesh_size` on the rotor core near the
  bridges (or draw the bridge as its own part and size it), >= 2-3 elements across. Expect
  bridges to saturate (> 2 T in the contour) - that is their purpose, not an error.
- **Magnet direction.** Parallel magnetisation normal to the magnet's long side, pointing
  outward on N poles and inward on S poles:
  `orientation={"pattern": "Parallel", "direction": [cos(a), sin(a), 0]}` with `a` the normal
  direction of that magnet (IPM recipe is not yet verified end-to-end; verify with a B contour
  that each pole's two magnets push flux the same way).
- Labels: put the magnet's `direction_deg` and polarity into the labels JSON so the agent never
  recomputes them.

## 4. Induction machine (cage rotor)

Parameters: `Qs, Qr (rotor bars), poles, r_ro, airgap, bar shape (round / drop / closed slot
with bridge h_bridge), bar depth, r_shaft`.

- Bars are closed loops inside the rotor core: `Bar<k>`, material aluminium or copper with eddy
  currents on, excited through `jmag_add_fem_conductor` (one FEMConductor per bar or per group).
- Closed rotor slots: draw the thin bridge above each bar like an IPM bridge (mesh it).
- Avoid `Qr = Qs` and `Qr = Qs +/- 2p` (cusps / locking torque); 2D cannot skew - use a
  multi-slice or 3D model when skew matters (not covered by the tools).
- **End ring note.** A 2D cross-section has no end rings; their resistance and inductance enter
  through the circuit (ring segments between adjacent bar conductors). Equivalent per-bar ring
  resistance for referring to a bar: `R_ring_eq = R_seg / (2 sin^2(pi p / Qr))`. JMAG's cage
  settings ("Automate Coil Windings/Cage Settings" in the manual) may build this for you -
  **unverified on 23.0**; until then build the ring with `jmag_circuit_add` and check it with
  `jmag_circuit_image`.

## 5. SynRM (flux barriers)

Parameters: `poles, r_ro, n_layers (typ. 2-4), barrier shape (circular arcs centred on a point
outside the rotor, or U-shapes of lines + arcs), barrier widths, insulation ratio
(sum of barrier widths / iron path along the q-axis, typ. 0.3-0.5), tangential rib width at
the surface, optional radial (centre) ribs`.

- Each barrier is a closed region: `Barrier<pole>_<layer>`, `Air`, moves with the rotor
  (same handling as IPM pockets). PM-assisted SynRM: magnets inside barriers as separate parts.
- Axis convention for SynRM work: d-axis = high-permeance axis through the iron segments,
  q-axis through the barriers (opposite to the PM-machine convention; say which one you use).
- Tangential ribs follow the IPM bridge rules (surface stays a full circle, explicit mesh).

## 6. EI / C-core inductor

- C-core: `jmag_bridge.dxf.c_core_inductor(path)` - 15 mm legs, 1 mm gap, coil sides inside
  and outside the window, returns label points and analytic data. Verified by `jmag_self_test`
  (11.0 mH FEA vs 9.42 mH ideal gap formula, +17 % 2D fringing).
- EI core: E (three legs) and I as separate closed loops; centre-leg gap (and optional outer-leg
  gaps) as the space between E and I. Coil go/return sides in both windows, sharing edges with
  nothing (insulation clearance), labelled `CoilA+` / `CoilA-`.
- Ungapped outer legs: E and I touch along a shared edge; draw each core as its own closed loop
  with identical edge coordinates, accept two parts, and give both the same steel.
- Analytic check: `L = N^2 mu0 A_gap / g_total` (gap-dominated), 2D fringing raises it 10-20 %.

## 7. Solenoid plunger (axisymmetric)

- Draw the half cross-section in the r-z plane: x = r >= 0, y = z. Parts: `Shell` (yoke),
  `Plunger` (moving), `Stop` (pole piece), `Coil`, `Sleeve` (non-magnetic, optional), and the
  working gap air between plunger and stop (a closed face -> label and suppress or give `Air`).
- Stroke is a geometry parameter: regenerate the DXF per position (robust) rather than moving
  the plunger with a motion condition (translation motion has no verified recipe).
- Which axis JMAG's axisymmetric studies (`StaticAX` etc., listed by `jmag_create_study`) use as the
  symmetry axis is **unverified on 23.0**: check with `jmag_docs_search("axisymmetric")` and
  confirm by comparing a coil's flux linkage with `N^2 mu0 A / l` for an air-cored case.

## 8. Checks every template script should print

- Number of closed faces it expects JMAG to create, and each part's expected area.
- Minimum bridge/rib thickness and minimum distance of any rotor part to the gap mid-radius.
- That every label point lies inside exactly one face (point-in-polygon on the drawn loops).
- Total magnet area per pole and slot area (feeds `jmag-machine-design` slot-fill checks).
