"""Analytic hand calculations for electrical machines and magnetic devices.

Pure Python. Purpose: an expected value *before* trusting an FEA number (the "engineering" rung of the
feedback ladder). Every estimate returns

    {"kind", "value", "unit", "formula", "assumptions": [...], "expected_fea_tolerance_pct", "details": {...}}

``value`` is a number, or a dict of named numbers sharing ``unit`` (e.g. tooth and yoke flux density).
``expected_fea_tolerance_pct`` is how far a correct FEA result typically lands from this estimate; a larger
gap means either the model or the estimate's assumptions are off - find out which.

Length inputs are in **mm** (like JMAG's default model unit), areas in mm^2, flux density in T, current in A
(peak unless the name says rms), speed in rpm, ``poles`` = number of poles (2p).
"""
from __future__ import annotations

import inspect
import math
from typing import Any, Callable

MU0 = 4e-7 * math.pi
MM = 1e-3

RESISTIVITY_20C = {"copper": (1.724e-8, 0.00393), "aluminium": (2.82e-8, 0.00403), "aluminum": (2.82e-8, 0.00403)}


def _pos(name: str, v: Any, allow_zero: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {v!r}") from None
    if not math.isfinite(x) or x < 0 or (x == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'>= 0' if allow_zero else '> 0'}, got {v!r}")
    return x


def _poles(poles: Any) -> int:
    p = int(_pos("poles", poles))
    if p != poles or p % 2:
        raise ValueError(f"poles must be an even integer (number of poles 2p), got {poles!r}")
    return p


def _frac(name: str, v: Any) -> float:
    x = _pos(name, v)
    if x > 1:
        raise ValueError(f"{name} is a fraction in (0, 1], got {v!r}")
    return x


def _out(kind: str, value: Any, unit: str, formula: str, assumptions: list[str], tol: float, **details: Any) -> dict:
    return {"kind": kind, "value": value, "unit": unit, "formula": formula, "assumptions": assumptions,
            "expected_fea_tolerance_pct": tol, "details": details}


# --------------------------------------------------------------------------- air gap

def carter_factor(slot_opening_mm: float, slot_pitch_mm: float, airgap_mm: float) -> dict:
    """Carter factor kc = tau_s / (tau_s - gamma*g), gamma = (4/pi)[x atan x - ln sqrt(1+x^2)], x = b0/(2g)."""
    b0, ts, g = _pos("slot_opening_mm", slot_opening_mm, True), _pos("slot_pitch_mm", slot_pitch_mm), _pos("airgap_mm", airgap_mm)
    if b0 >= ts:
        raise ValueError("slot_opening_mm must be smaller than slot_pitch_mm (pitch = pi*D/Q)")
    x = b0 / (2 * g)
    gamma = 4 / math.pi * (x * math.atan(x) - math.log(math.sqrt(1 + x * x)))
    kc = ts / (ts - gamma * g)
    return _out("carter_factor", kc, "-", "kc = tau_s/(tau_s - gamma g), gamma = 4/pi [x atan(x) - ln sqrt(1+x^2)], x = b0/(2g)",
                ["slotting on one side only", "rectangular slot opening, infinitely deep slot"], 3, effective_gap_mm=kc * g)


def airgap_flux_density(Br: float, magnet_mm: float, airgap_mm: float, mu_r: float = 1.05, carter: float = 1.0,
                        leakage: float = 0.95) -> dict:
    """Surface-PM air-gap flux density (flat top): Bg = k_leak * Br * hm / (hm + mu_r * kc * g).

    Br [T] at operating temperature, magnet_mm = radial magnet thickness hm, airgap_mm = mechanical gap g
    (add a retaining sleeve to g), carter = Carter factor (see kind 'carter_factor'), leakage = flux leakage
    factor (0.85-0.98 for SPM).
    Example: Br=1.2, hm=4, g=1 -> ~0.88 T.
    """
    br, hm, g = _pos("Br", Br), _pos("magnet_mm", magnet_mm), _pos("airgap_mm", airgap_mm)
    mr, kc, kl = _pos("mu_r", mu_r), _pos("carter", carter), _frac("leakage", leakage)
    if kc < 1:
        raise ValueError("carter must be >= 1")
    bg = kl * br * hm / (hm + mr * kc * g)
    return _out("airgap_flux_density", bg, "T", "Bg = k_leak Br hm / (hm + mu_r kc g)",
                ["infinitely permeable iron (no saturation)", "flat-top distribution under the magnet",
                 "radial magnetisation, surface magnets", f"leakage factor {kl}"], 10,
                note="FEA |B| in the gap mid-line under a magnet should be within ~10 %; IPM rotors need a "
                     "magnetic-circuit model instead")


# --------------------------------------------------------------------------- winding & constants

def winding_factor(slots: int, poles: int, phases: int = 3, layers: int = 2, coil_pitch: int | None = None,
                   harmonics: int = 13) -> dict:
    """kw1 and harmonic winding factors from the star of slots (see jmag_winding_plan for the full layout)."""
    from . import winding
    p = winding.plan(int(slots), int(poles), int(phases), int(layers), coil_pitch, harmonics=harmonics)
    return _out("winding_factor", p["kw1"], "-", "kw_nu = |sum of signed conductor phasors at nu*pp| / conductors per phase",
                ["star of slots, 2m phase belts", "no skew (multiply by the skew factor if skewed)"], 0,
                winding_factors=p["winding_factors"], q=p["q"], coil_pitch=p["coil_pitch"])


def _flux_linkage(turns_per_phase: float, kw1: float, Bg: float, bore_mm: float, stack_mm: float, poles: int,
                  pole_arc_ratio: float) -> tuple[float, float]:
    n, k, b = _pos("turns_per_phase", turns_per_phase), _frac("kw1", kw1), _pos("Bg", Bg)
    d, l, p = _pos("bore_mm", bore_mm) * MM, _pos("stack_mm", stack_mm) * MM, _poles(poles)
    a = _frac("pole_arc_ratio", pole_arc_ratio)
    b1 = 4 / math.pi * b * math.sin(a * math.pi / 2)
    tau = math.pi * d / p
    phi1 = 2 / math.pi * b1 * tau * l
    return n * k * phi1, phi1


_PM_ASSUMPTIONS = ["series turns per phase, star connection", "fundamental of a flat-top gap field of width "
                   "pole_arc_ratio * pole pitch", "bore_mm = air-gap (stator bore) diameter", "no saturation, no skew"]


def emf_constant(turns_per_phase: float, kw1: float, Bg: float, bore_mm: float, stack_mm: float, poles: int,
                 pole_arc_ratio: float = 1.0) -> dict:
    """Back-EMF constant from the gap field.

    B1 = (4/pi) Bg sin(alpha pi/2); Phi1 = (2/pi) B1 tau_p L; psi_m = N kw1 Phi1 [Wb, peak per phase];
    Ke = pp * psi_m [V s/rad, peak phase EMF per mechanical rad/s]. Also reports E_rms line-line per 1000 rpm.
    Compare with FEA no-load flux-linkage amplitude (Coil Flux-Linkage) or back-EMF fundamental.
    """
    p = _poles(poles)
    psi, phi1 = _flux_linkage(turns_per_phase, kw1, Bg, bore_mm, stack_mm, p, pole_arc_ratio)
    ke = p / 2 * psi
    e_ll_rms_krpm = ke * 1000 * 2 * math.pi / 60 / math.sqrt(2) * math.sqrt(3)
    return _out("emf_constant", ke, "V s/rad (peak phase, per mech rad/s)",
                "psi_m = N kw1 (2/pi)(4/pi) Bg sin(alpha pi/2) tau_p L;  Ke = pp psi_m",
                _PM_ASSUMPTIONS, 10, psi_m_Wb=psi, flux_per_pole_Wb=phi1,
                E_line_rms_per_krpm_V=e_ll_rms_krpm, kt_Nm_per_A_peak=1.5 * ke)


def torque_constant(turns_per_phase: float, kw1: float, Bg: float, bore_mm: float, stack_mm: float, poles: int,
                    pole_arc_ratio: float = 1.0) -> dict:
    """Torque constant Kt = 3/2 pp psi_m [Nm per A peak phase current, id = 0]; also per A rms."""
    p = _poles(poles)
    psi, _ = _flux_linkage(turns_per_phase, kw1, Bg, bore_mm, stack_mm, p, pole_arc_ratio)
    kt = 1.5 * p / 2 * psi
    return _out("torque_constant", kt, "Nm/A (peak)", "Kt = 3/2 pp psi_m (amplitude-invariant dq, id = 0)",
                _PM_ASSUMPTIONS + ["3 phases, sinusoidal currents"], 10, kt_Nm_per_A_rms=kt * math.sqrt(2), psi_m_Wb=psi)


def torque_dq(poles: int, psi_m: float, iq: float, id: float = 0.0, Ld: float | None = None,
              Lq: float | None = None) -> dict:
    """T = 3/2 pp (psi_m iq + (Ld - Lq) id iq), amplitude-invariant dq (peak A, Wb, H).

    For IPM/SynRM pass Ld and Lq; id < 0 with Ld < Lq gives positive reluctance torque.
    """
    pp = _poles(poles) / 2
    psi = _pos("psi_m", psi_m, True)
    iq, id_ = float(iq), float(id)
    rel = 0.0
    if (Ld is None) != (Lq is None):
        raise ValueError("pass both Ld and Lq (H) or neither")
    if Ld is not None:
        rel = (_pos("Ld", Ld) - _pos("Lq", Lq)) * id_ * iq
    t = 1.5 * pp * (psi * iq + rel)
    return _out("torque_dq", t, "Nm", "T = 3/2 pp (psi_m iq + (Ld - Lq) id iq)",
                ["amplitude-invariant Park transform (peak values)", "constant Ld, Lq, psi_m (no saturation/cross-coupling)",
                 "3 phases"], 5, magnet_torque_Nm=1.5 * pp * psi * iq, reluctance_torque_Nm=1.5 * pp * rel)


def magnetizing_inductance(turns_per_phase: float, kw1: float, bore_mm: float, stack_mm: float, poles: int,
                           g_eff_mm: float, phases: int = 3) -> dict:
    """Synchronous magnetising inductance Lm = (m/pi) mu0 (N kw1)^2 D L / (pp^2 g_eff).

    g_eff_mm: effective magnetic gap = kc*g (+ hm/mu_r for surface magnets, which act as air).
    """
    n, k = _pos("turns_per_phase", turns_per_phase), _frac("kw1", kw1)
    d, l, g = _pos("bore_mm", bore_mm) * MM, _pos("stack_mm", stack_mm) * MM, _pos("g_eff_mm", g_eff_mm) * MM
    pp, m = _poles(poles) / 2, int(_pos("phases", phases))
    lm = m / math.pi * MU0 * (n * k) ** 2 * d * l / (pp ** 2 * g)
    return _out("magnetizing_inductance", lm, "H", "Lm = (m/pi) mu0 (N kw1)^2 D L / (pp^2 g_eff)",
                ["infinitely permeable iron", "uniform effective gap", "fundamental only (no slot/end leakage)"], 15)


def dq_inductance(turns_per_phase: float, kw1: float, bore_mm: float, stack_mm: float, poles: int, g_eff_mm: float,
                  saliency: float = 1.0, leakage_fraction: float = 0.1, phases: int = 3) -> dict:
    """Rough Ld/Lq: Ld = Lm (1 + leakage_fraction), Lq = saliency * Ld.

    saliency = Lq/Ld: ~1 for SPM, 1.5-3 for IPM, 3-8 for SynRM. For SPM use g_eff = kc g + hm/mu_r.
    """
    lm = magnetizing_inductance(turns_per_phase, kw1, bore_mm, stack_mm, poles, g_eff_mm, phases)["value"]
    xi, lf = _pos("saliency", saliency), _pos("leakage_fraction", leakage_fraction, True)
    ld = lm * (1 + lf)
    return _out("dq_inductance", {"Ld": ld, "Lq": xi * ld}, "H", "Ld = Lm (1 + k_sigma); Lq = xi Ld",
                ["Lm from the effective d-axis gap", f"slot+end leakage = {lf:.0%} of Lm", f"saliency xi = {xi}",
                 "unsaturated; IPM Lq drops strongly with load"], 25, Lm_H=lm)


# --------------------------------------------------------------------------- magnetic circuits

def gapped_core_inductance(turns: float, core_area_mm2: float, gap_mm: float, iron_path_mm: float = 0.0,
                           mu_r_iron: float = 2000.0, core_dims_mm: list[float] | None = None,
                           current: float | None = None) -> dict:
    """Gapped/C-core inductor: L = N^2 / (R_gap + R_iron), R_gap = g/(mu0 A_gap), R_iron = l/(mu0 mu_r A).

    gap_mm = total gap length in the flux path (two 0.5 mm gaps -> 1). core_dims_mm=[a, b] (cross-section
    sides) adds the fringing correction A_gap = (a + g)(b + g). current [A] adds the core flux density.
    Example (self-test C-core): N=100, A=750 mm2, g=1 -> 9.42 mH without fringing.
    """
    n, a, g = _pos("turns", turns), _pos("core_area_mm2", core_area_mm2) * MM ** 2, _pos("gap_mm", gap_mm) * MM
    li, mr = _pos("iron_path_mm", iron_path_mm, True) * MM, _pos("mu_r_iron", mu_r_iron)
    a_gap = a
    if core_dims_mm:
        if len(core_dims_mm) != 2:
            raise ValueError("core_dims_mm must be [a_mm, b_mm]")
        x, y = (_pos("core_dims_mm", v) * MM for v in core_dims_mm)
        a_gap = (x + g) * (y + g)
    rg, ri = g / (MU0 * a_gap), li / (MU0 * mr * a)
    ind = n * n / (rg + ri)
    d = {"R_gap_per_H": rg, "R_iron_per_H": ri, "L_gap_only_no_fringing_H": n * n * MU0 * a / g,
         "gap_area_mm2": a_gap / MM ** 2}
    if current is not None:
        d["B_core_T"] = n * float(current) / (rg + ri) / a
    return _out("gapped_core_inductance", ind, "H", "L = N^2 / (g/(mu0 A_gap) + l_fe/(mu0 mu_r A))",
                ["linear iron (check B_core < ~1.5 T)", "uniform flux in the core",
                 "fringing only via the (a+g)(b+g) rule" if core_dims_mm else "no fringing (FEA will be 5-35 % higher in 2D)"],
                10 if core_dims_mm else 30, **d)


def tooth_yoke_flux_density(Bg: float, bore_mm: float, slots: int, poles: int, tooth_width_mm: float,
                            yoke_height_mm: float, pole_arc_ratio: float = 1.0, stacking_factor: float = 0.97) -> dict:
    """Flux conservation from the gap: B_tooth = Bg tau_s / (k_fe w_t); B_yoke = alpha Bg tau_p / (2 k_fe h_y).

    tau_s = pi D/Q slot pitch, tau_p = pi D/(2p) pole pitch at the bore. Typical limits (SiFe):
    teeth 1.5-1.9 T, yoke 1.2-1.6 T.
    """
    b, d = _pos("Bg", Bg), _pos("bore_mm", bore_mm)
    q, p = int(_pos("slots", slots)), _poles(poles)
    wt, hy = _pos("tooth_width_mm", tooth_width_mm), _pos("yoke_height_mm", yoke_height_mm)
    a, kfe = _frac("pole_arc_ratio", pole_arc_ratio), _frac("stacking_factor", stacking_factor)
    ts, tp = math.pi * d / q, math.pi * d / p
    bt = b * ts / (kfe * wt)
    by = a * b * tp / (2 * kfe * hy)
    return _out("tooth_yoke_flux_density", {"tooth": bt, "yoke": by}, "T",
                "Bt = Bg tau_s/(k_fe w_t); By = alpha Bg tau_p/(2 k_fe h_y)",
                ["all gap flux of a slot pitch enters one tooth", "half the pole flux in each yoke direction",
                 "no slot leakage, no armature reaction (no-load)"], 15, slot_pitch_mm=ts, pole_pitch_mm=tp)


# --------------------------------------------------------------------------- copper

def slot_fill(slot_area_mm2: float, conductors_per_slot: float, wire_diameter_mm: float | None = None,
              conductor_area_mm2: float | None = None) -> dict:
    """Slot fill factor = conductors * copper area / slot area (bare copper; insulation and liner excluded).

    Typical achievable: round wire 0.35-0.45 (hand/needle wound), concentrated segmented 0.5-0.6, hairpin 0.6-0.7.
    """
    s, nc = _pos("slot_area_mm2", slot_area_mm2), _pos("conductors_per_slot", conductors_per_slot)
    if (wire_diameter_mm is None) == (conductor_area_mm2 is None):
        raise ValueError("pass exactly one of wire_diameter_mm or conductor_area_mm2")
    ac = math.pi / 4 * _pos("wire_diameter_mm", wire_diameter_mm) ** 2 if wire_diameter_mm is not None \
        else _pos("conductor_area_mm2", conductor_area_mm2)
    kf = nc * ac / s
    d = {"copper_area_mm2": nc * ac, "conductor_area_mm2": ac}
    if kf > 0.75:
        d["warning"] = "fill above ~0.75 is not manufacturable with round wire"
    return _out("slot_fill", kf, "-", "k_fill = N_c A_cu / A_slot", ["bare copper area", "slot area excludes the slot opening"],
                0, **d)


def resistivity(temperature_C: float = 20.0, material: str = "copper") -> float:
    """rho(T) = rho20 (1 + alpha (T - 20)) [ohm m]."""
    key = str(material).lower()
    if key not in RESISTIVITY_20C:
        raise ValueError(f"material must be one of {sorted(RESISTIVITY_20C)}")
    r20, al = RESISTIVITY_20C[key]
    return r20 * (1 + al * (float(temperature_C) - 20))


def copper_loss(current_rms: float, turns_per_phase: float, conductor_area_mm2: float, stack_mm: float,
                end_winding_mm: float | None = None, bore_mm: float | None = None, coil_pitch_slots: int | None = None,
                slots: int | None = None, temperature_C: float = 20.0, phases: int = 3, parallel_paths: int = 1,
                material: str = "copper") -> dict:
    """DC copper loss P = m I^2 R, R_ph = rho(T) N l_mt / (a A_c), l_mt = 2 (L + l_end).

    end_winding_mm = length of ONE end-turn (one side of the stack). If omitted it is estimated as
    1.2 * pi * D * y / Q from bore_mm, coil_pitch_slots and slots (rough; concentrated windings are shorter).
    conductor_area_mm2 = copper area of one turn (all strands in hand). current_rms = phase current.
    """
    i, n = _pos("current_rms", current_rms, True), _pos("turns_per_phase", turns_per_phase)
    ac, l = _pos("conductor_area_mm2", conductor_area_mm2) * MM ** 2, _pos("stack_mm", stack_mm) * MM
    m, a = int(_pos("phases", phases)), int(_pos("parallel_paths", parallel_paths))
    assumptions = ["DC resistance (no skin/proximity effect)", f"{material} at {temperature_C} C"]
    if end_winding_mm is None:
        if bore_mm is None or coil_pitch_slots is None or slots is None:
            raise ValueError("pass end_winding_mm, or bore_mm + coil_pitch_slots + slots to estimate it")
        le = 1.2 * math.pi * _pos("bore_mm", bore_mm) * _pos("coil_pitch_slots", coil_pitch_slots) / _pos("slots", slots) * MM
        assumptions.append("end-turn length 1.2 pi D y / Q (rough)")
    else:
        le = _pos("end_winding_mm", end_winding_mm, True) * MM
    rho = resistivity(temperature_C, material)
    lmt = 2 * (l + le)
    r = rho * n * lmt / (a * ac)
    p = m * i * i * r
    return _out("copper_loss", p, "W", "P = m I^2 rho(T) N 2(L + l_end) / (a A_c)", assumptions, 10,
                R_phase_ohm=r, J_rms_A_per_mm2=i / (a * ac / MM ** 2), mean_turn_length_mm=lmt / MM,
                end_turn_mm=le / MM, rho_ohm_m=rho)


# --------------------------------------------------------------------------- time stepping & losses

def electrical_frequency(rpm: float, poles: int) -> dict:
    """f_e = rpm/60 * pp."""
    f = _pos("rpm", rpm, True) / 60 * _poles(poles) / 2
    return _out("electrical_frequency", f, "Hz", "f = n/60 * p/2", ["synchronous machine (induction: f = f_s, slip separate)"],
                0, period_s=(1 / f) if f else None, omega_e_rad_s=2 * math.pi * f)


def step_plan(rpm: float, poles: int, steps_per_period: int = 60, periods: float = 2.0, slots: int | None = None) -> dict:
    """Transient step planner: dt, step count, end time, and a slide-mesh division suggestion.

    jmag_set_step(steps=total_steps, time_step=dt). The rotor turns 360/(pp*steps_per_period) mech-deg per
    step; a slide-mesh circumferential division of k * pp * steps_per_period over 360 deg makes each step an
    integer number of air-gap elements (JMAG property name for this is not verified here). With slots given,
    also the cogging period (360/lcm(Q, 2p) mech-deg) and steps per cogging period.
    """
    f = electrical_frequency(rpm, poles)["value"]
    if f <= 0:
        raise ValueError("rpm must be > 0 for a transient step plan")
    spp = int(_pos("steps_per_period", steps_per_period))
    per = _pos("periods", periods)
    pp = _poles(poles) // 2
    dt = 1 / (f * spp)
    total = int(math.ceil(spp * per)) + 1
    deg_step = 360.0 / (pp * spp)
    div = pp * spp
    while div < 360:
        div *= 2
    d: dict = {"time_step_s": dt, "total_steps": total, "end_time_s": dt * (total - 1), "electrical_frequency_Hz": f,
               "mech_deg_per_step": deg_step, "slide_mesh_divisions_360deg": div,
               "set_step_call": {"steps": total, "time_step": dt}}
    if slots:
        q = int(_pos("slots", slots))
        cog = 360.0 / math.lcm(q, 2 * pp)
        d["cogging_period_mech_deg"] = cog
        d["steps_per_cogging_period"] = cog / deg_step
        if cog / deg_step < 10:
            d["warning"] = "fewer than 10 steps per cogging period: torque ripple will be under-resolved"
    return _out("step_plan", dt, "s", "dt = 1/(f_e * steps_per_period); steps = ceil(spp*periods) + 1",
                ["constant speed", "first step at t = 0"], 0, **d)


def iron_loss_steinmetz(kh: float, ke: float, B: float, f: float, mass_kg: float, alpha: float = 2.0,
                        beta: float = 2.0, kex: float = 0.0) -> dict:
    """Iron loss P = m (kh f B^alpha + ke f^2 B^beta + kex f^1.5 B^1.5) [W] with B peak [T], f [Hz].

    kh [W/kg/Hz/T^alpha], ke [W/kg/Hz^2/T^beta], kex [W/kg/(Hz T)^1.5]. Fit them from the lamination datasheet
    (e.g. 35JN: kh ~ 0.02-0.03, ke ~ 3e-5-6e-5 at 50-400 Hz). Use the tooth and yoke B separately with
    their masses for a better number.
    """
    kh_, ke_, kx = _pos("kh", kh, True), _pos("ke", ke, True), _pos("kex", kex, True)
    b, fr, m = _pos("B", B, True), _pos("f", f, True), _pos("mass_kg", mass_kg)
    a, bb = _pos("alpha", alpha), _pos("beta", beta)
    ph, pe, px = kh_ * fr * b ** a, ke_ * fr ** 2 * b ** bb, kx * (fr * b) ** 1.5
    return _out("iron_loss_steinmetz", m * (ph + pe + px), "W",
                "P = m (kh f B^alpha + ke f^2 B^beta + kex (f B)^1.5)",
                ["sinusoidal flux at f (harmonics/PWM add 10-50 %)", "uniform B over the mass",
                 "datasheet coefficients (punching/build factor 1.2-1.8 not included)"], 30,
                W_per_kg=ph + pe + px, hysteresis_W=m * ph, eddy_W=m * pe, excess_W=m * px)


KINDS: dict[str, Callable[..., dict]] = {
    "carter_factor": carter_factor,
    "airgap_flux_density": airgap_flux_density,
    "winding_factor": winding_factor,
    "emf_constant": emf_constant,
    "torque_constant": torque_constant,
    "torque_dq": torque_dq,
    "magnetizing_inductance": magnetizing_inductance,
    "dq_inductance": dq_inductance,
    "gapped_core_inductance": gapped_core_inductance,
    "tooth_yoke_flux_density": tooth_yoke_flux_density,
    "slot_fill": slot_fill,
    "copper_loss": copper_loss,
    "electrical_frequency": electrical_frequency,
    "step_plan": step_plan,
    "iron_loss_steinmetz": iron_loss_steinmetz,
}


def signature(kind: str) -> dict:
    fn = KINDS[kind]
    req, opt = [], {}
    for name, p in inspect.signature(fn).parameters.items():
        if p.default is inspect.Parameter.empty:
            req.append(name)
        else:
            opt[name] = p.default
    return {"required": req, "optional": opt, "doc": inspect.getdoc(fn).split("\n\n")[0]}


def run(kind: str, params: dict | None) -> dict:
    """Dispatch by kind; unknown kinds/parameters raise ValueError listing what is accepted."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; kinds: {sorted(KINDS)}")
    params = dict(params or {})
    sig = signature(kind)
    allowed = set(sig["required"]) | set(sig["optional"])
    unknown = sorted(set(params) - allowed)
    missing = [r for r in sig["required"] if r not in params]
    if unknown or missing:
        raise ValueError(f"{kind}: " + "; ".join(x for x in (f"unknown parameters {unknown}" if unknown else "",
                                                             f"missing {missing}" if missing else "") if x)
                         + f". required={sig['required']}, optional={list(sig['optional'])}")
    return KINDS[kind](**params)
