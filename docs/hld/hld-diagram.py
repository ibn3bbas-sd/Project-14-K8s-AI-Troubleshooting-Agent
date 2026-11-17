#!/usr/bin/env python3
"""K8s AI Troubleshooting Agent on Huawei Cloud CCE — High-Level Design.

Generates the HLD diagram at FHD (1920 x 1080), in light and dark themes:

    python docs/hld/hld-diagram.py

Writes next to this file:
    hld-k8s-ai-agent-light.svg / -dark.svg   theme-specific vectors
    hld-k8s-ai-agent-auto.svg                one file, follows prefers-color-scheme

Rasterise the PNGs the README embeds (GitHub strips CSS media queries from
images, so the auto SVG is not used there):

    rsvg-convert -w 1920 -h 1080 hld-k8s-ai-agent-dark.svg  -o hld-k8s-ai-agent-dark.png
    rsvg-convert -w 1920 -h 1080 hld-k8s-ai-agent-light.svg -o hld-k8s-ai-agent-light.png

(or: npx @resvg/resvg-js-cli, or a headless browser screenshot at 1920x1080).

Standard library only. Edit this file, never the generated SVG/PNG.
"""

from __future__ import annotations

import math
import pathlib

W, H = 1920, 1080
FONT = "'Inter','Segoe UI','Noto Sans','DejaVu Sans',Helvetica,Arial,sans-serif"

THEMES = {
    "dark": dict(
        bg="#0d1117", dots="#161b22", text="#e6edf3", muted="#9198a1", faint="#7d8590",
        panel="#161b22", border="#30363d", tile_bg="#0d1117", wash="#ffffff", wash_op=0.03,
        huawei="#f8637a", vpc="#ff9a4d", subnet="#3fb950", cce="#58a6ff", ns="#bc8cff",
        ingress="#58a6ff", egress="#3fb950", internal="#6e7681", approve="#e3b341",
        c=dict(user="#8b949e", lock="#ff7b6b", eip="#3fd68b", elb="#58a6ff", nat="#3fd68b",
               claude="#e8916b", git="#8b949e", swr="#ffab4d", ui="#58a6ff", agent="#bc8cff",
               db="#58a6ff", api="#3fd68b", box="#ffab4d", shield="#ff8080", server="#ffab4d",
               disk="#ff8080"),
    ),
    "light": dict(
        bg="#ffffff", dots="#eef1f4", text="#1f2328", muted="#57606a", faint="#8c959f",
        panel="#f6f8fa", border="#d0d7de", tile_bg="#ffffff", wash="#0b1220", wash_op=0.025,
        huawei="#c8102e", vpc="#e8590c", subnet="#1a7f37", cce="#0969da", ns="#8250df",
        ingress="#0969da", egress="#1a7f37", internal="#8c959f", approve="#9a6700",
        c=dict(user="#6e7781", lock="#e5534b", eip="#12b76a", elb="#2e90fa", nat="#12b76a",
               claude="#c96442", git="#6e7781", swr="#f79009", ui="#2e90fa", agent="#8250df",
               db="#2e90fa", api="#12b76a", box="#f79009", shield="#e04f4f", server="#f79009",
               disk="#e04f4f"),
    ),
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------- canvas
class Canvas:
    def __init__(self, theme: dict):
        self.t = theme
        self.parts: list[str] = []
        self.late: list[str] = []  # drawn last (labels over lines)

    def add(self, s: str, late: bool = False):
        (self.late if late else self.parts).append(s)

    def rect(self, x, y, w, h, r=10, fill="none", stroke="none", sw=2, dash=None,
             fill_op=None, late=False):
        a = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="{sw}"')
        if dash:
            a += f' stroke-dasharray="{dash}"'
        if fill_op is not None:
            a += f' fill-opacity="{fill_op}"'
        self.add(a + "/>", late)

    def text(self, x, y, s, size=14, fill=None, weight=400, anchor="start", spacing=0,
             late=False):
        self.add(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or self.t["text"]}" text-anchor="{anchor}" '
            f'letter-spacing="{spacing}">{esc(s)}</text>',
            late,
        )

    def line(self, x1, y1, x2, y2, color, sw=1.5, op=1.0):
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
                 f'stroke-width="{sw}" opacity="{op}"/>')

    def arrow(self, pts, color, sw=2.4, dash=None, head=10, both=False):
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                 f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}/>')
        self._head(pts[-2], pts[-1], color, head)
        if both:
            self._head(pts[1], pts[0], color, head)

    def _head(self, p1, p2, color, size):
        (x1, y1), (x2, y2) = p1, p2
        ang = math.atan2(y2 - y1, x2 - x1)
        a = (x2 - size * math.cos(ang - 0.42), y2 - size * math.sin(ang - 0.42))
        b = (x2 - size * math.cos(ang + 0.42), y2 - size * math.sin(ang + 0.42))
        self.add(f'<polygon points="{x2},{y2} {a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f}" '
                 f'fill="{color}"/>')

    def svg(self, label: str) -> str:
        body = "\n".join(self.parts + self.late)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                f'viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}">\n{body}\n</svg>\n')


