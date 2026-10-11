import re
from app.textparse import city_key, norm

VIAGER = re.compile(r"\bviager\b|vente a terme|nue[- ]propriete|\bbouquet\b")

OVERRIDABLE = ("has_garage", "has_garden", "has_bathtub", "works", "property_type",
               "land_m2", "dpe", "bedrooms", "price", "surface_m2", "city")


def effective(row: dict) -> dict:
    """Champs bruts de l'annonce, surchargés par l'enrichissement (agent Hermes)."""
    enr = row.get("enrichment") or {}
    return {**row, **{k: enr[k] for k in OVERRIDABLE if enr.get(k) is not None}}


def score_listing(row: dict, crit: dict) -> tuple[int, dict, str | None]:
    r = effective(row)
    enr = row.get("enrichment") or {}
    if not r.get("city"):
        return 0, {}, "à compléter"
    sale = ((row.get("raw") or {}).get("sale_status") or "").lower()
    if sale in ("vendu", "sous compromis"):
        return 0, {}, sale
    if VIAGER.search(norm(f"{r.get('title') or ''} {r.get('description') or ''}")):
        return 0, {}, "viager / vente à terme"
    ck = city_key(r.get("city"), r.get("postal_code"))
    pref = [city_key(c) for c in crit["preferred_cities"]]
    other = {city_key(c) for c in crit.get("other_cities", [])}
    excl = {city_key(c) for c in crit.get("excluded_cities", [])}

    if ck in excl:
        return 0, {}, "ville exclue"
    if ck not in pref and ck not in other:
        return 0, {}, "hors zone"
    price = r.get("price")
    if price and price > crit["price"]["max"] and not enr.get("user_added"):   # ajout manuel : budget assumé
        return 0, {}, "prix > max"
    if crit["require"].get("garage") and r.get("has_garage") is False:
        return 0, {}, "pas de garage"
    if crit["require"].get("garden") and r.get("has_garden") is False:
        return 0, {}, "pas de jardin"

    d = {}
    d["ville"] = 25 - pref.index(ck) if ck in pref else 5
    d["prix"] = (0 if not price else 15 if price <= 400000 else
                 12 if price <= crit["price"]["target"] else 5 if price <= 500000 else 0)
    d["type"] = {"detached": 12, "semi": 7}.get(r.get("property_type"), 2)
    land = float(r.get("land_m2") or 0)
    d["jardin"] = 12 if land >= 400 else 8 if land >= 250 else 4 if land >= 150 else 1
    if enr.get("garden_shape") == "long":
        d["jardin"] -= 3
    d["dpe"] = {"A": 8, "B": 8, "C": 6, "D": 3, "E": 0, "F": -5, "G": -5}.get(
        (r.get("dpe") or "").upper(), 0)
    d["travaux"] = {"none": 8, "light": 3, "heavy": -8}.get(r.get("works"), 0)
    d["baignoire"] = 3 if r.get("has_bathtub") else 0
    d["chambres"] = (3 if (r.get("bedrooms") or 0) >= 3 else 0) + \
                    (4 if (enr.get("largest_bedroom_m2") or 0) >= 14 else 0)
    total_min = (row.get("commute") or {}).get("total_min")
    d["trajet"] = (0 if total_min is None else 8 if total_min <= 30 else
                   5 if total_min <= 40 else 2 if total_min <= 50 else 0)
    d["style"] = -4 if enr.get("style") == "cubic" else 0
    return max(0, min(100, sum(d.values()))), d, None


def fingerprint(r: dict) -> str | None:
    if not (r.get("price") and r.get("surface_m2")):
        return None
    return "|".join([city_key(r.get("city"), r.get("postal_code")),
                     str(round(float(r["price"]) / 10000)),
                     str(round(float(r["surface_m2"]) / 5)),
                     str(round(float(r.get("land_m2") or 0) / 50))])
