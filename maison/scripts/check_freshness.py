"""Vérifie que les 30 annonces Bien'ici les plus récentes et le total correspondent à la base."""
import json
import httpx
from app import db, settings
from app.sources.bienici import URL, _filters

crit = settings.load_criteria()
zones = [z for v in crit["zones"].values() for z in v["zone_ids"]]
f = _filters(zones, 0, crit["price"]["max"]); f["size"] = 30
data = httpx.get(URL, params={"filters": json.dumps(f)}, headers={"User-Agent": settings.USER_AGENT}, timeout=30).json()
ids = [a["id"] for a in data["realEstateAds"]]
with db.connect() as conn:
    known = {r["external_id"]: r for r in conn.execute(
        "SELECT external_id, first_seen, status, excluded_reason FROM listing WHERE source='bienici' AND external_id = ANY(%s)", (ids,)).fetchall()}
    active = conn.execute("SELECT count(*) n FROM listing WHERE source='bienici' AND status='active'").fetchone()["n"]
print("Bien'ici total en ligne :", data["total"], "| actives en base :", active)
for a in data["realEstateAds"]:
    k = known.get(a["id"])
    print(a["publicationDate"][:16], a["city"][:18].ljust(18), a.get("price"),
          "OK " + str(k["first_seen"])[:16] + (" (" + k["excluded_reason"] + ")" if k["excluded_reason"] else "") if k else "MANQUANTE")