# --------------------------------------------------------------------- icons
# Each glyph is drawn in a 24x24 box with white strokes, then scaled into a tile.
def _glyph(kind: str) -> str:
    def p(d, fill="none", sw=1.9, op=1.0):
        return (f'<path d="{d}" fill="{fill}" stroke="{"none" if fill != "none" else "#fff"}" '
                f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" opacity="{op}"/>')

    def c(x, y, r, filled=False, sw=1.8):
        return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="{"#fff" if filled else "none"}" '
                f'stroke="{"none" if filled else "#fff"}" stroke-width="{sw}"/>')

    def r(x, y, w, h, rx=1.6, sw=1.8):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="none" '
                f'stroke="#fff" stroke-width="{sw}"/>')

    g = {
        "cloud": p("M6.5 18h11a4 4 0 0 0 .6-8 5.5 5.5 0 0 0-10.5-1.6A4.4 4.4 0 0 0 6.5 18z", fill="#fff"),
        "user": c(12, 8, 3.6) + p("M4.8 20c.7-3.9 3.6-6.1 7.2-6.1s6.5 2.2 7.2 6.1"),
        "lock": r(5.4, 10.6, 13.2, 9.4, 2) + p("M8.4 10.6V8a3.6 3.6 0 0 1 7.2 0v2.6") + c(12, 15.2, 1.4, True),
        "ip": r(3.5, 6.5, 17, 11, 2.4)
        + '<text x="12" y="15.3" font-family="Arial,sans-serif" font-size="8.4" font-weight="700" '
          'fill="#fff" text-anchor="middle">IP</text>',
        "elb": c(12, 5.6, 2.1, True) + c(5.4, 18.4, 2.1, True) + c(12, 18.4, 2.1, True)
        + c(18.6, 18.4, 2.1, True) + p("M12 7.7v8.6 M5.4 16.3v-2.4h13.2v2.4"),
        "nat": p("M5 9h11 M13 6l3.4 3-3.4 3") + p("M19 15H8 M11 12l-3.4 3 3.4 3"),
        "spark": p("M12 3l2 5.9 5.9 2-5.9 2L12 18.8l-2-5.9-5.9-2 5.9-2z", fill="#fff")
        + p("M18.8 15.6l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z", fill="#fff", op=0.85),
        "branch": c(7, 5.6, 2) + c(7, 18.4, 2) + c(17, 8, 2) + p("M7 7.6v8.8 M17 10c0 4-4.2 3.6-8.6 6.6"),
        "box": p("M12 3.6l7.6 4.2v8.4L12 20.4l-7.6-4.2V7.8z") + p("M4.4 7.8L12 12l7.6-4.2 M12 12v8.4", sw=1.6),
        "code": p("M8.6 7.4L4 12l4.6 4.6 M15.4 7.4L20 12l-4.6 4.6 M13.4 5.6l-2.8 12.8"),
        "db": p("M12 3.8c4.4 0 8 1.1 8 2.5v11.4c0 1.4-3.6 2.5-8 2.5s-8-1.1-8-2.5V6.3c0-1.4 3.6-2.5 8-2.5z")
        + p("M4 6.3c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5") + p("M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5", op=0.75),
        "api": c(12, 12, 3) + p("M12 3.6v3.6 M12 16.8v3.6 M3.6 12h3.6 M16.8 12h3.6 M6 6l2.5 2.5 "
                                 "M15.5 15.5L18 18 M18 6l-2.5 2.5 M8.5 15.5L6 18", sw=1.6),
        "shield": p("M12 3.6l7 2.6v5.3c0 4.3-2.9 7.5-7 8.9-4.1-1.4-7-4.6-7-8.9V6.2z") + p("M9 12.1l2.1 2.1 4-4.3", sw=2),
        "server": r(3.6, 5, 16.8, 6) + r(3.6, 13, 16.8, 6) + c(7, 8, 1.1, True) + c(7, 16, 1.1, True)
        + p("M10 8h7 M10 16h7", sw=1.6),
        "disk": p("M4 7c0-1.4 3.6-2.5 8-2.5S20 5.6 20 7v10c0 1.4-3.6 2.5-8 2.5S4 18.4 4 17z")
        + p("M4 7c0 1.4 3.6 2.5 8 2.5S20 8.4 20 7"),
        "k8s": p("M12 3.4l7.2 3.6v9.9L12 20.6 4.8 16.9V7z") + c(12, 12, 2.3)
        + p("M12 6.2v3.4 M12 14.3v3.4 M8.1 9.6l2.1 1.2 M13.8 12.2l2.1 1.2 M15.9 9.6l-2.1 1.2 M10.2 12.2l-2.1 1.2", sw=1.5),
        "net": c(12, 12, 8.4) + p("M3.6 12h16.8 M12 3.6c2.4 2.6 3.6 5.4 3.6 8.4S14.4 18 12 20.4"
                                  "c-2.4-2.4-3.6-5.4-3.6-8.4S9.6 6.2 12 3.6z", sw=1.5),
        "search": c(10.5, 10.5, 5.5) + p("M14.6 14.6L20 20", sw=2.2),
        "doc": p("M6.4 3.6h7.4l4.2 4.2v12.6H6.4z") + p("M13.6 3.8v4.2h4.2 M9.2 12.6h6 M9.2 16h6", sw=1.5),
        "check": p("M5 12.5l4.4 4.4L19 7.2", sw=2.6),
        "wrench": p("M14.7 6.3a4 4 0 0 0-5.2 5.2L4.6 16.4a1.6 1.6 0 0 0 2.3 2.3l4.9-4.9a4 4 0 0 0 5.2-5.2l-2.4 2.4-2.3-.6-.6-2.3z"),
    }
    return g[kind]


