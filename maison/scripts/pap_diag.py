"""Diagnostic pagination PAP : total, pages, in-zone par page (Wambrechies/Lille)."""
import re
import time

from app import settings
from app.sources import pap

crit = settings.load_criteria()
zones = crit["zones"]
zone_slugs = {pap.slug(c) for c in zones}
zone_cps = {cp for z in zones.values() for cp in (z.get("postal_codes") or [])}
session = pap._session()

for city in ("Wambrechies", "Lille"):
    zone = zones[city]
    gid = pap.gid_for(session, city)
    cp = (zone.get("postal_codes") or [""])[0]
    url = f"{pap.BASE}/annonce/vente-maisons-{pap.slug(city)}-{cp}-g{gid}"
    print(f"== {city} g{gid} {url}")
    for page in range(1, 9):
        html = pap._get(session, url)
        total = re.findall(r"&quot;annonces_total&quot;:(\d+)", html)
        allc = pap.parse_list(html)
        ink = pap.parse_list(html, zone_slugs, zone_cps)
        nxt = pap.next_page(html)
        print(f"   p{page}: total={total[:1]} toutes={len(allc)} in-zone={len(ink)} next={'oui' if nxt else 'non'}")
        if not nxt:
            break
        url = nxt
        time.sleep(1.2)
