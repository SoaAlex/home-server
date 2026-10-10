"""Essai à blanc du collecteur Netty (aucune écriture en base)."""
import collections, time
import httpx
from app import settings
from app.sources import netty

crit = settings.load_criteria()
t0 = time.time()
with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as c:
    items = netty.fetch(c, crit)
print(f"{len(items)} maisons en zone ≤ {crit['price']['max']} € en {time.time()-t0:.0f}s")
print(collections.Counter(i["agency"] for i in items))
for i in sorted(items, key=lambda x: x["price"] or 0):
    print(i["external_id"].ljust(22), str(i["price"]).rjust(7), (i["city"] or "")[:20].ljust(20), i["surface_m2"], "m²",
          "terrain", i["land_m2"], "| gar", i["has_garage"], "jard", i["has_garden"], "bain", i["has_bathtub"],
          "| DPE", i.get("dpe"), "|", i["property_type"], "|", len(i["photo_urls"]), "photos")
