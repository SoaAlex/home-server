"""Essai à blanc des petites agences (aucune écriture en base)."""
import time
import httpx
from app import settings
from app.sources import agences

crit = settings.load_criteria()
t0 = time.time()
with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30, follow_redirects=True) as c:
    items = agences.fetch(c, crit)
print(f"{len(items)} maisons en zone ≤ {crit['price']['max']} € en {time.time()-t0:.0f}s")
for i in sorted(items, key=lambda x: x.get("price") or 0):
    print(i["external_id"].ljust(24), str(i.get("price")).rjust(7), (i.get("city") or "")[:18].ljust(18), i.get("surface_m2"), "m²",
          "| terrain", i.get("land_m2"), "| ch", i.get("bedrooms"), "| gar", i.get("has_garage"), "jard", i.get("has_garden"),
          "bain", i.get("has_bathtub"), "| DPE", i.get("dpe"), "|", i.get("property_type"), "|", len(i.get("photo_urls") or []), "photos")
