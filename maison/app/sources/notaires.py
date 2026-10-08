import time
from app import textparse as tp

URL = "https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces"
PAGE = 50


def _ads(payload):
    return payload.get("annonceResumeDto") or []


def parse(payload, crit) -> list[dict]:
    insee = {str(v["insee"]) for v in crit["zones"].values() if v.get("insee")}
    cps = {str(cp) for v in crit["zones"].values() for cp in v.get("postal_codes", [])}
    out = []
    for a in _ads(payload):
        if str(a.get("inseeCommune")) not in insee and str(a.get("codePostal")) not in cps:
            continue
        text = a.get("descriptionFr") or ""
        city = a.get("communeNom") or (a.get("localiteNom") or "").title()
        out.append(dict(
            source="notaires", external_id=a.get("annonceId") or a.get("id"),
            url=a.get("urlDetailAnnonceFr"),
            title=f"Maison {a.get('nbPieces') or ''} pièces {city} (notaires)",
            description=text, price=a.get("prixTotal") or a.get("prixAffiche"),
            surface_m2=a.get("surface"), land_m2=a.get("surfaceTerrain"),
            rooms=a.get("nbPieces"), bedrooms=a.get("nbChambres"),
            property_type=tp.detect_type(text), city=city,
            postal_code=a.get("codePostal"), insee=a.get("inseeCommune"),
            has_garage=tp.tri_garage(text), has_garden=tp.tri_garden(text),
            has_bathtub=tp.tri_bathtub(text), works=tp.detect_works(text),
            agency="Notaires", published_at=a.get("dateCreation"),
            photo_urls=[a["urlPhotoPrincipale"]] if a.get("urlPhotoPrincipale") else [],
            raw={k: a.get(k) for k in ("annonceId", "reference", "dateMaj", "variation", "nbPhoto",
                                        "prixAffiche", "emoluments", "localiteNom")}))
    return out


def fetch(client, crit, **_):
    items, page = [], 1
    while True:
        r = client.get(URL, params=dict(page=page, parPage=PAGE, prixMax=crit["price"]["max"],
                                        typeBiens="MAI", typeTransactions="VENTE", departements=59))
        r.raise_for_status()
        data = r.json()
        items += parse(data, crit)
        if page >= (data.get("nbPages") or 1) or page >= 80:
            return items
        page += 1
        time.sleep(2)
