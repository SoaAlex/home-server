"""Essai à blanc PAP (aucune écriture en base) : 3 villes."""
import time
from app import settings
from app.sources import pap

crit = settings.load_criteria()
zones = crit["zones"]
cities = [c for c in ("Wambrechies", "Lambersart") if c in zones]
zone_slugs = {pap.slug(c) for c in zones}
zone_cps = {cp for z in zones.values() for cp in (z.get("postal_codes") or [])}
session = pap._session()
t0 = time.time()
total = []
for c in cities:
    try:
        items = pap.fetch_city(session, c, zones[c], {}, set(), zone_slugs, zone_cps)
        total += items
        print(f"== {c}: {len(items)} annonces")
    except Exception as e:
        print(f"== {c}: ÉCHEC {type(e).__name__} {e}")
    time.sleep(1)
print(f"total {len(total)} en {time.time()-t0:.0f}s")
for i in sorted(total, key=lambda x: x.get("price") or 0)[:25]:
    print("  ", i["external_id"].ljust(16), str(i.get("price")).rjust(7), (i.get("city") or "")[:16].ljust(16),
          i.get("surface_m2"), "m² |", i.get("rooms"), "p |", i.get("dpe"), "|", (i.get("address") or "")[:38].ljust(38),
          "|", len(i.get("photo_urls") or []), "photos |", i.get("property_type"))
