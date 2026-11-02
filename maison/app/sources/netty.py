"""Sites d'agences sur le CMS Netty (Septeo) : API JSON publique /webapi/getJson/Templates/ProductsList.

Une agence = {name, url, username[, group]} dans criteria.yaml (clé netty_agencies). Le « username » se lit
dans les URL d'images (img.netty.immo/productcw/<username>/…). Plusieurs sites d'un même groupe partagent
le même portefeuille : on leur donne le même `group` pour que les annonces ne soient comptées qu'une fois.
"""
import html
import logging
import re
import time
from datetime import datetime, timezone
from app import textparse as tp

log = logging.getLogger(__name__)
API = "/webapi/getJson/Templates/ProductsList"
TYPES = {"01": "detached", "02": "semi", "03": "terraced"}   # type_house Netty
MAX_PAGES = 60


def _fr(v):
    return (v or {}).get("fr") if isinstance(v, dict) else v


def _text(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def _int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def zone_postcodes(crit) -> set[str]:
    return {str(cp) for v in crit["zones"].values() for cp in v.get("postal_codes", [])}


def parse_product(p: dict, site: dict) -> dict:
    """Une fiche Netty (élément de data.prodId) -> item normalisé."""
    title = _text(_fr(p.get("title"))) or p.get("title_auto") or "Maison"
    desc = _text(_fr(p.get("details")))
    plus = " ".join(p.get("plusList") or []) if isinstance(p.get("plusList"), list) else ""
    text = f"{title}\n{desc}\n{plus}"
    geo = p.get("geoloc") or {}
    park_int = _int(p.get("park_int"))
    garden = _int(p.get("garden"))
    garage = True if (park_int or 0) >= 1 else tp.tri_garage(text)
    # Le champ Netty « sdb » (salle de bains) est saisi trop librement pour prouver une baignoire :
    # on s'en tient au texte, l'agent d'enrichissement vérifie sur les photos.
    bath = tp.tri_bathtub(text)
    online = _int(p.get("time_online"))
    declared = TYPES.get(p.get("type_house"))      # type saisi par l'agence : prime sur le texte
    slug = _fr(p.get("url")) or "maison"
    ref = p.get("prod_ref") or p.get("ref")
    return dict(
        source="netty", external_id=f"{site.get('group') or site['username']}:{ref}",
        url=f"{site['url'].rstrip('/')}/vente/{slug},{ref}",
        title=title, description=desc[:6000], price=_int(p.get("pricePrimary") or p.get("price2")),
        surface_m2=_int(p.get("surface")), land_m2=_int(p.get("land")) or _int(p.get("garden2")),
        rooms=_int(p.get("rooms")), bedrooms=_int(p.get("rooms2")),
        **({"property_type": declared, "property_type_src": "structured"} if declared else tp.type_fields(text)),
        city=p.get("city"), postal_code=str(p.get("cp") or "") or None,
        year_built=_int(p.get("year")),
        has_garage=garage,
        has_garden=True if garden == 1 else False if garden == 2 else tp.tri_garden(text),
        has_bathtub=bath, works=tp.detect_works(text), agency=site["name"],
        lat=float(geo["latitude"]) if geo.get("latitude") else None,
        lon=float(geo["longitude"]) if geo.get("longitude") else None,
        geo_precision="city" if geo.get("latitude") else None,
        published_at=datetime.fromtimestamp(online, timezone.utc).isoformat() if online else None,
        photo_urls=[u for u in (p.get("photos") or []) if isinstance(u, str) and u.startswith("http")][:30],
        raw={"ref": ref, "time_modif": p.get("time_modif"), "site": site["url"], "agence_id": p.get("agence_id")})


def keep(p: dict, cps: set[str], max_price: int) -> bool:
    price = _int(p.get("pricePrimary") or p.get("price2"))
    return (p.get("transact") == 1 and str(p.get("cp")) in cps and price is not None and price <= max_price)


def parse_detail(page: str) -> dict:
    """Page HTML d'une annonce : seules les classes DPE/GES n'existent pas dans l'API liste."""
    t = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    e = re.search(r"Classe [ée]nergie\s*:?\s*([A-G])\b", t)
    g = re.search(r"Classe climat\s*:?\s*([A-G])\b", t)
    return {"dpe": e.group(1) if e else None, "ges": g.group(1) if g else None}


def list_products(client, site: dict) -> dict:
    """Toutes les maisons à la vente d'un site (y compris vendues : filtrées ensuite par keep())."""
    base = site["url"].rstrip("/")
    out = {}
    for page in range(1, MAX_PAGES + 1):
        body = {"params": {"type_offer": "1", "prod_type": "house", "query": {"page": page}}, "offset": 0,
                "limit": 1, "username": site["username"], "pageType": "ProductsList", "lang": "fr",
                "location": "/vente/maison"}
        r = client.post(base + API, json=body, headers={"Accept": "application/json",
                                                        "Referer": base + "/vente/maison"})
        r.raise_for_status()
        d = r.json()["data"]
        ids = (d.get("prodResults") or {}).get("search") or []
        new = [i for i in ids if i not in out]
        for i in ids:
            out[i] = d["prodId"][i]
        if not new or len(out) >= (d.get("prodCount") or 0):
            break
        time.sleep(1)
    return out


def fetch(client, crit, known: dict | None = None, **_):
    """known = {external_id: raw} des annonces netty déjà en base (évite de re-télécharger le détail)."""
    known = known or {}
    cps, max_price = zone_postcodes(crit), crit["price"]["max"]
    out, failed_groups = {}, set()
    for site in crit.get("netty_agencies") or []:
        group = site.get("group") or site["username"]
        try:
            products = list_products(client, site)
        except Exception as e:                          # site en panne : ne pas marquer ses annonces disparues
            log.warning("netty %s en échec : %s", site["url"], e)
            failed_groups.add(group)
            continue
        for p in products.values():
            if not keep(p, cps, max_price):
                continue
            item = parse_product(p, site)
            if item["external_id"] in out:
                continue
            prev = known.get(item["external_id"]) or {}
            if prev.get("time_modif") != p.get("time_modif") or "dpe_checked" not in prev:
                time.sleep(1.5)
                try:
                    d = client.get(item["url"], follow_redirects=True)
                    if d.status_code == 200:
                        item.update({k: v for k, v in parse_detail(d.text).items() if v})
                except Exception:
                    pass
            item["raw"]["dpe_checked"] = True
            out[item["external_id"]] = item
    # Groupes en échec : simple « vu » pour leurs annonces connues (pas de faux « disparu »)
    for ext, raw in known.items():
        if ext.split(":")[0] in failed_groups and ext not in out:
            out[ext] = {"source": "netty", "external_id": ext, "url": raw.get("url")}
    return list(out.values())
