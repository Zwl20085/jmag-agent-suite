"""Generate the README hero banner (SVG) procedurally: a 12-slot/4-pole SPM cross-section
with animated flux-like field lines, and the title / feedback-ladder block. No external deps.

usage: python make_hero.py "<title>" "<tagline>" out.svg
"""
import math
import sys

W, H = 1400, 440
TITLE = sys.argv[1] if len(sys.argv) > 1 else "JMAG Agent Suite"
TAG = sys.argv[2] if len(sys.argv) > 2 else "MCP server + skills that let AI agents run JMAG-Designer FEA, and prove the physics."
OUT = sys.argv[3] if len(sys.argv) > 3 else "hero.svg"
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"


def P(r, a):
    t = math.radians(a)
    return (r * math.cos(t), -r * math.sin(t))


def arc(r, a0, a1, ccw=True):
    x1, y1 = P(r, a1)
    large = 1 if abs(a1 - a0) > 180 else 0
    # angles increase counter-clockwise in math coords; with y flipped that is sweep=0 on screen
    sweep = 0 if (a1 > a0) == ccw else 1
    if not ccw:
        sweep = 1 if a1 < a0 else 0
    return "A %.3f %.3f 0 %d %d %.3f %.3f" % (r, r, large, 0 if a1 > a0 else 1, x1, y1)


def sector(r1, r2, a0, a1):
    x0, y0 = P(r1, a0)
    x1, y1 = P(r2, a0)
    x3, y3 = P(r1, a1)
    return ("M %.3f %.3f L %.3f %.3f %s L %.3f %.3f %s Z"
            % (x0, y0, x1, y1, arc(r2, a0, a1), x3, y3, arc(r1, a1, a0)))


parts = []
Rso, Rsi = 100.0, 60.0
g = 2.0
Rro, Rsh = Rsi - g, 22.0
hm = 7.0
Q, p2 = 12, 4
slot_pitch = 360.0 / Q
tooth_frac = 0.42
slot_depth = 26.0
yoke_r = Rsi + slot_depth

parts.append('<circle r="%s" fill="url(#steel)"/>' % Rso)
parts.append('<circle r="%s" fill="#0b1020"/>' % yoke_r)
for k in range(Q):
    c = k * slot_pitch + slot_pitch / 2
    half = slot_pitch * tooth_frac / 2
    parts.append('<path d="%s" fill="url(#steel)"/>' % sector(Rsi, yoke_r + 0.6, c - half, c + half))
    tip = slot_pitch * 0.46
    parts.append('<path d="%s" fill="url(#steel)"/>' % sector(Rsi, Rsi + 3.2, c - tip, c + tip))

phase_cols = ["#ffb454", "#5ad1ff", "#9b8cff"]
for k in range(Q):
    s0 = k * slot_pitch
    ph = phase_cols[k % 3]
    half_t = slot_pitch * tooth_frac / 2
    a0 = s0 - slot_pitch / 2 + half_t + 1.2
    a1 = s0 + slot_pitch / 2 - half_t - 1.2
    parts.append('<path d="%s" fill="%s" opacity="0.85"/>' % (sector(Rsi + 4.0, yoke_r - 1.6, a0, a1), ph))

parts.append('<circle r="%s" fill="url(#steel)"/>' % (Rro - hm))
parts.append('<circle r="%s" fill="#0b1020"/>' % Rsh)
parts.append('<circle r="%s" fill="none" stroke="#38425c" stroke-width="0.8"/>' % Rsh)
for k in range(p2):
    c = k * 360.0 / p2 + 45
    span = 360.0 / p2 * 0.82
    col = "#ff5c7a" if k % 2 == 0 else "#4fd1a1"
    parts.append('<path d="%s" fill="%s" opacity="0.92"/>' % (sector(Rro - hm, Rro, c - span / 2, c + span / 2), col))
    x, y = P(Rro - hm / 2, c)
    parts.append('<text x="%.1f" y="%.1f" font-size="7" font-weight="700" text-anchor="middle" fill="#0b1020">%s</text>'
                 % (x, y + 2.4, "N" if k % 2 == 0 else "S"))

flux = []
for k in range(p2):
    c = k * 360.0 / p2 + 90
    for j in range(3):
        a_in = 360.0 / p2 * (0.22 + 0.16 * j)
        r_lo = Rro - hm - 12 + 5 * j
        r_hi = yoke_r + (Rso - yoke_r) * (0.3 + 0.25 * j)
        d = ("M %.2f %.2f L %.2f %.2f %s L %.2f %.2f %s Z"
             % (P(r_lo, c - a_in) + P(r_hi, c - a_in) + (arc(r_hi, c - a_in, c + a_in),)
                + P(r_lo, c + a_in) + (arc(r_lo, c + a_in, c - a_in),)))
        flux.append('<path d="%s" fill="none" stroke="#7fe3ff" stroke-width="%.2f" opacity="%.2f" stroke-dasharray="3 2.2">'
                    '<animate attributeName="stroke-dashoffset" from="0" to="-10.4" dur="%.1fs" repeatCount="indefinite"/></path>'
                    % (d, 0.9 - 0.15 * j, 0.6 - 0.12 * j, 2.4 + 0.6 * j))

