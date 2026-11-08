#!/usr/bin/env python3
"""Build the public infrastructure diagrams shown in the main README.

The figures are drawn with the stack-diagram skill
(https://github.com/caru-ini/stack-diagram, MIT). This script places the nodes
and arrows on the skill's template, fetches the logos with the skill's
fetch_icons.py (the list is in icons.txt), and inlines them with the skill's
build.py. Run render.py afterwards to turn the pages into PNG files.

Usage:
  python3 -I build.py --skill DIR [--work DIR] [--refresh-icons]

DIR is the `skills/stack-diagram` folder of a clone of the skill. WORK holds the
downloaded logos and the generated pages (default: ~/.cache/home-server-diagrams).

Public version: names, roles, and public ports only. The script refuses any IPv4
address or domain name in a label, and any match of your own deny list (see
private_words), so a private detail cannot reach the public repository through a
figure.
"""
import argparse, html, json, os, pathlib, re, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_WORK = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "home-server-diagrams"
TEMPLATE, MANIFEST = "", {}          # set by main()

esc = lambda s: html.escape(s, quote=False)

# The template has two group palettes (warm vendor, accent). These add a green one for
# data and a violet one for apps, a blue outline for "reachable from the internet", and
# its legend swatch. Same token structure as the template: only tokens, no loose colors.
EXTRA_ROOT = """  /* extra group palettes (same structure as palettes A/B, hue retuned per domain) */
  --color-g2:      oklch(60% 0.12 160);   /* green  : data / storage */
  --color-g2-soft: oklch(72% 0.10 160 / .6);
  --color-g2-ink:  oklch(44% 0.10 160);
  --color-g2-tint: oklch(97% 0.02 160 / .55);
  --color-g3:      oklch(56% 0.15 300);   /* violet : apps / agents */
  --color-g3-soft: oklch(70% 0.11 300 / .55);
  --color-g3-ink:  oklch(44% 0.14 300);
  --color-g3-tint: oklch(97% 0.015 300 / .55);
"""
EXTRA_DARK = """  --color-g2:      oklch(72% 0.12 160);
  --color-g2-soft: oklch(60% 0.10 160 / .6);
  --color-g2-ink:  oklch(80% 0.11 160);
  --color-g2-tint: oklch(26% 0.04 160 / .40);
  --color-g3:      oklch(72% 0.13 300);
  --color-g3-soft: oklch(60% 0.10 300 / .55);
  --color-g3-ink:  oklch(80% 0.11 300);
  --color-g3-tint: oklch(27% 0.045 300 / .40);
"""
EXTRA_CLASSES = """.group.gc { border:1.5px solid var(--color-g2); background: var(--color-g2-tint); }
.group.gd { border:1.5px solid var(--color-g3); background: var(--color-g3-tint); }
.group.ge { border:1.5px dashed var(--color-rule-2); background: var(--color-paper-2); }
.group.gc > .g-label { color: var(--color-g2-ink); }
.group.gd > .g-label { color: var(--color-g3-ink); }
/* a blue outline marks what the internet can reach */
.tile.pub { border-color: var(--color-accent); box-shadow: 0 0 0 3px var(--color-accent-soft); }
.legend .sw.pub { width:16px; height:16px; margin:0 9px 0 4px; background:#fff; border:1.6px solid var(--color-accent);
  border-radius:5px; box-shadow: 0 0 0 3px var(--color-accent-soft); }
"""

SRC_NAMES = {"svgl": "svgl", "devicon": "devicon", "gilbarbara": "gilbarbara/logos",
             "explicit": "official sites and repositories", "lucide": "Lucide"}
KICK = "home-server / infrastructure"
FOOT = "UGREEN NASync · public version: no LAN addresses, VPN peers, or known weak spots"

# Anything that must never appear in a public figure.
FORBIDDEN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"                        # IPv4
                       r"|\b[\w-]+\.(?:com|net|org|io|fr|me|app|dev|xyz)\b", re.I)  # domain names
DENY_FILE = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "home-server-diagrams" / "deny.txt"


def private_words():
    """Optional deny list of private words (names, hostnames): one regular expression per line.
    It lives outside the repository, because the words are the very thing to keep out of it."""
    if not DENY_FILE.is_file():
        return []
    lines = DENY_FILE.read_text(encoding="utf-8").splitlines()
    return [re.compile(l.strip(), re.I) for l in lines if l.strip() and not l.lstrip().startswith("#")]


