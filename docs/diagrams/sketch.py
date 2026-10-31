#!/usr/bin/env python3
"""Hand-drawn (sketchy) SVG diagram primitives, following the diagram-design skill:
doc-wide 1280x720 viewBox, orthogonal connectors, masked labels, exposure badges."""
import html
import math
import sys

PAPER, PAPER2, INK, MUTED, SOFT = "#f5f5f5", "#ececec", "#2d3142", "#4f5d75", "#5a6580"
ACCENT, ACCENT_T, LINK = "#bf4520", "rgba(191,69,32,0.08)", "#2e5aa8"
SANS, MONO, SERIF = "'Geist', system-ui, sans-serif", "'Geist Mono', ui-monospace, monospace", "'Instrument Serif', serif"
TREAT = {
    "focal": (ACCENT_T, ACCENT, None, ACCENT),
    "backend": ("#ffffff", INK, None, INK),
    "store": ("rgba(45,49,66,0.05)", MUTED, None, MUTED),
    "external": ("rgba(45,49,66,0.03)", "rgba(45,49,66,0.30)", None, SOFT),
    "input": ("rgba(79,93,117,0.10)", SOFT, None, SOFT),
    "optional": ("rgba(45,49,66,0.02)", "rgba(45,49,66,0.20)", "4,3", SOFT),
}
COLW = {"muted": (MUTED, "arrow"), "accent": (ACCENT, "arrow-accent"), "link": (LINK, "arrow-link")}


def e(s):
    return html.escape(s, quote=True)


def w4(n):
    return int(math.ceil(n / 4.0) * 4)


def ortho(pts, r=8):
    """Polyline through orthogonal corner points, corners rounded with radius r."""
    d = f"M {pts[0][0]},{pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        dx0, dy0 = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        dx1, dy1 = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
        d += f" L {x1 - dx0 * r},{y1 - dy0 * r} Q {x1},{y1} {x1 + dx1 * r},{y1 + dy1 * r}"
    d += f" L {pts[-1][0]},{pts[-1][1]}"
    return d


