import json
import time
from app import textparse as tp

URL = "https://www.bienici.com/realEstateAds.json"
PAGE = 100


def _filters(zone_ids, frm, max_price):
    return {"size": PAGE, "from": frm, "filterType": "buy", "propertyType": ["house"],
            "maxPrice": max_price, "page": frm // PAGE + 1, "sortBy": "publicationDate",
            "sortOrder": "desc", "onTheMarket": [True], "zoneIdsByTypes": {"zoneIds": zone_ids}}


def _pos(ad):
    bi = ad.get("blurInfo") or {}
    p = bi.get("position") or bi.get("centroid") or {}
    if p.get("lat") and p.get("lon"):
        return p["lat"], p["lon"], ("exact" if ad.get("addressKnown") and bi.get("type") == "point" else "approx")
    return None, None, None


def _cls(v):
    return v if v and v not in ("NS", "VI") and len(v) == 1 else None


def parse(payload: dict) -> list[dict]:
    out = []
    for ad in payload.get("realEstateAds", []):
        text = f"{ad.get('title') or ''} {ad.get('description') or ''}"
        lat, lon, prec = _pos(ad)
        garden = True if ad.get("hasGarden") else tp.tri_garden(text)
        dpe_date = (ad.get("energyPerformanceDiagnosticDate") or "")[:10] or None
        out.append(dict(
            source="bienici", external_id=ad["id"],
            url=f"https://www.bienici.com/annonce/{ad['id']}",
            title=ad.get("title") or f"Maison {ad.get('roomsQuantity') or ''} pièces {ad.get('city')}",
            description=ad.get("description"),
            price=int(ad["price"]) if isinstance(ad.get("price"), (int, float)) else None,
            surface_m2=ad.get("surfaceArea"), land_m2=ad.get("landSurfaceArea"),
            rooms=ad.get("roomsQuantity"), bedrooms=ad.get("bedroomsQuantity"),
            property_type=tp.detect_type(text), city=ad.get("city"), postal_code=ad.get("postalCode"),
            dpe=_cls(ad.get("energyClassification")), ges=_cls(ad.get("greenhouseGazClassification")),
            dpe_value=int(ad["energyValue"]) if isinstance(ad.get("energyValue"), (int, float)) else None,
            dpe_date=dpe_date, year_built=ad.get("yearOfConstruction"),
            has_garage=tp.tri_garage(text), has_garden=garden, has_bathtub=tp.tri_bathtub(text),
            works=tp.detect_works(text), agency=ad.get("accountDisplayName"),
            lat=lat, lon=lon, geo_precision=prec, published_at=ad.get("publicationDate"),
            photo_urls=[p["url_photo"] for p in ad.get("photos", []) if p.get("url_photo")],
            raw={k: ad.get(k) for k in ("id", "reference", "blurInfo", "priceHasDecreased",
                                         "modificationDate", "isExclusiveSaleMandate", "status",
                                         "district")}))
    return out


def fetch(client, crit, **_) -> list[dict]:
    zone_ids = [z for v in crit["zones"].values() for z in v["zone_ids"]]
    items, frm = [], 0
    while True:
        r = client.get(URL, params={"filters": json.dumps(_filters(zone_ids, frm, crit["price"]["max"]))})
        r.raise_for_status()
        data = r.json()
        items += parse(data)
        frm += PAGE
        if frm >= data.get("total", 0) or not data.get("realEstateAds") or frm >= 2400:
            return items
        time.sleep(2)
