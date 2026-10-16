# Slot / pole combinations (3-phase)

Contents: 1 How the columns are computed - 2 Fractional-slot concentrated (y = 1) -
3 Distributed / integer-slot - 4 Reading the table - 5 Induction-machine slot rules

## 1. How the columns are computed

Values computed with the star-of-slots method (double layer, 60-degree phase spread) for this
skill; cross-check any design with `jmag_winding_plan`.

- `q = Q / (2p * 3)` slots per pole per phase.
- `y` coil pitch in slots (1 = tooth coil).
- `kw1` fundamental winding factor (double layer).
- `LCM(Q, 2p)` = number of cogging periods per mechanical revolution; cogging period =
  `360 / LCM` mechanical degrees. Larger LCM -> usually lower cogging amplitude.
- `gcd(Q, 2p)` = rotational symmetry of the force pattern. **1 -> unbalanced magnetic pull
  (UMP)**: the net radial force on the rotor is not zero.
- Smallest sector: `t = gcd(Q, p)`; if `Q/t` is even -> `360/(2t)` anti-periodic, else `360/t`
  periodic. 360 = full model only.
- Balanced 3-phase winding requires `Q / (3t)` to be an integer.

## 2. Fractional-slot concentrated windings (y = 1, double layer)

| Q | 2p | q | y | kw1 | LCM | cogging period [deg mech] | gcd(Q,2p) | UMP | smallest sector [deg] |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 2 | 0.5 | 1 | 0.866 | 6 | 60 | 1 | YES | 360 periodic |
| 3 | 4 | 0.25 | 1 | 0.866 | 12 | 30 | 1 | YES | 360 periodic |
| 6 | 4 | 0.5 | 1 | 0.866 | 12 | 30 | 2 | no | 180 periodic |
| 6 | 8 | 0.25 | 1 | 0.866 | 24 | 15 | 2 | no | 180 periodic |
| 9 | 6 | 0.5 | 1 | 0.866 | 18 | 20 | 3 | no | 120 periodic |
| 9 | 8 | 0.375 | 1 | 0.945 | 72 | 5 | 1 | YES | 360 periodic |
| 9 | 10 | 0.3 | 1 | 0.945 | 90 | 4 | 1 | YES | 360 periodic |
| 9 | 12 | 0.25 | 1 | 0.866 | 36 | 10 | 3 | no | 120 periodic |
| 12 | 8 | 0.5 | 1 | 0.866 | 24 | 15 | 4 | no | 90 periodic |
| 12 | 10 | 0.4 | 1 | 0.933 | 60 | 6 | 2 | no | 180 anti-periodic |
| 12 | 14 | 0.286 | 1 | 0.933 | 84 | 4.29 | 2 | no | 180 anti-periodic |
| 15 | 10 | 0.5 | 1 | 0.866 | 30 | 12 | 5 | no | 72 periodic |
| 15 | 14 | 0.357 | 1 | 0.951 | 210 | 1.71 | 1 | YES | 360 periodic |
| 15 | 16 | 0.312 | 1 | 0.951 | 240 | 1.5 | 1 | YES | 360 periodic |
| 18 | 12 | 0.5 | 1 | 0.866 | 36 | 10 | 6 | no | 60 periodic |
| 18 | 14 | 0.429 | 1 | 0.902 | 126 | 2.86 | 2 | no | 180 anti-periodic |
| 18 | 16 | 0.375 | 1 | 0.945 | 144 | 2.5 | 2 | no | 180 periodic |
| 18 | 20 | 0.3 | 1 | 0.945 | 180 | 2 | 2 | no | 180 periodic |
| 21 | 20 | 0.35 | 1 | 0.953 | 420 | 0.857 | 1 | YES | 360 periodic |
| 24 | 16 | 0.5 | 1 | 0.866 | 48 | 7.5 | 8 | no | 45 periodic |
| 24 | 20 | 0.4 | 1 | 0.933 | 120 | 3 | 4 | no | 90 anti-periodic |
| 24 | 22 | 0.364 | 1 | 0.949 | 264 | 1.36 | 2 | no | 180 anti-periodic |
| 24 | 26 | 0.308 | 1 | 0.949 | 312 | 1.15 | 2 | no | 180 anti-periodic |
| 27 | 24 | 0.375 | 1 | 0.945 | 216 | 1.67 | 3 | no | 120 periodic |
| 36 | 30 | 0.4 | 1 | 0.933 | 180 | 2 | 6 | no | 60 anti-periodic |
| 36 | 32 | 0.375 | 1 | 0.945 | 288 | 1.25 | 4 | no | 90 periodic |
| 36 | 34 | 0.353 | 1 | 0.953 | 612 | 0.588 | 2 | no | 180 anti-periodic |
| 48 | 40 | 0.4 | 1 | 0.933 | 240 | 1.5 | 8 | no | 45 anti-periodic |
| 48 | 44 | 0.364 | 1 | 0.949 | 528 | 0.682 | 4 | no | 90 anti-periodic |