class Fig:
    """One figure: nodes, groups, edges, and the geometry checks of the skill."""

    def __init__(self, key, title, lede, w, h, legend):
        self.key, self.title, self.lede = key, title, lede
        self.cw, self.ch, self.legend = w, h, legend
        self.nodes, self.groups, self.paths, self.texts = {}, [], [], []
        self.segs, self.lbls = [], []          # for the checks: (name, points) / (text, rect)

    # ---- nodes: 132 px wide box = 60 px tile + name + role (geometry.md) -------------
    def node(self, id, cx, top, name, icon, role, tile="", wide_name=False):
        self.nodes[id] = dict(id=id, cx=cx, top=top, name=name, icon=icon, role=role, tile=tile, wn=wide_name)

    def R(self, i): n = self.nodes[i]; return (n["cx"] + 36, n["top"] + 30)
    def L(self, i): n = self.nodes[i]; return (n["cx"] - 36, n["top"] + 30)
    def T(self, i): n = self.nodes[i]; return (n["cx"], n["top"] - 6)

    def B(self, i):
        n = self.nodes[i]                      # the box bottom grows with each extra role line
        return (n["cx"], n["top"] + 106 + 13 * (len(n["role"].split("\n")) - 1))

    # ---- groups: 24 px around the node boxes, label band on top, 150 px below ---------
    def group(self, members, label, cls, icon=None, icon_dark=None, plate=False):
        ns = [self.nodes[m] for m in members]
        half = lambda n: 76 if n["wn"] else 66
        left = min(n["cx"] - half(n) - 24 for n in ns)
        right = max(n["cx"] + half(n) + 24 for n in ns)
        top = min(n["top"] for n in ns) - 60
        bottom = max(n["top"] for n in ns) + 150
        self.groups.append(dict(l=left, t=top, w=right - left, h=bottom - top, label=label, cls=cls,
                                icon=icon, icon_dark=icon_dark, plate=plate, members=list(members)))

    # ---- edges ----------------------------------------------------------------------
    def poly(self, pts, cls="n", end=True, start=False, name=""):
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        m = (' marker-start="url(#ar)"' if start else "") + (' marker-end="url(#ar)"' if end else "")
        self.paths.append(f'<path class="{cls}" d="{d}"{m}/>')
        self.segs.append((name or cls, list(pts)))

    def label(self, x, y, text, accent=False, anchor="middle"):
        self.texts.append(f'<text class="{"el a" if accent else "el"}" x="{x}" y="{y}" text-anchor="{anchor}">{esc(text)}</text>')
        w = 6.8 * len(text)
        x0 = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.lbls.append((text, (x0, y - 10, x0 + w, y + 3)))

    def h(self, a, b, cls="e", text=None, both=False, dy=0, lx=None):
        (x1, y1), (x2, y2) = (self.R(a), self.L(b)) if self.nodes[b]["cx"] > self.nodes[a]["cx"] else (self.L(a), self.R(b))
        assert y1 == y2, f"h({a},{b}): the nodes are not on the same row"
        self.poly([(x1, y1), (x2, y2)], cls, start=both, name=f"{a}->{b}")
        if text:
            self.label(lx if lx is not None else (x1 + x2) // 2, y1 - 8 + dy, text, accent=(cls == "e"))

    def v(self, a, b, cls="e", text=None, both=False, ly=None):
        na, nb = self.nodes[a], self.nodes[b]
        assert na["cx"] == nb["cx"], f"v({a},{b}): the nodes are not in the same column"
        p1, p2 = (self.B(a), self.T(b)) if nb["top"] > na["top"] else (self.T(a), self.B(b))
        self.poly([p1, p2], cls, start=both, name=f"{a}->{b}")
        if text:
            self.label(p1[0] + 10, ly if ly is not None else (p1[1] + p2[1]) // 2 + 4, text, accent=(cls == "e"), anchor="start")

    # ---- checks: no arrow through text, no label on a border, nothing that wraps ----------
    def rects(self):
        out = []
        for n in self.nodes.values():
            cx, t = n["cx"], n["top"]
            out.append((n["id"], "tile", (cx - 30, t, cx + 30, t + 60)))
            wname = 7.6 * len(n["name"]) + 8
            out.append((n["id"], "name", (cx - wname / 2, t + 66, cx + wname / 2, t + 84)))
            lines = n["role"].split("\n")
            wrole = max(6.3 * len(l) for l in lines) + 8
            out.append((n["id"], "role", (cx - wrole / 2, t + 85, cx + wrole / 2, t + 85 + 13 * len(lines))))
        for g in self.groups:
            wl = 7.4 * len(g["label"]) + (24 if g["icon"] else 6)
            out.append((f"group:{g['label']}", "glabel", (g["l"] + 12, g["t"] + 9, g["l"] + 12 + wl, g["t"] + 31)))
        return out

    def check(self):
        warns, R = [], self.rects()
        hit = lambda a, b: a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
        for name, pts in self.segs:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                seg = (min(x1, x2), min(y1, y2), max(x1, x2) + 0.01, max(y1, y2) + 0.01)
                for owner, kind, r in R:
                    if hit(seg, r):
                        warns.append(f"[{self.key}] edge {name} crosses the {kind} of {owner}")
                for text, r in self.lbls:
                    if hit(seg, (r[0] + 1, r[1] + 2, r[2] - 1, r[3] - 2)):
                        warns.append(f"[{self.key}] edge {name} crosses the label '{text}'")
        for i, (t1, r1) in enumerate(self.lbls):
            for owner, kind, rr in R:
                if hit(r1, rr):
                    warns.append(f"[{self.key}] label '{t1}' overlaps the {kind} of {owner}")
            for t2, r2 in self.lbls[i + 1:]:
                if hit(r1, r2):
                    warns.append(f"[{self.key}] labels '{t1}' and '{t2}' overlap")
        for g in self.groups:
            l, t, r, b = g["l"], g["t"], g["l"] + g["w"], g["t"] + g["h"]
            for text, rc in self.lbls:
                for x0, y0, x1, y1 in ((l, t, r, t), (l, b, r, b), (l, t, l, b), (r, t, r, b)):
                    if hit(rc, (x0 - 1.5, y0 - 1.5, x1 + 1.5, y1 + 1.5)):
                        warns.append(f"[{self.key}] label '{text}' sits on the border of group {g['label']}")
                        break
        for n in self.nodes.values():               # text that would wrap: the arrows assume one line
            w = 152 if n["wn"] else 132
            if 7.4 * len(n["name"]) + 8 > w:
                warns.append(f"[{self.key}] name of {n['id']} may wrap ({len(n['name'])} characters)")
            for line in n["role"].split("\n"):
                if 6.08 * len(line) + 8 > w:
                    warns.append(f"[{self.key}] role line of {n['id']} may wrap: {line!r}")
        for g in self.groups:
            for nid, n in self.nodes.items():
                inside = g["l"] <= n["cx"] - 66 and n["cx"] + 66 <= g["l"] + g["w"] and g["t"] <= n["top"] and n["top"] + 100 <= g["t"] + g["h"]
                overlap = g["l"] < n["cx"] + 66 and n["cx"] - 66 < g["l"] + g["w"] and g["t"] < n["top"] + 100 and n["top"] < g["t"] + g["h"]
                if nid in g["members"] and not inside:
                    warns.append(f"[{self.key}] {nid} sticks out of group {g['label']}")
                if nid not in g["members"] and overlap:
                    warns.append(f"[{self.key}] {nid} overlaps group {g['label']} without being a member")
            if g["l"] < 0 or g["t"] < 0 or g["l"] + g["w"] > self.cw or g["t"] + g["h"] > self.ch:
                warns.append(f"[{self.key}] group {g['label']} is outside the canvas")
        for n in self.nodes.values():
            if n["cx"] - 66 < 0 or n["cx"] + 66 > self.cw or n["top"] + 105 > self.ch:
                warns.append(f"[{self.key}] node {n['id']} is outside the canvas")
        return warns

    def public_guard(self):
        """Refuse IP addresses, domain names, and any word of the private deny list in the figure."""
        texts = [self.title, self.lede, KICK, FOOT] + [t for _, t in self.legend]
        texts += [x for n in self.nodes.values() for x in (n["name"], n["role"])]
        texts += [g["label"] for g in self.groups] + [t for t, _ in self.lbls]
        rules = [FORBIDDEN, *private_words()]
        bad = sorted({m.group(0) for t in texts for rx in rules for m in [rx.search(t)] if m})
        if bad:
            raise SystemExit(f"[{self.key}] not public-safe, remove: {bad}")

    # ---- html -------------------------------------------------------------------------
    def node_html(self, n):
        w = 152 if n["wn"] else 132
        cls = "tile" + (" " + n["tile"] if n["tile"] else "")
        role = "<br>".join(esc(l) for l in n["role"].split("\n"))
        return (f'    <div class="node" style="left:{n["cx"] - w // 2}px;top:{n["top"]}px;width:{w}px">\n'
                f'      <div class="{cls}"><img src="%%ICON_{n["icon"]}%%" alt=""></div>\n'
                f'      <div class="name"><span>{esc(n["name"])}</span></div><div class="role"><span>{role}</span></div>\n'
                f'    </div>')

    def group_html(self, g):
        ic = ""
        if g["icon"] and g["icon_dark"]:
            ic = f'<img class="lt" src="%%ICON_{g["icon"]}%%" alt=""><img class="dk" src="%%ICON_{g["icon_dark"]}%%" alt="">'
        elif g["icon"]:
            ic = f'<img{" class=plate" if g["plate"] else ""} src="%%ICON_{g["icon"]}%%" alt="">'
        return (f'    <div class="group {g["cls"]}" style="left:{g["l"]}px; top:{g["t"]}px; width:{g["w"]}px; height:{g["h"]}px;">\n'
                f'      <div class="g-label">{ic}{esc(g["label"])}</div>\n    </div>')

    def render(self):
        t = TEMPLATE

        def sub(old, new):
            nonlocal t
            if t.count(old) != 1:
                raise SystemExit(f"the skill's template changed: anchor not found: {old[:70]!r}")
            t = t.replace(old, new)

        sub('<html lang="en"><!-- set lang to the label language, e.g. "ja" for Japanese labels -->', '<html lang="en">')
        sub("<title><!-- TITLE --></title>", f"<title>{esc(self.title)}</title>")
        sub("  --color-accent-tint: oklch(97% 0.012 256 / .5);\n", "  --color-accent-tint: oklch(97% 0.012 256 / .5);\n" + EXTRA_ROOT)
        sub("  --color-accent-tint: oklch(28% 0.045 256 / .35);\n", "  --color-accent-tint: oklch(28% 0.045 256 / .35);\n" + EXTRA_DARK)
        sub(".group.gb > .g-label { color: var(--color-accent); }\n", ".group.gb > .g-label { color: var(--color-accent); }\n" + EXTRA_CLASSES)
        sub(".canvas { position: relative; width: 1000px; height: 540px;", f".canvas {{ position: relative; width: {self.cw}px; height: {self.ch}px;")
        sub("svg.edges { position:absolute; inset:0; width:1000px; height:540px;", f"svg.edges {{ position:absolute; inset:0; width:{self.cw}px; height:{self.ch}px;")
        sub('<div class="kicker"><!-- KICKER: e.g. stack-diagram / author / 2026-08 --></div>', f'<div class="kicker">{esc(KICK)}</div>')
        sub("<h1><!-- TITLE --></h1>", f"<h1>{esc(self.title)}</h1>")
        sub('<p class="lede"><!-- LEDE: 1-2 short sentences, or delete this <p> --></p>', f'<p class="lede">{esc(self.lede)}</p>')

        start = t.index('<div class="canvas">') + len('<div class="canvas">')
        end = t.index('\n  </div>\n  </div>\n\n  <div class="legend">')
        defs = ('      <defs>\n        <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">\n'
                '          <path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/>\n        </marker>\n      </defs>\n')
        body = ("\n\n    <!-- GROUPS -->\n" + "\n".join(self.group_html(g) for g in self.groups) +
                f'\n\n    <!-- EDGES -->\n    <svg class="edges" viewBox="0 0 {self.cw} {self.ch}" aria-hidden="true">\n{defs}      '
                + "\n      ".join(self.paths + self.texts) + "\n    </svg>\n\n    <!-- NODES -->\n"
                + "\n".join(self.node_html(n) for n in self.nodes.values()) + "\n")
        t = t[:start] + body + t[end:]

        lg = "\n".join(f'    <span><span class="sw{(" " + c) if c else ""}"></span>{esc(txt)}</span>' for c, txt in self.legend)
        ls = t.index('<div class="legend">')
        le = t.index("</div>", t.index('<span><span class="sw n dash"></span>dev / deploy</span>')) + len("</div>")
        t = t[:ls] + f'<div class="legend">\n{lg}\n  </div>' + t[le:]

        used = sorted(set(re.findall(r"%%ICON_([A-Za-z0-9_-]+)%%", t)))
        srcs = []
        for u in used:
            base = u[:-5] if u.endswith("_dark") and u[:-5] in MANIFEST else u
            s = MANIFEST.get(base, {}).get("source")
            if s and SRC_NAMES.get(s, s) not in srcs:
                srcs.append(SRC_NAMES.get(s, s))
        sub("<span><!-- FOOT-LEFT: author / one-liner --></span>", f"<span>{esc(FOOT)}</span>")
        sub('<span><!-- FOOT-RIGHT: icon sources actually used, from manifest.json, e.g. "icons: svgl / devicon / Lucide" --> · made with',
            f"<span>icons: {esc(' / '.join(srcs))} · made with")
        return t


# ======================================================================================
# The figures. Roles stay under 20 characters per line (23 with wide_name) so nothing wraps.
# ======================================================================================
def fig_overview():
    f = Fig("01-overview", "Overview — NAS and services",
            "Two doors open to the internet, the Docker stacks behind them, the Hermes agent, and the backups.",
            900, 730, [("pub", "reachable from the internet"), ("", "public path (HTTPS)"),
                       ("n", "vpn · jobs · internal"), ("n dash", "backup · alerts")])
    R0, R1, R2, R3 = 50, 240, 390, 600
    f.node("browser", 150, R0, "Browser", "g_browser", "visitor")
    f.node("cf", 300, R0, "Cloudflare", "cloudflare", "dns + proxy", tile="wide")
    f.node("router", 450, R0, "Router", "router", "port forwarding")
    f.node("mobile", 600, R0, "Phone · Laptop", "g_device", "vpn clients")
    f.node("npm", 300, R1, "Nginx Proxy Manager", "npm", "reverse proxy", tile="pub", wide_name=True)
    f.node("wg", 600, R1, "WireGuard", "wireguard", "vpn + wstunnel", tile="pub")
    f.node("hermes", 150, R2, "Hermes agent", "hermes", "gateway + dashboard")
    f.node("docker", 450, R2, "Docker stacks", "docker", "4 Compose stacks")
    f.node("nas", 750, R2, "nas-backup", "clock", "restic · timers")
    f.node("discord", 150, R3, "Discord", "discord", "chat · alerts")
    f.node("gdrive", 750, R3, "Google Drive", "gdrive", "off-site copy")
    f.group(["npm", "wg", "hermes", "docker", "nas"], "UGREEN NASync", "ga", icon="hdd", plate=True)
    f.h("browser", "cf", "e", "HTTPS")
    f.h("cf", "router", "e", "443")
    f.h("mobile", "router", "n", "vpn")
    bx, by = f.B("router")
    lane = R1 - 22
    f.poly([(bx, by), (bx, lane)], "n", end=False, name="router-trunk")
    f.poly([(bx, lane), (300, lane), f.T("npm")], "e", name="router->npm")
    f.poly([(bx, lane), (600, lane), f.T("wg")], "n", name="router->wg")
    lane2 = R2 - 22
    f.poly([f.B("npm"), (300, lane2), (450, lane2), f.T("docker")], "e", name="npm->docker")
    f.poly([f.B("wg"), (600, lane2), (450, lane2)], "n", end=False, name="wg->docker")
    f.h("hermes", "docker", "n", "runs jobs")
    f.h("docker", "nas", "n dash", "backup")
    f.v("hermes", "discord", "n", "chat · alerts", both=True, ly=574)
    f.v("nas", "gdrive", "n dash", "encrypted copy", ly=574)
    return f


def fig_public_access():
    f = Fig("02-public-access", "Public access — HTTPS",
            "Cloudflare, then the home router, then Nginx Proxy Manager, which forwards to the published services.",
            860, 570, [("pub", "reachable from the internet"), ("", "public path (HTTPS)")])
    R0, R1, R2 = 50, 250, 390
    f.node("browser", 195, R0, "Browser", "g_browser", "visitor")
    f.node("cf", 345, R0, "Cloudflare", "cloudflare", "dns + proxy", tile="wide")
    f.node("router", 495, R0, "Router", "router", "nat · tcp 443")
    f.node("npm", 495, R1, "Nginx Proxy Manager", "npm", "reverse proxy · tls", tile="pub", wide_name=True)
    apps = [("vw", 270, "Vaultwarden", "vaultwarden", "passwords"),
            ("graf", 420, "Grafana", "grafana", "tesla dashboards"),
            ("aios", 570, "AIOStreams", "aiostreams", "stremio add-on"),
            ("aiom", 720, "AIOMetadata", "aiometadata", "stremio metadata")]
    for i, cx, nm, ic, ro in apps:
        f.node(i, cx, R2, nm, ic, ro, tile="pub")
    f.group(["npm"] + [a[0] for a in apps], "UGREEN NAS · Docker", "ga", icon="docker")
    f.h("browser", "cf", "e", "HTTPS")
    f.h("cf", "router", "e", "443")
    f.v("router", "npm", "e", "443")
    lane = R2 - 22
    bx, by = f.B("npm")
    xs = [a[1] for a in apps]
    f.poly([(bx, by), (bx, lane)], "e", end=False, name="npm-trunk")
    f.poly([(min(xs), lane), (max(xs), lane)], "e", end=False, name="npm-spine")
    for i, cx, *_ in apps:
        f.poly([(cx, lane), (cx, R2 - 6)], "e", name=f"npm->{i}")
    return f


def fig_private_access():
    f = Fig("03-private-access", "Private access — VPN and DNS",
            "WireGuard directly, or wrapped in HTTPS (wstunnel) when a network blocks UDP.",
            860, 560, [("pub", "reachable from the internet"), ("", "WireGuard direct (UDP)"),
                       ("n dash", "fallback over HTTPS (wstunnel)"), ("n", "internal")])
    R0, R1, R2 = 90, 230, 370
    f.node("phone", 100, R0, "Phone", "android", "wireguard", tile="wide")
    f.node("mac", 100, R2, "Laptop", "apple", "wstunnel + wg")
    f.node("router", 270, R1, "Router", "router", "nat · 51820 · 443")
    f.node("wg", 510, R0, "WireGuard", "wireguard", "vpn · host network", tile="pub")
    f.node("wst", 510, R1, "wstunnel", "wstunnel", "wg over wss · :8480")
    f.node("npm", 510, R2, "Nginx Proxy Manager", "npm", "reverse proxy", tile="pub", wide_name=True)
    f.node("apps", 700, R0, "Private apps", "server", "maison · frigate\nhermes dashboard")
    f.node("pihole", 700, R1, "Pi-hole", "pihole", "dns · lan + vpn")
    f.group(["wg", "wst", "npm", "apps", "pihole"], "UGREEN NAS · Docker", "ga", icon="docker")
    x_tr = 185                                         # phone and laptop merge into the router's left side
    f.poly([f.R("phone"), (x_tr, f.R("phone")[1]), (x_tr, f.L("router")[1]), f.L("router")], "e", name="phone->router")
    f.poly([f.R("mac"), (x_tr, f.R("mac")[1]), (x_tr, f.L("router")[1])], "n dash", end=False, name="mac->router")
    f.label(x_tr + 10, 180, "udp 51820", accent=True, anchor="start")
    f.label(160, f.R("mac")[1] + 20, "wss 443")
    rx, ry = f.R("router")                             # fork: up = UDP to WireGuard, down = TCP 443 to NPM
    x_fk = 372
    f.poly([(rx, ry), (x_fk, ry)], "n", end=False, name="router-trunk")
    f.poly([(x_fk, ry), (x_fk, f.L("wg")[1]), f.L("wg")], "e", name="router->wg")
    f.poly([(x_fk, ry), (x_fk, f.L("npm")[1]), f.L("npm")], "n dash", name="router->npm")
    f.v("npm", "wst", "n dash", "ws")
    f.v("wst", "wg", "n dash", "local udp")
    wx, wy = f.R("wg")                                 # WireGuard reaches the private apps and Pi-hole
    x_f2 = 608
    f.poly([(wx, wy), (x_f2, wy)], "n", end=False, name="wg-trunk")
    f.poly([(x_f2, wy), f.L("apps")], "n", name="wg->apps")
    f.poly([(x_f2, wy), (x_f2, f.L("pihole")[1]), f.L("pihole")], "n", name="wg->pihole")
    f.label(640, wy - 8, "access")
    f.label(640, f.L("pihole")[1] - 8, "dns")
    return f


def fig_containers():
    f = Fig("04-containers", "Docker containers — four stacks",
            "One Compose stack per folder; every database lives in common-postgres.",
            980, 880, [("pub", "reachable from the internet"), ("", "database access (common-network)"),
                       ("n", "link between services")])
    R0, R1, R2, R3 = 80, 330, 560, 700
    f.node("mosq", 130, R0, "Mosquitto", "mosquitto", "mqtt broker")
    f.node("tm", 280, R0, "TeslaMate", "teslamate", "car tracking · :4000")
    f.node("graf", 430, R0, "Grafana", "grafana", "dashboards · :3000", tile="pub")
    f.node("web", 640, R0, "maison-web", "house", "web ui · :8090")
    f.node("coll", 790, R0, "maison-collector", "radar", "listings crawler")
    f.node("redis", 385, R1, "Redis", "redis", "cache · aof")
    f.node("pg", 535, R1, "common-postgres", "postgresql", "postgres 17 · :5000")
    row2 = [("frig", 235, "Frigate", "frigate", "nvr · :8971", ""),
            ("aiom", 385, "AIOMetadata", "aiometadata", "stremio · :3232", "pub"),
            ("aios", 535, "AIOStreams", "aiostreams", "stremio · :1080", "pub"),
            ("strem", 685, "StremThru", "waypoints", "debrid gateway", ""),
            ("vw", 835, "Vaultwarden", "vaultwarden", "passwords · :83", "pub")]
    row3 = [("npm", 235, "Nginx Proxy Manager", "npm", "reverse proxy · :443", "pub"),
            ("pihole", 385, "Pi-hole", "pihole", "dns · macvlan", ""),
            ("wst", 535, "wstunnel", "wstunnel", "wg over wss · :8480", ""),
            ("wg", 685, "WireGuard", "wireguard", "vpn · host network", "pub"),
            ("ptz", 835, "PTZ preset", "python", "cam presets · :8095", "")]
    for i, cx, nm, ic, ro, tl in row2:
        f.node(i, cx, R2, nm, ic, ro, tile=tl)
    for i, cx, nm, ic, ro, tl in row3:
        f.node(i, cx, R3, nm, ic, ro, tile=tl, wide_name=(i == "npm"))
    f.group(["mosq", "tm", "graf"], "teslamate", "gb", icon="teslamate", plate=True)
    f.group(["web", "coll"], "maison", "gd", icon="house", plate=True)
    f.group(["redis", "pg"], "common", "gc", icon="postgresql", plate=True)
    f.group([r[0] for r in row2 + row3], "home-server", "ga", icon="docker")
    lane = 252                                         # consumers -> postgres (bus)
    pgx, pgy = f.T("pg")
    for s in ("tm", "graf", "web", "coll"):
        f.poly([f.B(s), (f.nodes[s]["cx"], lane)], "e", end=False, name=f"{s}-drop")
    f.poly([(f.nodes["tm"]["cx"], lane), (f.nodes["coll"]["cx"], lane)], "e", end=False, name="db-bus")
    f.poly([(pgx, lane), (pgx, pgy)], "e", name="bus->pg")
    f.label(pgx + 10, 290, "sql", accent=True, anchor="start")
    f.h("tm", "mosq", "n", "mqtt")
    f.poly([f.T("aios"), f.B("pg")], "e", name="aios->pg")
    f.label(545, 492, "sql", accent=True, anchor="start")
    l2 = R2 - 35
    f.poly([f.T("aiom"), (385, l2), (535, l2)], "e", end=False, name="aiom->pg")
    f.poly([f.T("aiom"), f.B("redis")], "n", name="aiom->redis")
    f.label(395, 492, "cache", anchor="start")
    f.h("aios", "strem", "n", "debrid")
    f.h("wst", "wg", "n", "udp")
    return f


def fig_hermes():
    f = Fig("05-hermes-agent", "Hermes agent",
            "The gateway answers Discord and runs the scheduled jobs; the dashboard stays on the LAN and the VPN.",
            860, 600, [("", "messages (Discord)"), ("n", "calls and jobs")])
    R0, R1, R2 = 30, 250, 450
    f.node("discord", 110, R1, "Discord", "discord", "chat · alerts")
    f.node("gw", 330, R1, "hermes-gateway", "hermes", "bot + scheduler")
    f.node("cron", 480, R1, "Scheduled jobs", "caltime", "hourly to monthly")
    f.node("dash", 630, R1, "hermes-dashboard", "hermes", "web ui · :9119")
    f.node("maison", 480, R0, "maison", "house", "4 scheduled jobs")
    f.node("eurl", 630, R0, "eurl-frais", "docker", "cli · 10th of month")
    f.node("llm", 330, R2, "LLM APIs", "cpu", "openai · claude")
    f.node("mcp", 480, R2, "MCP", "globe", "cloudflare\ncontext7")
    f.node("herald", 630, R2, "Herald", "android", "android app", tile="wide")
    f.group(["gw", "cron", "dash"], "Hermes agent · systemd", "gd", icon="hermes", icon_dark="hermes_dark")
    f.h("discord", "gw", "e", "messages", both=True, lx=190)
    f.h("gw", "cron", "n", "schedules")
    lane = 163
    cx_, cy_ = f.T("cron")
    f.poly([(cx_, cy_), f.B("maison")], "n", name="cron->maison")
    f.poly([(cx_, lane), (630, lane), f.B("eurl")], "n", name="cron->eurl")
    f.label(470, 168, "enriches · monitors", anchor="end")
    f.label(555, 155, "10th")
    gx, gy = f.B("gw")
    l2 = 422
    f.poly([(gx, gy), f.T("llm")], "n", name="gw->llm")
    f.poly([(gx, l2), (480, l2), f.T("mcp")], "n", name="gw->mcp")
    f.label(320, 412, "api", anchor="end")
    f.poly([f.T("herald"), f.B("dash")], "n", name="herald->dash")
    f.label(640, 432, "lan / vpn", anchor="start")
    return f


def fig_backups():
    f = Fig("06-backups", "Encrypted backups",
            "Encrypted restic copies on the RAID and on Google Drive, checked every week.",
            1010, 660, [("", "backup path"), ("n", "sources"), ("n dash", "alerts · sealed config")])
    R0, R1, R2 = 80, 290, 500
    f.node("vol1", 120, R0, "volume1 · SSD", "hdd", "docker · .env · vms")
    f.node("pg", 270, R0, "PostgreSQL", "postgresql", "pg_dump per db")
    f.node("vol2", 420, R0, "volume2 · RAID 5", "hdd", "documents · photos")
    f.node("hermes", 570, R0, "Hermes", "hermes", "~/.hermes\nnotes vault")
    f.node("age", 720, R0, "age", "age", "hermes-vault", tile="wide")
    f.node("github", 900, R0, "GitHub", "github", "sealed config")
    f.node("nas", 270, R1, "nas-backup", "clock", "systemd timers")
    f.node("restic", 420, R1, "restic", "restic", "RAID · 14d/8w/12m")
    f.node("rclone", 570, R1, "rclone", "rclone", "restic backend")
    f.node("gdrive", 900, R1, "Google Drive", "gdrive", "off-site copy")
    f.node("discord", 270, R2, "Discord", "discord", "alerts · recap")
    f.group(["vol1", "pg", "vol2", "hermes", "age", "nas", "restic", "rclone"], "UGREEN NAS", "gc", icon="hdd", plate=True)
    lane = 222                                         # sources -> nas-backup (bus)
    for s in ("vol1", "vol2", "hermes"):
        f.poly([f.B(s), (f.nodes[s]["cx"], lane)], "n", end=False, name=f"{s}-drop")
    f.poly([(120, lane), (570, lane)], "n", end=False, name="bus")
    f.poly([f.B("pg"), f.T("nas")], "n", name="pg->nas")
    f.h("nas", "restic", "e", "nightly")
    f.h("restic", "rclone", "e", "copy")
    f.h("rclone", "gdrive", "e", "encrypted (restic)")
    f.h("hermes", "age", "n dash", "seal")
    f.h("age", "github", "n dash", "push", lx=838)
    f.v("nas", "discord", "n dash", "alerts", ly=470)
    return f


def fig_storage():
    f = Fig("07-storage", "Storage",
            "Where the data lives, and how it leaves the NAS.",
            920, 560, [("", "copy · sync to the cloud"), ("n", "local")])
    R0, R1, R2 = 80, 250, 430
    f.node("vols", 130, R0, "Volumes & VMs", "db", "@docker · @kvm")
    f.node("dockerdir", 130, R1, "/volume1/docker", "docker", "this repository\napp data · .env")
    f.node("home", 420, R0, "/home", "hdd", "~/.hermes\nphoto archive")
    f.node("docs", 600, R0, "CLOUD_NAS", "archive", "documents · notes")
    f.node("repo", 420, R1, "@nas-backup", "restic", "restic · staging")
    f.node("photos", 600, R1, "Photos", "image", "+ shared photos")
    f.node("github", 130, R2, "GitHub", "github", "no secrets, ever")
    f.node("onedrive", 830, R0, "OneDrive", "onedrive", "document sync", tile="wide")
    f.node("gdrive", 830, R1, "Google Drive", "gdrive", "encrypted restic")
    f.group(["vols", "dockerdir"], "volume1 · NVMe SSD", "ga", icon="hdd", plate=True)
    f.group(["home", "docs", "repo", "photos"], "volume2 · RAID 5, 4 disks", "gc", icon="hdd", plate=True)
    ry = f.R("dockerdir")[1]                           # volume1 -> restic repository (nightly)
    f.poly([f.R("dockerdir"), f.L("repo")], "n", name="dockerdir->repo")
    f.poly([f.R("vols"), (250, f.R("vols")[1]), (250, ry)], "n", end=False, name="vols->repo")
    f.label(262, 200, "nightly", anchor="start")
    f.v("home", "repo", "n", "nightly")
    f.h("docs", "onedrive", "e", "sync", lx=742)
    f.h("photos", "gdrive", "e", "monthly", lx=742)
    bx, by = f.B("repo")                               # repository -> Google Drive, under the Photos node
    f.poly([(bx, by), (bx, 385), (830, 385), f.B("gdrive")], "e", name="repo->gdrive")
    f.label(760, 377, "restic", accent=True)
    f.v("dockerdir", "github", "e", "git push", ly=418)
    return f


FIGURES = (fig_overview, fig_public_access, fig_private_access, fig_containers, fig_hermes, fig_backups, fig_storage)


# ======================================================================================
def shrink_pngs(icons, limit=100_000, side=192):
    """Resize (never recolor) PNG logos over 100 kB: two upstream logos weigh 1.4 MB each."""
    try:
        from PIL import Image
    except ImportError:
        print("note: Pillow is missing, large PNG logos are inlined at full size")
        return
    for p in sorted(icons.glob("*.png")):
        if p.stat().st_size > limit:
            im = Image.open(p)
            im.load()
            im.thumbnail((side, side), Image.LANCZOS)
            im.save(p, optimize=True)


def ensure_icons(skill, work, refresh):
    icons = work / "icons"
    specs = [l.strip() for l in (HERE / "icons.txt").read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if refresh or not (icons / "manifest.json").exists():
        icons.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-I", str(skill / "scripts" / "fetch_icons.py"), str(icons), *specs], check=True)
    for name in ("browser", "device"):                 # generic glyphs shipped with the skill
        shutil.copyfile(skill / "assets" / "generic" / f"{name}.svg", icons / f"g_{name}.svg")
    shrink_pngs(icons)
    return icons


def main():
    global TEMPLATE, MANIFEST
    ap = argparse.ArgumentParser(description="Build the public diagrams with the stack-diagram skill.")
    ap.add_argument("--skill", required=True, type=pathlib.Path, help="skills/stack-diagram folder of the skill")
    ap.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK, help=f"work folder (default {DEFAULT_WORK})")
    ap.add_argument("--refresh-icons", action="store_true", help="download the logos again")
    a = ap.parse_args()
    skill, work = a.skill.expanduser().resolve(), a.work.expanduser().resolve()
    for need in ("scripts/fetch_icons.py", "scripts/build.py", "assets/template.html", "assets/fonts", "assets/generic"):
        if not (skill / need).exists():
            raise SystemExit(f"{skill} is not the skills/stack-diagram folder of the skill (missing {need})")
    TEMPLATE = (skill / "assets" / "template.html").read_text(encoding="utf-8")
    icons = ensure_icons(skill, work, a.refresh_icons)
    MANIFEST = json.loads((icons / "manifest.json").read_text())

    draft, built = work / "draft", work / "html"
    for d in (draft, built):
        d.mkdir(parents=True, exist_ok=True)
    warnings = []
    for make in FIGURES:
        f = make()
        f.public_guard()
        warnings += f.check()
        page = f.render()
        missing = sorted(t for t in set(re.findall(r"%%ICON_([A-Za-z0-9_-]+)%%", page)) if not list(icons.glob(f"{t}.*")))
        if missing:
            raise SystemExit(f"[{f.key}] no logo file for {missing}: add it to icons.txt")
        (draft / f"{f.key}.html").write_text(page, encoding="utf-8")
        subprocess.run([sys.executable, "-I", str(skill / "scripts" / "build.py"), str(draft / f"{f.key}.html"),
                        str(icons), str(built / f"{f.key}.html"), "--fonts", str(skill / "assets" / "fonts")],
                       check=True, stdout=subprocess.DEVNULL)
        print(f"built {built / (f.key + '.html')}  ({len(f.nodes)} nodes, {len(f.groups)} groups)")
    for w in warnings:
        print("LAYOUT:", w)
    if warnings:
        raise SystemExit(f"{len(warnings)} layout warning(s): fix the coordinates in build.py")
    print("no layout warning; next: python3 -I render.py --skill", skill)


if __name__ == "__main__":
    main()