ladder = ["Call", "Setup lint", "Solver", "Physics", "Engineering", "Visual", "Self-test"]
pills = []
x = 0.0
for i, name in enumerate(ladder):
    w = 22 + 8.4 * len(name)
    pills.append('<g transform="translate(%.1f,0)"><rect width="%.0f" height="30" rx="15" fill="#152038" stroke="#2b3b5f"/>'
                 '<circle cx="15" cy="15" r="4.5" fill="%s"/>'
                 '<text x="%.1f" y="19.5" text-anchor="middle" font-size="13" fill="#d7e3ff">%s</text></g>'
                 % (x, w, "#4fd1a1" if i < 6 else "#7fe3ff", w / 2 + 6, name))
    x += w + 16
    if i < len(ladder) - 1:
        pills.append('<text x="%.1f" y="20.5" font-size="15" fill="#3d4f78">&#8250;</text>' % (x - 11.5))

svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{FONT}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#070b16"/><stop offset="0.55" stop-color="#0b1020"/><stop offset="1" stop-color="#101a36"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
    <stop offset="0" stop-color="#2f6bff" stop-opacity="0.45"/><stop offset="1" stop-color="#2f6bff" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="steel" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#8ea2c8"/><stop offset="1" stop-color="#4e5f85"/>
  </linearGradient>
  <linearGradient id="title" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#9fd6ff"/>
  </linearGradient>
  <pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse">
    <path d="M 28 0 L 0 0 0 28" fill="none" stroke="#ffffff" stroke-opacity="0.035" stroke-width="1"/>
  </pattern>
</defs>
<rect width="{W}" height="{H}" rx="22" fill="url(#bg)"/>
<rect width="{W}" height="{H}" rx="22" fill="url(#grid)"/>
<circle cx="300" cy="220" r="260" fill="url(#glow)"/>

<g transform="translate(300,222) scale(1.75)">
  {STATOR}
  <g>
    <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="90s" repeatCount="indefinite"/>
    {ROTOR}
  </g>
  {FLUX}
  <circle r="{Rsi}" fill="none" stroke="#7fe3ff" stroke-opacity="0.35" stroke-width="0.6"/>
  <circle r="{Rro}" fill="none" stroke="#7fe3ff" stroke-opacity="0.35" stroke-width="0.6"/>
</g>

<g font-size="12" fill="#8ea2c8">
  <text x="482" y="78">slide mesh · rotation motion</text><line x1="478" y1="74" x2="408" y2="112" stroke="#5a6b94" stroke-width="1"/>
  <text x="488" y="392">torque 1.774 Nm (FEA) vs 1.777 Nm (from &#968;)</text><line x1="484" y1="386" x2="420" y2="340" stroke="#5a6b94" stroke-width="1"/>
</g>

<g transform="translate(620,118)">
  <text x="0" y="0" font-size="19" letter-spacing="4" fill="#7fe3ff" font-weight="600">JMAG-DESIGNER  ·  MCP SERVER  ·  AGENT SKILLS</text>
  <text x="-3" y="66" font-size="62" font-weight="800" fill="url(#title)">{TITLE}</text>
  <text x="0" y="108" font-size="19" fill="#c8d4f0">{TAG}</text>
  <g transform="translate(0,150)">{PILLS}</g>
  <text x="0" y="218" font-size="14" fill="#6f82ad">every tool answers ok · hint · jmag_messages   |   KVL, energy and power-balance invariants   |   verified on JMAG-Designer 23</text>
</g>
</svg>
"""
# split rotor (rotating) from stator (static)
rotor_parts = parts[-(3 + 2 * p2):]
stator_parts = parts[:-(3 + 2 * p2)]
svg = (svg.replace("{W}", str(W)).replace("{H}", str(H)).replace("{FONT}", FONT)
       .replace("{ROTOR}", "".join(rotor_parts)).replace("{STATOR}", "".join(stator_parts))
       .replace("{FLUX}", "".join(flux)).replace("{Rsi}", str(Rsi)).replace("{Rro}", str(Rro))
       .replace("{TITLE}", TITLE).replace("{TAG}", TAG).replace("{PILLS}", "".join(pills)))
open(OUT, "w", encoding="utf-8").write(svg)
print("wrote", OUT, len(svg), "bytes")
