"""Résout zoneIds Bien'ici + INSEE + CP pour chaque ville du fichier de critères.

Usage : python scripts/resolve_zones.py config/criteria.yaml   (réécrit la clé `zones`)
"""
import sys
import time
import httpx
import yaml
from app.textparse import city_key
from app.settings import USER_AGENT

path = sys.argv[1]
crit = yaml.safe_load(open(path, encoding="utf-8"))
cities = list(dict.fromkeys(crit["preferred_cities"] + crit["other_cities"]))
zones = crit.get("zones") or {}
with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20) as c:
    for city in cities:
        if city in zones:
            continue
        r = c.get("https://res.bienici.com/suggest.json", params={"q": city})
        r.raise_for_status()
        cands = [s for s in r.json() if city_key(s.get("name")) == city_key(city)]
        cands.sort(key=lambda s: 0 if str(s.get("postalCodes", [""])[0]).startswith("59") else 1)
        if not cands:
            print("NOT FOUND", city, [s.get("name") for s in r.json()][:5])
            continue
        best = cands[0]
        zones[city] = {"zone_ids": best.get("zoneIds", []),
                       "insee": best.get("insee_code") or best.get("inseeCode"),
                       "postal_codes": best.get("postalCodes", [])}
        print(city, zones[city])
        time.sleep(1)
crit["zones"] = zones
with open(path, "w", encoding="utf-8") as f:
    yaml.safe_dump(crit, f, allow_unicode=True, sort_keys=False)