class Fig:
    def __init__(self, slug, title, desc):
        self.slug, self.title, self.desc = slug, title, desc
        self.zshape, self.ztext, self.arrows, self.labels, self.nshape, self.ntext, self.extra = ([] for _ in range(7))
        self.n = {}

    def zone(self, x, y, w, h, label, accent=False, right=False):
        stroke = 'stroke="rgba(191,69,32,0.60)" stroke-width="1.2" stroke-dasharray="6,4"' if accent else 'stroke="rgba(45,49,66,0.18)" stroke-width="0.9"'
        fill = "rgba(191,69,32,0.03)" if accent else "rgba(45,49,66,0.02)"
        self.zshape.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" {stroke}/>')
        lw = w4(len(label) * 7 * 0.62 + 16)
        lx = x + w - 16 - lw if right else x + 16
        self.ztext.append(f'<rect x="{lx}" y="{y + 4}" width="{lw}" height="12" rx="2" fill="{PAPER}"/>'
                          f'<text x="{lx + lw / 2}" y="{y + 13}" fill="{ACCENT if accent else SOFT}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.14em">{e(label)}</text>')

    def arrow(self, pts, kind="muted", dashed=False):
        col, mk = COLW[kind]
        sw = "1.1" if dashed else ("1.6" if kind == "accent" else "1.3")
        da = ' stroke-dasharray="5,4"' if dashed else ""
        self.arrows.append(f'<path d="{ortho(pts)}" fill="none" stroke="{col}" stroke-width="{sw}"{da} marker-end="url(#{self.slug}-{mk})"/>')

    def forbid(self, p0, p1, horizontal=False):
        """Blocked ingress: dashed accent stroke that stops on a bar before the boundary."""
        self.arrows.append(f'<path d="M {p0[0]},{p0[1]} L {p1[0]},{p1[1]}" fill="none" stroke="{ACCENT}" stroke-width="1.4" stroke-dasharray="3,3"/>')
        x, y = p1
        bar = f'M {x},{y - 9} L {x},{y + 9}' if horizontal else f'M {x - 9},{y} L {x + 9},{y}'
        self.arrows.append(f'<path d="{bar}" stroke="{ACCENT}" stroke-width="2.4" stroke-linecap="round"/>')

    def label(self, x, y, text, kind="muted", anchor="middle"):
        col = {"muted": SOFT, "accent": ACCENT, "link": LINK}[kind]
        lw = w4(len(text) * 8 * 0.62 + 8)
        mx = x - lw / 2 if anchor == "middle" else (x - 4 if anchor == "start" else x - lw + 4)
        self.labels.append(f'<rect x="{mx}" y="{y - 9}" width="{lw}" height="12" rx="2" fill="{PAPER}"/>'
                           f'<text x="{x}" y="{y}" fill="{col}" font-size="8" font-family="{MONO}" text-anchor="{anchor}" letter-spacing="0.06em">{e(text)}</text>')

    def node(self, key, x, y, w, h, kind, tag, name, *subs, expo=None):
        fill, stroke, dash, tagc = TREAT[kind]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.n[key] = (x, y, w, h)
        tw = w4(len(tag) * 7 * 0.62 + 10)
        self.nshape.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PAPER}"/>'
                           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.2"{da}/>'
                           f'<rect x="{x + 8}" y="{y + 8}" width="{tw}" height="12" rx="2" fill="transparent" stroke="{tagc}" stroke-opacity="0.45" stroke-width="0.8"/>')
        t = [f'<text x="{x + 8 + tw / 2}" y="{y + 17}" fill="{tagc}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">{e(tag)}</text>']
        if expo:  # exposure badge, top-right: PUBLIC = filled ink, PRIVÉ = outline (shape, not only colour)
            ew = w4(len(expo) * 7 * 0.62 + 10)
            ex = x + w - 8 - ew
            if expo == "PUBLIC":
                self.nshape.append(f'<rect x="{ex}" y="{y + 8}" width="{ew}" height="12" rx="2" fill="{INK}"/>')
                t.append(f'<text x="{ex + ew / 2}" y="{y + 17}" fill="{PAPER}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">{e(expo)}</text>')
            else:
                self.nshape.append(f'<rect x="{ex}" y="{y + 8}" width="{ew}" height="12" rx="2" fill="transparent" stroke="{SOFT}" stroke-width="0.8" stroke-dasharray="2,2"/>')
                t.append(f'<text x="{ex + ew / 2}" y="{y + 17}" fill="{SOFT}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">{e(expo)}</text>')
        block = 12 + 13 * len(subs)
        ty = y + 20 + (h - 20) / 2 - block / 2 + 10
        cx = x + w / 2
        t.append(f'<text x="{cx}" y="{ty}" fill="{INK}" font-size="12" font-weight="600" font-family="{SANS}" text-anchor="middle">{e(name)}</text>')
        for i, s in enumerate(subs):
            t.append(f'<text x="{cx}" y="{ty + 15 + i * 13}" fill="{MUTED}" font-size="9" font-family="{MONO}" text-anchor="middle">{e(s)}</text>')
        self.ntext.append("".join(t))

    def callout(self, x, y, lines, leader, dot, anchor="start", accent=False):
        col = ACCENT if accent else INK
        for i, ln in enumerate(lines):
            self.extra.append(f'<text x="{x}" y="{y + i * 17}" fill="{col}" font-size="14" font-style="italic" font-family="{SERIF}" text-anchor="{anchor}">{e(ln)}</text>')
        self.zshape.append(f'<path d="{leader}" fill="none" stroke="{"rgba(191,69,32,0.50)" if accent else "rgba(45,49,66,0.40)"}" stroke-width="1" stroke-dasharray="4,3"/>')
        self.extra.append(f'<circle cx="{dot[0]}" cy="{dot[1]}" r="2.2" fill="{col}"/>')

    def svg(self, legend):
        s = self.slug
        defs = "".join(f'<marker id="{s}-{i}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{c}"/></marker>'
                       for i, c in (("arrow", MUTED), ("arrow-accent", ACCENT), ("arrow-link", LINK)))
        defs += (f'<filter id="{s}-sketchy" x="-2%" y="-2%" width="104%" height="104%">'
                 '<feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="2" seed="7"/>'
                 '<feDisplacementMap in="SourceGraphic" scale="2.6"/></filter>')
        ly = 640
        leg = [f'<line x1="40" y1="{ly}" x2="1240" y2="{ly}" stroke="rgba(45,49,66,0.10)" stroke-width="0.8"/>',
               f'<text x="40" y="{ly + 22}" fill="{MUTED}" font-size="8" font-family="{MONO}" letter-spacing="0.18em">LEGEND</text>']
        lshape = []
        x = 120
        for kind, text in legend:
            if kind.startswith("arrow"):
                k = kind.split(":")[1]
                col, mk = COLW.get(k, (MUTED, "arrow"))
                da = ' stroke-dasharray="5,4"' if k == "dash" else ""
                lshape.append(f'<line x1="{x}" y1="{ly + 19}" x2="{x + 28}" y2="{ly + 19}" stroke="{col}" stroke-width="1.3"{da} marker-end="url(#{s}-{mk})"/>')
                w = 36
            elif kind == "forbid":
                lshape.append(f'<line x1="{x}" y1="{ly + 19}" x2="{x + 24}" y2="{ly + 19}" stroke="{ACCENT}" stroke-width="1.4" stroke-dasharray="3,3"/>'
                              f'<line x1="{x + 26}" y1="{ly + 12}" x2="{x + 26}" y2="{ly + 26}" stroke="{ACCENT}" stroke-width="2.4" stroke-linecap="round"/>')
                w = 36
            elif kind in ("PUBLIC", "PRIVÉ", "PRIVATE"):
                ew = w4(len(kind) * 7 * 0.62 + 10)
                if kind == "PUBLIC":
                    leg.append(f'<rect x="{x}" y="{ly + 13}" width="{ew}" height="12" rx="2" fill="{INK}"/><text x="{x + ew / 2}" y="{ly + 22}" fill="{PAPER}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">PUBLIC</text>')
                else:
                    leg.append(f'<rect x="{x}" y="{ly + 13}" width="{ew}" height="12" rx="2" fill="transparent" stroke="{SOFT}" stroke-width="0.8" stroke-dasharray="2,2"/><text x="{x + ew / 2}" y="{ly + 22}" fill="{SOFT}" font-size="7" font-family="{MONO}" text-anchor="middle" letter-spacing="0.08em">{e(kind)}</text>')
                w = ew + 8
            elif kind == "zone-accent":
                lshape.append(f'<rect x="{x}" y="{ly + 13}" width="22" height="12" rx="3" fill="rgba(191,69,32,0.03)" stroke="rgba(191,69,32,0.60)" stroke-width="1.2" stroke-dasharray="4,3"/>')
                w = 30
            else:
                fill, stroke, dash, _ = TREAT[kind]
                da = f' stroke-dasharray="{dash}"' if dash else ""
                lshape.append(f'<rect x="{x}" y="{ly + 14}" width="14" height="10" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"{da}/>')
                w = 20
            leg.append(f'<text x="{x + w}" y="{ly + 22}" fill="{MUTED}" font-size="8.5" font-family="{SANS}">{e(text)}</text>')
            x += w + len(text) * 5.2 + 28
        sk = f'filter="url(#{s}-sketchy)"'
        return (f'<svg viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="{s}-title {s}-desc">'
                f'<title id="{s}-title">{e(self.title)}</title><desc id="{s}-desc">{e(self.desc)}</desc>'
                f'<defs>{defs}</defs><rect width="100%" height="100%" fill="{PAPER}"/>'
                f'<g {sk}>{"".join(self.zshape)}{"".join(self.arrows)}</g>'
                f'<g>{"".join(self.ztext)}{"".join(self.labels)}</g>'
                f'<g {sk}>{"".join(self.nshape)}{"".join(lshape)}</g>'
                f'<g>{"".join(self.ntext)}{"".join(self.extra)}{"".join(leg)}</g></svg>')