def tile(cv: Canvas, kind, cx, cy, size, color, radius=None, late=False):
    """Coloured rounded square with a white glyph."""
    r = radius if radius is not None else size * 0.26
    k = size * 0.72 / 24
    cv.add(f'<rect x="{cx - size / 2}" y="{cy - size / 2}" width="{size}" height="{size}" '
           f'rx="{r}" fill="{color}"/>', late)
    cv.add(f'<g transform="translate({cx - size * 0.36:.2f},{cy - size * 0.36:.2f}) scale({k:.4f})">'
           f'{_glyph(kind)}</g>', late)


def bare(cv: Canvas, kind, cx, cy, size, color):
    """Glyph drawn in `color` without a tile."""
    k = size / 24
    cv.add(f'<g transform="translate({cx - size / 2:.2f},{cy - size / 2:.2f}) scale({k:.4f})">'
           + _glyph(kind).replace('"#fff"', f'"{color}"') + "</g>")


# --------------------------------------------------------------------- diagram
def build(name: str) -> str:
    t = THEMES[name]
    c = t["c"]
    cv = Canvas(t)
    ing, egr, itn = t["ingress"], t["egress"], t["internal"]

    cv.add(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')
    cv.add(f'<defs><pattern id="dots-{name}" width="32" height="32" patternUnits="userSpaceOnUse">'
           f'<circle cx="1.2" cy="1.2" r="1.2" fill="{t["dots"]}"/></pattern></defs>')
    cv.add(f'<rect width="{W}" height="{H}" fill="url(#dots-{name})"/>')

    # ------------------------------------------------------------- header
    cv.text(48, 52, "K8s AI Troubleshooting Agent on Huawei Cloud — High-Level Design (HLD)", 30, weight=700)
    cv.text(48, 78, "Claude API (Opus 5.5) · CCE cluster · one VPC · in-cluster agent with a human approval gate",
            15.5, t["muted"])
    cv.text(1880, 52, "FHD 1920 × 1080", 14, t["faint"], 600, "end")
    cv.text(1880, 74, "v1 · theme-aware", 13.5, t["faint"], anchor="end")
    cv.line(48, 96, 1880, 96, t["border"], 1.5, 0.9)

    # ------------------------------------------------------------- containers
    HX, HY, HW, HH = 330, 352, 1116, 668   # Huawei Cloud region
    VX, VY, VW, VH = 352, 468, 1072, 538   # VPC
    SX, SY, SW, SH = 372, 512, 1032, 480   # node subnet
    CX, CY, CW, CH = 392, 556, 992, 420    # CCE cluster
    NX, NY, NW, NH = 416, 640, 710, 190    # namespace ai-agent

    def frame(x, y, w, h, color, dash=None, r=14):
        cv.rect(x, y, w, h, r, fill=t["wash"], stroke=color, sw=2.2, dash=dash, fill_op=t["wash_op"])

    frame(HX, HY, HW, HH, t["huawei"], "10 7")
    frame(VX, VY, VW, VH, t["vpc"], "8 6")
    frame(SX, SY, SW, SH, t["subnet"])
    frame(CX, CY, CW, CH, t["cce"])
    frame(NX, NY, NW, NH, t["ns"], "6 5", 12)

    def chip(x, y, label, sub, color, kind):
        w = 24 + len(label) * 9.0 + 24 + (len(sub) * 7.4 + 12 if sub else 0)
        cv.rect(x, y - 17, w, 34, 9, fill=t["tile_bg"], stroke=color, sw=1.8, late=True)
        tile(cv, kind, x + 23, y, 22, color, 6, late=True)
        cv.add(f'<text x="{x + 42}" y="{y + 5.5}" font-family="{FONT}" font-size="14.5">'
               f'<tspan font-weight="700" fill="{color}">{esc(label)}</tspan>'
               f'<tspan fill="{t["muted"]}">  {esc(sub)}</tspan></text>', late=True)
        return w

    chip(780, HY, "Huawei Cloud", "· region (e.g. me-east-1)", t["huawei"], "cloud")
    chip(VX + 16, VY, "VPC", "· agent VPC", t["vpc"], "net")
    chip(SX + 16, SY, "Subnet", "· CCE nodes", t["subnet"], "shield")
    chip(CX + 16, CY, "CCE", "· Kubernetes cluster", t["cce"], "k8s")
    chip(NX + NW - 252, NY, "Namespace", "· ai-agent", t["ns"], "box")

    def labelled(x, y, kind, color, title, sub=None, size=50, side="right"):
        tile(cv, kind, x, y, size, color)
        tx = x + size / 2 + 12 if side == "right" else x - size / 2 - 12
        anchor = "start" if side == "right" else "end"
        cv.text(tx, y - 2, title, 15, weight=700, anchor=anchor)
        if sub:
            cv.text(tx, y + 16, sub, 12.5, t["muted"], anchor=anchor)

    IN_X, EG_X, CI_X = 690, 1160, 420

    # ------------------------------------------------------------- north side
    bare(cv, "user", IN_X, 146, 46, c["user"])
    cv.text(IN_X + 34, 143, "Operator", 15, weight=700)
    cv.text(IN_X + 34, 161, "SRE / DevOps in a browser", 12.5, t["muted"])
    labelled(IN_X, 246, "lock", c["lock"], "VPN", "private access to the VPC")
    labelled(IN_X, 410, "elb", c["elb"], "ELB (private, optional)", "or kubectl port-forward", 52)

    labelled(EG_X, 146, "spark", c["claude"], "Claude API", "api.anthropic.com · Opus 5.5", 52)
    labelled(EG_X, 318, "ip", c["eip"], "EIP", "public outbound IP")
    labelled(EG_X, 410, "nat", c["nat"], "SNAT", "NAT gateway (outbound)", 52)

    labelled(CI_X, 146, "branch", c["git"], "GitHub Actions", "test → build → push")
    labelled(CI_X, 410, "box", c["swr"], "SWR", "image registry → node pulls", 52)

    # ------------------------------------------------------------- namespace ai-agent
    cards = [
        (436, "code", c["ui"], "Frontend", ["Next.js UI · /api proxy", "Deployment × 1"]),
        (676, "spark", c["agent"], "Agent backend", ["FastAPI · agent loop", "27 tools · approval gate", "Deployment × 1 (Recreate)"]),
        (916, "db", c["db"], "PostgreSQL", ["session store", "StatefulSet + EVS 10 Gi"]),
    ]
    CARD_Y, CARD_H, CARD_W = 680, 132, 196
    for x, kind, color, title, lines in cards:
        cv.rect(x, CARD_Y, CARD_W, CARD_H, 10, fill=t["panel"], stroke=t["border"], sw=1.5)
        tile(cv, kind, x + 32, CARD_Y + 32, 38, color)
        cv.text(x + 60, CARD_Y + 37, title, 15, weight=700)
        for i, ln in enumerate(lines):
            cv.text(x + 16, CARD_Y + 74 + i * 19, ln, 12.5, t["muted"])

    # Kubernetes API server
    AX, AY, AW, AH = 1150, 640, 210, 190
    cv.rect(AX, AY, AW, AH, 12, fill=t["panel"], stroke=t["cce"], sw=1.8)
    tile(cv, "api", AX + 34, AY + 36, 40, c["api"])
    cv.text(AX + 64, AY + 32, "kube-apiserver", 15, weight=700)
    cv.text(AX + 64, AY + 50, "CCE control plane", 12.5, t["muted"])
    for i, ln in enumerate(["managed by Huawei Cloud", "RBAC: reader (no Secrets)", "+ remediator (narrow)", "ServiceAccount auth"]):
        cv.text(AX + 16, AY + 92 + i * 20, ln, 12.5, t["muted"])

    # bottom row: what the agent inspects / fixes
    ROW_Y, ROW_H = 880, 80
    bottom = [
        (416, 236, "server", c["server"], "Worker nodes", "ECS × N · node pools · EVS"),
        (672, 344, "box", c["box"], "Application namespaces", "Deployments · Pods · Services · PVCs"),
        (1036, 324, "shield", c["shield"], "Protected (read-only)", "kube-system · monitoring · ai-agent"),
    ]
    for x, w, kind, color, title, sub in bottom:
        cv.rect(x, ROW_Y, w, ROW_H, 10, fill=t["panel"], stroke=t["border"], sw=1.5)
        tile(cv, kind, x + 32, ROW_Y + 40, 38, color)
        cv.text(x + 62, ROW_Y + 36, title, 14.5, weight=700)
        cv.text(x + 62, ROW_Y + 55, sub, 12, t["muted"])

    # ------------------------------------------------------------- flows
    # ingress: operator -> VPN -> ELB -> frontend
    cv.arrow([(IN_X, 171), (IN_X, 219)], ing)
    cv.arrow([(IN_X, 271), (IN_X, 382)], ing)
    cv.arrow([(IN_X, 436), (IN_X, 616), (534, 616), (534, CARD_Y)], ing)
    cv.text(IN_X + 8, 502, "HTTPS", 12.5, ing, 700)

    # egress: backend -> SNAT -> EIP -> Claude API
    bx = 676 + CARD_W / 2
    cv.arrow([(bx, CARD_Y), (bx, 630), (EG_X, 630), (EG_X, 438)], egr)
    cv.arrow([(EG_X, 384), (EG_X, 345)], egr)
    cv.arrow([(EG_X, 291), (EG_X, 174)], egr)
    cv.text(EG_X + 8, 502, "HTTPS 443 · Messages API", 12.5, egr, 700)

    # CI: GitHub -> SWR -> node image pulls
    cv.arrow([(CI_X, 173), (CI_X, 382)], itn, dash="6 6")
    cv.text(CI_X + 8, 262, "docker push", 12, t["faint"], 600)
    cv.arrow([(CI_X - 28, 410), (362, 410), (362, ROW_Y + 40), (416, ROW_Y + 40)], itn, dash="6 6")

    # inside the namespace
    mid = CARD_Y + 50
    cv.arrow([(436 + CARD_W, mid), (676, mid)], itn, dash="5 5")
    cv.text(656, mid - 8, "/api", 11.5, t["faint"], 600, "middle")
    cv.arrow([(676 + CARD_W, mid), (916, mid)], itn, dash="5 5")
    cv.text(896, mid - 8, "SQL", 11.5, t["faint"], 600, "middle")

    # backend -> kube-apiserver
    cv.arrow([(bx, CARD_Y + CARD_H), (bx, 848), (1200, 848), (1200, AY + AH)], itn, dash="6 6")
    cv.text(bx + 10, 842, "tools: read always · write only after operator approval", 12, t["faint"], 600)

    # kube-apiserver -> nodes / namespaces
    cv.arrow([(1310, AY + AH), (1310, 866), (1198, 866), (1198, ROW_Y)], itn, dash="6 6")
    cv.arrow([(1198, 866), (844, 866), (844, ROW_Y)], itn, dash="6 6")
    cv.arrow([(844, 866), (534, 866), (534, ROW_Y)], itn, dash="6 6")

    # ------------------------------------------------------------- left: at a glance
    PX, PY, PW = 48, 120, 262
    cv.rect(PX, PY, PW, 432, 14, fill=t["panel"], stroke=t["border"], sw=1.6)
    cv.text(PX + 18, PY + 36, "At a glance", 18, weight=700)
    cv.line(PX + 18, PY + 50, PX + PW - 18, PY + 50, t["border"], 1.2)
    bullets = [
        ["1 CCE cluster · namespace ai-agent"],
        ["UI + agent backend + PostgreSQL"],
        ["Model: Claude Opus 5.5 via the", "Claude API (API key in a Secret)"],
        ["Ingress: VPN → private ELB", "(or kubectl port-forward)"],
        ["Egress: SNAT + EIP →", "api.anthropic.com:443"],
        ["Images: GitHub Actions → SWR,", "pulled via default-secret"],
        ["Sessions on 1 × EVS disk"],
        ["No Secret read access", "Writes only after approval"],
    ]
    y = PY + 80
    for lines in bullets:
        cv.add(f'<circle cx="{PX + 24}" cy="{y - 4}" r="3" fill="{t["cce"]}"/>')
        for ln in lines:
            cv.text(PX + 36, y, ln, 13, t["muted"])
            y += 17
        y += 12

    # ------------------------------------------------------------- left: workflow
    QY, QH = 572, 448
    cv.rect(PX, QY, PW, QH, 14, fill=t["panel"], stroke=t["border"], sw=1.6)
    cv.text(PX + 18, QY + 36, "Agent workflow", 18, weight=700)
    cv.text(PX + 18, QY + 56, "enforced in code, per session", 12.5, t["muted"])
    steps = [
        ("search", t["cce"], "1  Diagnose", "read tools only"),
        ("doc", t["ns"], "2  Propose", "one issue per problem"),
        ("user", t["approve"], "3  Approve", "operator decides (gate)"),
        ("wrench", t["huawei"], "4  Remediate", "writes unlocked · ≤ 10"),
        ("check", t["egress"], "5  Verify", "report_outcome · relock"),
    ]
    sy = QY + 100
    for i, (kind, color, title, sub) in enumerate(steps):
        tile(cv, kind, PX + 40, sy, 34, color)
        cv.text(PX + 70, sy - 2, title, 14.5, weight=700)
        cv.text(PX + 70, sy + 16, sub, 12, t["muted"])
        if i < len(steps) - 1:
            cv.arrow([(PX + 40, sy + 19), (PX + 40, sy + 51)], t["faint"], 1.8, head=7)
        sy += 70
    # loop back
    top, bottom_y = QY + 100, QY + 100 + 4 * 70
    cv.arrow([(PX + 23, bottom_y), (PX + 12, bottom_y), (PX + 12, top), (PX + 23, top)], t["approve"], 1.6, "4 4", 7)
    cv.text(PX + 18, QY + QH - 22, "loop: unresolved or rejected → new plan", 11.5, t["faint"])

    # ------------------------------------------------------------- right: abbreviations
    LX, LY, LW, LH = 1466, 120, 414, 902
    cv.rect(LX, LY, LW, LH, 14, fill=t["panel"], stroke=t["border"], sw=1.6)
    y = LY + 40
    cv.text(LX + 24, y, "Abbreviations", 19, weight=700)
    y += 20
    cv.text(LX + 24, y, "every acronym used in this diagram", 12.5, t["muted"])
    y += 22
    sections = [
        ("Network & access", c["elb"], [
            ("VPN", "Virtual Private Network"),
            ("VPC", "Virtual Private Cloud"),
            ("ELB", "Elastic Load Balance"),
            ("EIP", "Elastic IP (public address)"),
            ("SNAT", "Source NAT — outbound only"),
            ("HTTPS", "HTTP over TLS (port 443)"),
        ]),
        ("Compute & containers", c["swr"], [
            ("CCE", "Cloud Container Engine (K8s)"),
            ("ECS", "Elastic Cloud Server (VM)"),
            ("EVS", "Elastic Volume Service (block)"),
            ("SWR", "SoftWare Repository for Container"),
            ("K8s", "Kubernetes"),
            ("RBAC", "Role-Based Access Control"),
            ("PVC", "PersistentVolumeClaim"),
        ]),
        ("AI & application", c["claude"], [
            ("API", "Application Programming Interface"),
            ("UI", "User Interface (web)"),
            ("SQL", "Structured Query Language"),
            ("CI", "Continuous Integration"),
        ]),
        ("Document", t["faint"], [
            ("HLD", "High-Level Design"),
            ("FHD", "Full HD — 1920 × 1080 px"),
        ]),
    ]
    for title, color, rows in sections:
        y += 24
        cv.line(LX + 24, y - 12, LX + LW - 24, y - 12, t["border"], 1.2, 0.8)
        cv.text(LX + 24, y + 2, title.upper(), 11.5, color, 700, spacing=1.2)
        y += 20
        for ab, desc in rows:
            cv.add(f'<rect x="{LX + 24}" y="{y - 11}" width="4" height="14" rx="2" fill="{color}" opacity="0.55"/>')
            cv.text(LX + 38, y, ab, 14, weight=700)
            cv.text(LX + 112, y, desc, 13.5, t["muted"])
            y += 24
    y += 14
    cv.line(LX + 24, y - 12, LX + LW - 24, y - 12, t["border"], 1.2, 0.8)
    cv.text(LX + 24, y + 2, "FLOW KEY", 11.5, t["faint"], 700, spacing=1.2)
    y += 22
    for color, label, dash in ((ing, "Ingress  (operator → UI)", None),
                               (egr, "Egress  (agent → Claude API)", None),
                               (itn, "Internal  (in-cluster / CI)", "6 6")):
        cv.arrow([(LX + 26, y - 4), (LX + 96, y - 4)], color, 2.4, dash)
        cv.text(LX + 112, y, label, 13.5, t["muted"])
        y += 25

    # ------------------------------------------------------------- footer
    cv.line(48, 1040, 1880, 1040, t["border"], 1.5, 0.9)
    cv.text(48, 1063, "Ingress: operator → VPN → private ELB (or port-forward) → UI   ·   "
                      "Egress: agent → SNAT → EIP → api.anthropic.com   ·   CI: GitHub Actions → SWR",
            13.5, t["muted"])
    cv.text(1880, 1063, "Not shown: CFW · LTS logs · AOM metrics · NetworkPolicy", 13, t["faint"], anchor="end")

    return cv.svg("K8s AI Troubleshooting Agent on Huawei Cloud CCE — high-level design")


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent
    svgs = {}
    for name in THEMES:
        svgs[name] = build(name)
        (out / f"hld-k8s-ai-agent-{name}.svg").write_text(svgs[name], encoding="utf-8")

    def inner(svg: str) -> str:
        return svg.split(">", 1)[1].rsplit("</svg>", 1)[0]

    css = ("<style>.theme-dark{display:none}@media (prefers-color-scheme: dark)"
           "{.theme-light{display:none}.theme-dark{display:inline}}</style>")
    (out / "hld-k8s-ai-agent-auto.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="K8s AI Troubleshooting Agent — HLD">{css}\n'
        f'<g class="theme-light">{inner(svgs["light"])}</g>\n'
        f'<g class="theme-dark">{inner(svgs["dark"])}</g>\n</svg>\n',
        encoding="utf-8",
    )
    print("wrote hld-k8s-ai-agent-{light,dark,auto}.svg to", out)