## 3. Distributed / integer-slot windings (double layer unless noted)

| Q | 2p | q | y | kw1 | LCM | cogging period [deg mech] | gcd(Q,2p) | UMP | smallest sector [deg] |
|---|---|---|---|---|---|---|---|---|---|
| 12 | 4 | 1 | 3 | 1.000 | 12 | 30 | 4 | no | 90 anti-periodic |
| 24 | 4 | 2 | 6 | 0.966 | 24 | 15 | 4 | no | 90 anti-periodic |
| 24 | 4 | 2 | 5 | 0.933 | 24 | 15 | 4 | no | 90 anti-periodic |
| 36 | 4 | 3 | 9 | 0.960 | 36 | 10 | 4 | no | 90 anti-periodic |
| 36 | 4 | 3 | 8 | 0.945 | 36 | 10 | 4 | no | 90 anti-periodic |
| 36 | 4 | 3 | 7 | 0.902 | 36 | 10 | 4 | no | 90 anti-periodic |
| 18 | 4 | 1.5 | 4 | 0.945 | 36 | 10 | 2 | no | 180 periodic |
| 27 | 6 | 1.5 | 4 | 0.945 | 54 | 6.67 | 3 | no | 120 periodic |
| 36 | 6 | 2 | 6 | 0.966 | 36 | 10 | 6 | no | 60 anti-periodic |
| 36 | 6 | 2 | 5 | 0.933 | 36 | 10 | 6 | no | 60 anti-periodic |
| 54 | 6 | 3 | 9 | 0.960 | 54 | 6.67 | 6 | no | 60 anti-periodic |
| 54 | 6 | 3 | 8 | 0.945 | 54 | 6.67 | 6 | no | 60 anti-periodic |
| 24 | 8 | 1 | 3 | 1.000 | 24 | 15 | 8 | no | 45 anti-periodic |
| 36 | 8 | 1.5 | 4 | 0.945 | 72 | 5 | 4 | no | 90 periodic |
| 48 | 8 | 2 | 6 | 0.966 | 48 | 7.5 | 8 | no | 45 anti-periodic |
| 48 | 8 | 2 | 5 | 0.933 | 48 | 7.5 | 8 | no | 45 anti-periodic |
| 72 | 8 | 3 | 9 | 0.960 | 72 | 5 | 8 | no | 45 anti-periodic |
| 72 | 8 | 3 | 7 | 0.902 | 72 | 5 | 8 | no | 45 anti-periodic |
| 36 | 12 | 1 | 3 | 1.000 | 36 | 10 | 12 | no | 30 anti-periodic |
| 72 | 12 | 2 | 6 | 0.966 | 72 | 5 | 12 | no | 30 anti-periodic |
| 72 | 12 | 2 | 5 | 0.933 | 72 | 5 | 12 | no | 30 anti-periodic |

The repo's example motor (`examples/spm_motor`) is 12s/4p **single-layer** full pitch (q = 1,
kw1 = 1): cogging period 30 deg mech = 6th electrical harmonic, which is why its torque ripple
is dominated by the 6th harmonic (~3 Nm at 10 A, verified run).

## 4. Reading the table

- **Winding factor**: kw1 >= 0.93 is good; q = 0.5 combos (0.866) trade torque for simple
  manufacture and low cogging.
- **Cogging**: the cogging fundamental is `LCM(Q, 2p)` per revolution, i.e. `LCM / p`
  per electrical period (12s4p: 12/2 = 6th electrical). Give one cogging period >= 20-30
  samples in a cogging run (the `jmag-experiments` skill covers the test).
- **UMP**: `gcd(Q, 2p) = 1` (odd Q with no common factor) puts a rotating net radial force on the
  rotor; acceptable only with stiff bearings and an explicit comment. Other odd-Q combos (15s10p,
  9s6p, 27s24p) are balanced because `gcd >= 2`: check the column, not the parity of Q.
- **Sector models** reduce runtime by the symmetry factor but need the periodic boundary to be
  right (`jmag-geometry`, symmetry section; unverified on 23.0).
- **Parasitic MMF harmonics**: fractional-slot windings with q < 0.5 have strong sub- and
  super-harmonics -> rotor/magnet eddy losses; look at magnet loss before choosing.

## 5. Induction-machine slot rules (Q_s stator, Q_r rotor)

Avoid `Q_r = Q_s` (locking) and `Q_s - Q_r = +/- 2p` (synchronous cusps at start-up); avoid
`Q_s - Q_r = +/- 1, +/- 2, +/- (p +/- 1), +/- (p +/- 2)` (vibration and noise). Confirm with a
textbook table for the final choice. Common 4-pole choices: 36/28, 36/44, 48/40. Skew of about one stator slot pitch reduces slot harmonics (needs multi-slice
or 3D in FEA).
