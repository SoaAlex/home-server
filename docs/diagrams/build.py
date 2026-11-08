#!/usr/bin/env python3
"""Build docs/diagrams/infrastructure.html: the public (redacted) infrastructure diagrams.

Six hand-drawn figures (overview, network access, containers, Hermes agent, backups,
storage). This public version leaves out LAN addresses, VPN peers, and known weak spots;
the full version lives outside the repository.

Usage: python3 -I build.py [OUT.html]   (default: infrastructure.html next to this file)
Render the PNGs with render.py (see README.md in this folder).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sketch import INK, MONO, MUTED, PAPER, SANS, SERIF, SOFT, Fig, e  # noqa: E402

LEG_EXPO = [("PUBLIC", "reachable from the internet"), ("PRIVATE", "LAN + VPN only")]
figs = []

# 1. Overview ----------------------------------------------------------------
f = Fig("overview", "Overview: what the internet can reach",
        "Only two doors of the NAS are open to the internet: the NPM reverse proxy behind Cloudflare, and WireGuard; private apps, the Hermes agent, and the backups stay behind the router.")
f.zone(20, 64, 1240, 128, "INTERNET")
f.zone(276, 224, 200, 288, "OPEN DOORS", accent=True)
f.zone(532, 224, 456, 288, "NAS · BEHIND THE ROUTER", right=True)
f.arrow([(200, 136), (296, 136)], "link")
f.arrow([(376, 176), (376, 256)], "link")
f.arrow([(120, 176), (120, 440), (296, 440)])
f.arrow([(456, 296), (552, 296)])
f.arrow([(456, 440), (552, 440)])
f.arrow([(808, 296), (712, 296)])
f.arrow([(808, 440), (712, 440)])
f.arrow([(968, 296), (1104, 296), (1104, 176)], "link")
f.arrow([(968, 440), (1160, 440), (1160, 176)], "link")
f.forbid((680, 192), (680, 216))
f.label(248, 124, "HTTPS", "link")
f.label(384, 220, "TCP 443", "link", anchor="start")
f.label(128, 300, "UDP 51820 · WSS", anchor="start")
f.label(504, 284, "PROXY")
f.label(504, 428, "TUNNEL")
f.label(760, 284, "DUMPS")
f.label(760, 428, "API · CLI")
f.label(1096, 236, "ENCRYPTED", "link", anchor="end")
f.label(1168, 236, "LLM · BOT", "link", anchor="start")
f.label(692, 207, "NO OTHER PORT", "accent", anchor="start")
f.node("cli", 40, 96, 160, 80, "input", "USER", "Clients", "web · laptop · phone")
f.node("cf", 296, 96, 160, 80, "external", "DNS", "Cloudflare", "proxied DNS")
f.node("cloud", 1024, 96, 200, 80, "external", "SaaS", "Cloud services", "Discord · Drive · LLM")
f.node("npm", 296, 256, 160, 80, "backend", "EDGE", "Nginx Proxy Mgr", ":443 · Let's Encrypt", expo="PUBLIC")
f.node("wg", 296, 400, 160, 80, "backend", "VPN", "WireGuard", "udp 51820 · wss", expo="PUBLIC")
f.node("pub", 552, 256, 160, 80, "backend", "OCI", "Published apps", "vaultwarden · grafana", "aiostreams · aiometa", expo="PUBLIC")
f.node("priv", 552, 400, 160, 80, "backend", "OCI", "Private apps", "maison · code-server", "frigate · pi-hole", expo="PRIVATE")
f.node("bkp", 808, 256, 160, 80, "store", "BKP", "nas-backup", "restic · 04:00", expo="PRIVATE")
f.node("her", 808, 400, 160, 80, "backend", "AGENT", "Hermes agent", "gateway · dashboard", expo="PRIVATE")
f.callout(1240, 560, ["Tailscale is off on purpose:", "remote access uses WireGuard or wss"], "M 1080 552 Q 760 560 456 470", (456, 470), anchor="end")
figs.append(("01-overview", "Overview", "Two doors open to the internet; everything else stays behind the router.",
             f.svg(LEG_EXPO + [("zone-accent", "open door"), ("forbid", "blocked"), ("arrow:link", "HTTP / API"), ("arrow:muted", "internal flow")])))

# 2. Network access ----------------------------------------------------------
f = Fig("access", "Network access",
        "Ways in: HTTPS through Cloudflare and the router to NPM, WireGuard directly from a phone or wrapped in wstunnel from a network that blocks UDP, and Pi-hole DNS on the LAN.")
f.zone(20, 64, 460, 128, "INTERNET")
f.zone(532, 200, 476, 408, "NAS", right=True)
f.arrow([(200, 136), (296, 136)], "link")
f.arrow([(376, 176), (376, 232)], "link")
f.arrow([(456, 272), (552, 272)], "link")
f.arrow([(712, 272), (808, 272)])
f.arrow([(888, 312), (888, 408), (712, 408)])
f.arrow([(200, 408), (352, 408), (352, 312)])
f.arrow([(400, 312), (400, 424), (552, 424)])
f.arrow([(632, 448), (632, 504)])
f.arrow([(200, 544), (552, 544)])
f.forbid((456, 244), (524, 244), horizontal=True)
f.label(248, 124, "HTTPS", "link")
f.label(384, 208, "PROXIED · DNS", "link", anchor="start")
f.label(504, 262, "TCP 443", "link")
f.label(490, 233, "OTHERS", "accent")
f.label(760, 260, "WSS :8480")
f.label(896, 360, "LOCAL UDP", anchor="start")
f.label(276, 396, "UDP 51820")
f.label(476, 412, "NAT UDP")
f.label(640, 480, "PEER DNS", anchor="start")
f.label(376, 532, "DNS")
f.node("cli", 40, 96, 160, 80, "input", "USER", "HTTPS clients", "browser · office laptop")
f.node("cf", 296, 96, 160, 80, "external", "DNS", "Cloudflare", "vpn. = DNS only")
f.node("box", 296, 232, 160, 80, "external", "NAT", "Home router", "443 tcp/udp · 51820")
f.node("phone", 40, 368, 160, 80, "input", "USER", "Phone", "WireGuard app")
f.node("pc", 40, 504, 160, 80, "input", "USER", "Home PC", "LAN")
f.node("npm", 552, 232, 160, 80, "focal", "EDGE", "Nginx Proxy Mgr", "proxy hosts · certs", expo="PUBLIC")
f.node("wst", 808, 232, 160, 80, "backend", "TUN", "wstunnel", "host :8480")
f.node("wg", 552, 368, 160, 80, "focal", "VPN", "WireGuard", "host network", expo="PUBLIC")
f.node("pih", 552, 504, 160, 80, "backend", "DNS", "Pi-hole", "macvlan · ad-block", expo="PRIVATE")
f.callout(1240, 108, ["Where UDP is blocked, the laptop", "wraps WireGuard in HTTPS (wstunnel)"], "M 1180 130 Q 1100 180 968 260", (968, 260), anchor="end")
f.callout(560, 104, ["Admin UIs (NPM, maison, Hermes", "dashboard) never get a public host"], "M 600 128 Q 590 190 528 236", (528, 236), accent=True)
figs.append(("02-network-access", "Network access", "The paths into the NAS, and the ones that stop at the router.",
             f.svg(LEG_EXPO + [("focal", "open door"), ("forbid", "blocked at the router"), ("arrow:link", "HTTPS / DNS"), ("arrow:muted", "internal flow")])))

# 3. Containers --------------------------------------------------------------
f = Fig("containers", "Docker containers by exposure",
        "The NAS containers grouped by exposure: three services published behind NPM, four kept to the LAN and VPN, and the shared Postgres database.")
f.zone(276, 64, 200, 544, "PUBLISHED VIA NPM", accent=True)
f.zone(788, 64, 472, 272, "LAN + VPN ONLY")
f.zone(532, 472, 200, 136, "DATA · COMMON-NETWORK")
f.arrow([(200, 252), (240, 252), (240, 136), (296, 136)])
f.arrow([(200, 272), (296, 272)])
f.arrow([(200, 292), (256, 292), (256, 408), (296, 408)])
f.arrow([(456, 272), (664, 272), (664, 504)])
f.arrow([(456, 408), (616, 408), (616, 504)])
f.arrow([(848, 312), (848, 552), (712, 552)])
f.label(672, 440, "SQL", anchor="start")
f.label(560, 396, "SQL")
f.label(780, 540, "SQL")
f.node("npm", 40, 232, 160, 80, "focal", "EDGE", "Nginx Proxy Mgr", "proxy hosts + certs", expo="PUBLIC")
f.node("vw", 296, 96, 160, 80, "backend", "OCI", "Vaultwarden", ":83 · ws :3012", expo="PUBLIC")
f.node("aio", 296, 232, 160, 80, "backend", "OCI", "AIOStreams", "+ aiometadata", ":1080 · :3232", expo="PUBLIC")
f.node("tm", 296, 368, 160, 80, "backend", "OCI", "TeslaMate", "+ Grafana :3000", "+ Mosquitto :1883", expo="PUBLIC")
f.node("pg", 552, 504, 160, 80, "store", "SQL", "common-postgres", "Postgres 17", "+ Redis", expo="PRIVATE")
f.node("fr", 808, 96, 160, 80, "backend", "OCI", "Frigate", ":8971 · + ptz :8095", expo="PRIVATE")
f.node("cs", 1064, 96, 160, 80, "backend", "OCI", "code-server", ":8443", expo="PRIVATE")
f.node("ma", 808, 232, 160, 80, "backend", "OCI", "maison", "web :8090", "+ collector", expo="PRIVATE")
f.node("ph", 1064, 232, 160, 80, "backend", "DNS", "Pi-hole", "macvlan", expo="PRIVATE")
f.callout(1240, 420, ["On demand: eurl-frais, nas-backup,", "hermes-dockercli"], "M 1047 414 Q 1044 378 1040 340", (1040, 340), anchor="end")
figs.append(("03-containers", "Docker containers", "17 running containers, grouped by who can reach them.",
             f.svg(LEG_EXPO + [("zone-accent", "published zone"), ("focal", "entry point"), ("store", "data"), ("arrow:muted", "internal flow")])))

# 4. Hermes agent ------------------------------------------------------------
f = Fig("hermes", "Hermes agent",
        "Hermes runs on the host: the gateway takes Discord messages and fires the scheduled jobs, the dashboard serves the Herald app over LAN and VPN, and memory lives in an Obsidian vault.")
f.zone(20, 64, 460, 128, "INTERNET")
f.zone(276, 200, 732, 408, "NAS · SYSTEMD USER SERVICES", right=True)
f.arrow([(200, 136), (296, 136)], "link")
f.arrow([(376, 176), (376, 232)], "link")
f.arrow([(120, 176), (120, 408), (296, 408)])
f.arrow([(456, 272), (552, 272)])
f.arrow([(752, 272), (808, 272)])
f.arrow([(652, 232), (652, 176)], "link")
f.arrow([(652, 376), (652, 504)])
f.arrow([(752, 544), (808, 544)], "link", dashed=True)
f.forbid((376, 600), (376, 456))
f.label(248, 124, "MESSAGES", "link")
f.label(384, 189, "BOT", "link", anchor="start")
f.label(128, 300, "HERALD · LAN/WG", anchor="start")
f.label(504, 260, "TICK")
f.label(780, 260, "API")
f.label(660, 208, "LLM · MCP", "link", anchor="start")
f.label(660, 440, "MEMORY", anchor="start")
f.label(780, 532, "AGE", "link")
f.label(384, 520, "INTERNET", "accent", anchor="start")
f.node("me", 40, 96, 160, 80, "input", "USER", "Owner", "Discord · desktop")
f.node("dc", 296, 96, 160, 80, "external", "SaaS", "Discord", "bot · private server")
f.node("api", 552, 96, 200, 80, "external", "API", "External APIs", "Claude · OpenAI", "MCP cloudflare · ctx7")
f.node("gw", 296, 232, 160, 80, "backend", "SVC", "hermes-gateway", "Discord · cron", expo="PRIVATE")
f.node("dash", 296, 368, 160, 80, "focal", "SVC", "dashboard", ":9119 · basic auth", "terminal on the NAS", expo="PRIVATE")
f.node("cron", 552, 232, 200, 144, "backend", "CRON", "Scheduled jobs", "maison enrich · 2 h", "addresses · 30 min", "maison-health · 07:30", "eurl-frais · 10th", "one-off reminders")
f.node("svc", 808, 232, 160, 80, "backend", "CLI", "NAS tools", "maison · eurl-frais", "nas-backup · dk")
f.node("vault", 552, 504, 200, 80, "store", "FS", "~/.hermes + vault", "config · skills", "Obsidian memory")
f.node("gh", 808, 504, 160, 80, "external", "GIT", "GitHub", "hermes-vault .age")
f.callout(1240, 128, ["Only the owner holds the age key", "that decrypts the config"], "M 1150 150 Q 1080 400 968 540", (968, 540), anchor="end")
figs.append(("04-hermes-agent", "Hermes agent", "What talks to Hermes, and what Hermes drives.",
             f.svg([("PRIVATE", "LAN + VPN only"), ("focal", "sensitive access"), ("forbid", "blocked"), ("arrow:link", "external API"), ("arrow:muted", "internal flow"), ("arrow:dash", "encrypted copy")])))

# 5. Backups -----------------------------------------------------------------
f = Fig("backups", "Backups",
        "Databases, configuration, and Hermes go every night into an encrypted restic repository on the RAID and are copied to Google Drive; Sunday checks and test-restores; the 1st of the month uploads the whole RAID.")
f.zone(20, 64, 200, 544, "SOURCES")
f.zone(580, 128, 240, 144, "@NAS-BACKUP · OUTSIDE SMB SHARES")
f.arrow([(200, 136), (248, 136), (248, 160), (296, 160)])
f.arrow([(200, 272), (264, 272), (264, 200), (296, 200)])
f.arrow([(200, 408), (280, 408), (280, 240), (296, 240)])
f.arrow([(200, 544), (296, 544)])
f.arrow([(496, 200), (600, 200)])
f.arrow([(496, 368), (700, 368), (700, 240)])
f.arrow([(800, 200), (1036, 200), (1036, 320)], "link")
f.arrow([(496, 544), (1036, 544), (1036, 416)], "link")
f.label(548, 188, "RESTIC")
f.label(600, 356, "CHECK · PRUNE")
f.label(918, 188, "NIGHTLY COPY", "link")
f.label(766, 532, "1ST OF MONTH", "link")
f.node("pg", 40, 96, 160, 80, "store", "SQL", "Postgres", "pg_dump -Fc per db", "→ TechnicalBackups")
f.node("sq", 40, 232, 160, 80, "store", "DB", "App SQLite DBs", "vaultwarden · npm", "pi-hole · grafana")
f.node("fs", 40, 368, 160, 80, "store", "FS", "Config & Hermes", "volume1 · VMs", "~/.hermes · vault")
f.node("raid", 40, 504, 160, 80, "store", "FS", "RAID data", "photos · homes", "documents")
f.node("night", 296, 120, 200, 160, "backend", "TIMER", "Every night", "04:00", "dumps + .backup", "restic → RAID", "copy → Drive")
f.node("week", 296, 328, 200, 80, "backend", "TIMER", "Every Sunday", "05:30 · check 1/8", "test restore")
f.node("month", 296, 504, 200, 80, "backend", "TIMER", "1st of the month", "01:00 · whole RAID", "resumable upload")
f.node("repo", 600, 160, 200, 80, "focal", "REPO", "restic repository", "encrypted · 14d/8w/12m")
f.node("gd", 936, 320, 200, 96, "focal", "S3", "Google Drive", "restic repository #2", "encrypted · drive.file")
f.callout(1240, 470, ["Failures and the weekly", "recap: Discord webhook"], "M 1160 486 Q 880 470 496 380", (496, 380), anchor="end")
figs.append(("05-backups", "Backups", "Everything that leaves the NAS for backup is encrypted by restic.",
             f.svg([("focal", "encrypted"), ("store", "source"), ("backend", "job"), ("arrow:link", "to Google Drive"), ("arrow:muted", "local")])))

# 6. Storage -----------------------------------------------------------------
f = Fig("storage", "Storage",
        "The volume1 SSD holds Docker and the VMs, the volume2 RAID 5 holds documents, photos, home folders, and the backup repository; backups leave encrypted for Google Drive.")
f.zone(20, 128, 200, 272, "VOLUME1 · NVME SSD")
f.zone(276, 64, 480, 544, "VOLUME2 · RAID 5 · 4 DISKS")
f.arrow([(200, 200), (248, 200), (248, 256), (296, 256)])
f.arrow([(200, 336), (248, 336), (248, 288), (296, 288)])
f.arrow([(552, 272), (456, 272)])
f.arrow([(120, 376), (120, 504)], "link")
f.arrow([(712, 136), (1064, 136)], "link")
f.arrow([(376, 312), (376, 568), (1144, 568), (1144, 448)], "link")
f.arrow([(712, 408), (1064, 408)], "link")
f.label(128, 440, "GIT PUSH", "link", anchor="start")
f.label(888, 124, "DOCUMENT SYNC", "link")
f.label(504, 260, "NIGHTLY")
f.label(760, 556, "RESTIC", "link")
f.label(888, 396, "MONTHLY", "link")
f.node("kvm", 40, 160, 160, 80, "store", "FS", "@docker · @kvm", "volumes · VMs")
f.node("dock", 40, 296, 160, 80, "store", "FS", "/volume1/docker", "this repository", "app data · .env")
f.node("gh", 40, 504, 160, 80, "external", "GIT", "GitHub", "no secrets, ever")
f.node("nb", 296, 232, 160, 80, "focal", "REPO", "@nas-backup", "restic · staging")
f.node("cn", 552, 96, 160, 80, "store", "FS", "CLOUD_NAS", "documents · vault")
f.node("home", 552, 232, 160, 80, "store", "FS", "/home", "~/.hermes", "photo archive")
f.node("ph", 552, 368, 160, 80, "store", "FS", "Photos", "+ shared photos")
f.node("od", 1064, 96, 160, 80, "external", "SaaS", "OneDrive", "document sync")
f.node("gd", 1064, 368, 160, 80, "focal", "S3", "Google Drive", "encrypted restic")
f.callout(1240, 260, ["UGOS runs from eMMC; volume1", "is backed up nightly to the RAID"], "M 1000 276 Q 600 360 210 300", (210, 300), anchor="end")
figs.append(("06-storage", "Storage", "Where the data lives, and how it leaves the NAS.",
             f.svg([("focal", "backup repository"), ("store", "storage"), ("external", "cloud"), ("arrow:link", "copy / sync"), ("arrow:muted", "local")])))

# Page -----------------------------------------------------------------------
sections = "".join(
    f'<section id="{slug}"><p class="eyebrow">{i + 1:02d} / {len(figs):02d}</p><h2>{e(h)}</h2>'
    f'<p class="subtitle">{e(sub)}</p><div class="diagram">{svg}</div></section>'
    for i, (slug, h, sub, svg) in enumerate(figs))
page = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Home server infrastructure</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:{SANS};background:{PAPER};color:{INK};padding:3rem 2rem}}
.container{{max-width:1360px;margin:0 auto}}
.eyebrow{{font-family:{MONO};font-size:.66rem;font-weight:500;letter-spacing:.18em;text-transform:uppercase;color:{MUTED};margin-bottom:.6rem}}
h1{{font-family:{SERIF};font-size:clamp(1.75rem,3vw + 1rem,2.6rem);font-weight:400;letter-spacing:-.02em;line-height:1.1;margin-bottom:.5rem}}
h2{{font-family:{SERIF};font-size:1.9rem;font-weight:400;margin-bottom:.3rem}}
.subtitle{{font-size:1rem;line-height:1.55;color:{MUTED};max-width:72ch}}
section{{margin-top:3.5rem}}
.diagram{{overflow-x:auto;margin-top:1rem}}
svg{{width:100%;min-width:1280px;display:block}}
.footer{{margin-top:3rem;padding-top:1.5rem;border-top:1px solid rgba(45,49,66,.10);font-family:{MONO};font-size:.72rem;color:{SOFT}}}
@media print{{svg{{min-width:0}}.diagram{{overflow-x:visible}}}}
</style></head><body><div class="container">
<p class="eyebrow">Home server · infrastructure</p>
<h1>What runs on the NAS, and what the internet can reach</h1>
<p class="subtitle">An overview, then five areas in detail. Orange marks what the internet can reach; the black PUBLIC badge marks services reachable from outside, the dashed PRIVATE badge services kept to the LAN and the VPN.</p>
{sections}
<p class="footer">Generated by docs/diagrams/build.py · public version: no LAN addresses, VPN peers, or known weak spots.</p>
</div></body></html>'''

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "infrastructure.html")
with open(out, "w", encoding="utf-8") as fh:
    fh.write(page)
print("wrote", out, [s for s, *_ in figs])
