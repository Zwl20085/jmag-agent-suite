# Labels JSON schema

The labels file is the contract between the geometry script and the agent: everything the
simulation needs that is not in the DXF/STEP itself. One file per geometry file, written next
to it by the same script run.

Contents: 1 Core fields (verified) - 2 Optional fields (recommended) - 3 Example - 4 Validation

## 1. Core fields (used by `examples/spm_motor/run_mcp.py`, verified on JMAG 23.0)

| Field | Type | Meaning | Consumed by |
|---|---|---|---|
| `dxf` | string | absolute path of the geometry file | `jmag_import_dxf(path=...)` |
| `parameters` | object | every dimension/parameter used (mm, deg); must include `stack_mm` for 2D | `jmag_set_properties(kind='study', values={'ModelThickness': stack_mm})` |
| `parts` | list of `{"name", "point": [x, y]}` | one point strictly inside every closed face (3D: `[x, y, z]`) | `jmag_rename_parts(renames=[{"part": point, "name": name}])` |
| `magnets` | list of `{"name", "center_deg", "polarity": "outward" \| "inward"}` | radial magnets and their polarity | `jmag_set_material(orientation={"pattern": "Radial", "outward": polarity == "outward"})` |
| `coils` | `{phase: {"up": [part...], "down": [part...]}}` | go (+z) and return (-z) coil sides per phase | `jmag_add_fem_coil(sides=[{"parts": up, "direction": "up"}, {"parts": down, "direction": "down"}])` |
| `rotor_parts` | list of part names | everything that rotates (core, magnets, pockets, shaft region) | RotationMotion and Torque conditions |

## 2. Optional fields (recommended; consumed by the agent, not by a verified script yet)

| Field | Type | Meaning |
|---|---|---|
| `units` | `"mm"` | model length unit the DXF numbers are in |
| `suppress` | list of part names | faces to suppress per study (the air gap `GapAir`) |
| `air_parts` | list of part names | faces that stay as `Air` (pockets, barriers, shaft bore) |
| `magnets[].pattern` | `"Radial"` \| `"Parallel"` | orientation pattern; `Parallel` needs `direction` |
| `magnets[].direction` | `[x, y, z]` | magnetisation direction for `Parallel` (IPM, flat bars) |
| `conductors` | `{group: [part...]}` | solid conductors (cage bars, hairpins) for `jmag_add_fem_conductor` |
| `winding` | `{"slots", "poles", "phases", "layers", "coil_pitch", "turns", "parallel_paths"}` | the arguments of `jmag_winding_plan`; its `labels_coils` output has exactly the `coils` shape above and goes to `jmag_winding(coils=...)` |
| `materials` | `{part_or_group: material_name}` | intended material per part group |
| `expected` | `{"part_count": n, "areas": {name: mm2}}` | for the import self-check against `jmag_import_dxf` / `jmag_list_parts` |
| `slide_radius_mm` | number | mid-gap radius; no user part may cross it |
| `sector` | `{"angle_deg", "type": "periodic" \| "anti-periodic"}` | only for sector models |
| `analytic` | object | the script's own hand numbers (gap area, core area, ideal L ...) |

Keep names stable across parameter variants so a sweep can reuse the same study setup.

## 3. Example (12s/4p SPM, abbreviated)

```json
{"dxf": "C:/work/spm_12s4p.dxf",
 "parameters": {"poles": 4, "slots": 12, "r_rotor": 27.0, "magnet_thickness": 3.0,
                "magnet_arc_deg": 80.0, "airgap": 1.0, "stack_mm": 50.0},
 "parts": [{"name": "StatorCore", "point": [52.5, 0.0]}, {"name": "RotorCore", "point": [0.0, 0.0]},
           {"name": "Magnet1", "point": [28.5, 0.0]}, {"name": "Slot01_U+", "point": [38.0, 0.0]}],
 "magnets": [{"name": "Magnet1", "center_deg": 0, "polarity": "outward"}],
 "coils": {"U": {"up": ["Slot01_U+", "Slot07_U+"], "down": ["Slot04_U-", "Slot10_U-"]}},
 "rotor_parts": ["RotorCore", "Magnet1", "Magnet2", "Magnet3", "Magnet4"],
 "suppress": ["GapAir"]}
```

Note: the SPM example does not label the air gap; the agent names the one leftover
`0/Region.N` part `GapAir` after renaming everything else. New scripts should label it
directly with a point at mid-gap between two magnets.

## 4. Validation the agent does after import

1. Count of parts from `jmag_import_dxf` == `len(parts)` (+ unlabelled air gap if not listed).
2. `jmag_rename_parts` returns no unresolved points; `jmag_list_parts` shows no name starting
   with the layer prefix (`0/`).
3. Areas within ~0.5 % of `expected.areas` (arcs are exact; polylines lose a little).
4. Every part in `coils`, `magnets`, `rotor_parts`, `conductors` exists after renaming.
