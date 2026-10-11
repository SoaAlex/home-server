"""Annonces PAP.fr (particulier à particulier) — transport curl_cffi (Cloudflare « Un instant… »).

Liste par ville : /annonce/vente-maisons-<slug>-<cp>-g<ID> puis pagination /proximite/<slug>-g<ID>-<N>.
Le slug est cosmétique : seul le -g<ID> compte (résolu via /json/ac-geo?q=<ville>).
Chaque carte porte le lien de l'annonce (/annonces/<type>-<ville>-<cp>-r<ID>), les tags (pièces,
chambres, m²) et la classe DPE. La fiche détail contient un JSON-LD complet : prix, surface,
pièces, photos, description et la RUE (address.streetAddress) — plus le widget DPE.
Les annonces « partenaire » (immoneuf.com, construiresamaison…) n'ont pas de lien /annonces/…-r<ID> :
elles sont ignorées.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape

from curl_cffi import requests as cffi
from selectolax.parser import HTMLParser
from app import textparse as tp

log = logging.getLogger(__name__)
BASE = "https://www.pap.fr"
DELAY = 1.2          # entre deux requêtes (PAP renvoie 429 si on accélère)
MAX_PAGES = 8        # ~10 annonces par page (les annonces locales sont en tête)
REFRESH_DAYS = 3     # re-télécharge la fiche d'une annonce connue après N jours (prix, photos)
IMPERSONATE = "chrome"
AD_URL = re.compile(r"/annonces/[a-z-]+-(\d{5})-r(\d+)")
_gid_cache: dict[str, int] = {}


def _session():
    return cffi.Session(impersonate=IMPERSONATE)


def slug(city: str) -> str:
    s = tp.norm(city).replace("'", "-").replace(" ", "-")
    return re.sub(r"-+", "-", s).strip("-")


def _get(session, url: str, tries: int = 3) -> str:
    for attempt in range(tries):
        r = session.get(url, timeout=40)
        if r.status_code == 200:
            return r.text
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"PAP {r.status_code} sur {url}")


def gid_for(session, city: str) -> int | None:
    """ID géographique PAP d'une ville (via l'autocomplétion du site)."""
    if city in _gid_cache:
        return _gid_cache[city]
    try:
        r = session.get(f"{BASE}/json/ac-geo", params={"q": city}, timeout=30,
                        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/"})
        data = r.json() if r.status_code == 200 else []
    except Exception:
        data = []
    if isinstance(data, list) and data:
        _gid_cache[city] = data[0]["id"]
    return _gid_cache.get(city)


def parse_list(html: str, zone_slugs: set | None = None, zone_cps: set | None = None) -> list[dict]:
    """Cartes d'annonces PAP d'une page de résultats (partenaires ignorés, hors zone écartées).

    Les recherches PAP s'élargissent toutes seules aux villes voisines : on ne garde que les
    annonces dont le code postal (ou le slug de ville) appartient à nos zones."""
    doc = HTMLParser(html)
    out = []
    for card in doc.css(".search-list-item-alt"):
        a = card.css_first("a.item-title")
        href = (a.attributes.get("href") if a else "") or ""
        m = AD_URL.search(href)
        if not m:
            continue
        cp, rid = m.group(1), m.group(2)
        if zone_cps is not None and cp not in zone_cps:
            tail = re.search(r"/annonces/([a-z-]+)-\d{5}-r\d+", href)
            if not (tail and zone_slugs and any(tail.group(1).endswith("-" + s) for s in zone_slugs)):
                continue
        tags = [x.text(strip=True) for x in card.css(".item-tags li")]
        d = dict(id=rid, url=href if href.startswith("http") else BASE + href, cp=cp)
        for t in tags:
            if m2 := re.search(r"(\d+)\s*pièces", t):
                d["rooms"] = int(m2.group(1))
            elif m2 := re.search(r"(\d+)\s*chambres", t):
                d["bedrooms"] = int(m2.group(1))
            elif m2 := re.search(r"([\d.]+)\s*m²", t):
                d["surface_m2"] = float(m2.group(1))
        dpe = card.css_first("[class*='item-thumb-dpe-']")
        if dpe and (m2 := re.search(r"item-thumb-dpe-([a-g])", dpe.attributes.get("class") or "")):
            d["dpe"] = m2.group(1).upper()
        desc = card.css_first(".item-description")
        if desc:
            d["description"] = re.sub(r"\s+", " ", desc.text(strip=True).replace("**", ""))
        photos = [img.attributes.get("src") for img in card.css("img")
                  if "cdn.pap.fr/photos" in (img.attributes.get("src") or "")]
        if photos:
            d["photo_urls"] = list(dict.fromkeys(photos))
        out.append(d)
    return out


def next_page(html: str) -> str | None:
    doc = HTMLParser(html)
    a = doc.css_first("#pagination-next")
    href = (a.attributes.get("href") if a else None) or None
    return (BASE + href) if href and href.startswith("/") else href


def parse_detail(html: str) -> dict:
    """Fiche annonce : JSON-LD Product + widget DPE + terrain."""
    doc = HTMLParser(html)
    ld = None
    for node in doc.css("script[type='application/ld+json']"):
        try:
            d = json.loads(node.text())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            ld = d
            break
    if not ld:
        return {}
    props = {p.get("name"): p.get("value") for p in ld.get("additionalProperty") or [] if isinstance(p, dict)}
    txt = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
    addr = ld.get("address") or {}
    out = dict(
        title=_clean(ld.get("name")), description=_clean(ld.get("description")),
        price=int(float((ld.get("offers") or {}).get("price") or 0)) or None,
        surface_m2=_num(props.get("Surface")), rooms=_int(props.get("Nombre de pièces")),
        city=_clean(addr.get("addressLocality")), postal_code=addr.get("postalCode"),
        photo_urls=[u for u in (ld.get("image") or []) if isinstance(u, str)][:20],
    )
    if m := re.search(r"(\d+)\s*pièces\s+(\d+)\s*chambres\s+([\d.,]+)\s*m²", txt):   # ligne « X pièces Y chambres Z m² »
        out["rooms"], out["bedrooms"], out["surface_m2"] = int(m.group(1)), int(m.group(2)), _num(m.group(3))
    elif m := re.search(r"(\d+)\s*chambres", txt):
        out["bedrooms"] = int(m.group(1))
    if m := re.search(r"Terrain\s+([\d\s\u202f]+?)\s*m²", txt):
        out["land_m2"] = _num(m.group(1))
    street = _clean(addr.get("streetAddress"))
    if street:
        cp, city = out.get("postal_code") or "", out.get("city") or ""
        if cp and (cp in street or city.lower() in street.lower()):
            out["address"] = street          # la rue contient déjà ville/code postal
        else:
            out["address"] = f"{street}, {cp} {city}".strip().strip(",")
    widgets = doc.css(".energy-indice")
    for i, w in enumerate(widgets[:2]):
        act = w.css_first("li.active")
        if act:
            out["dpe" if i == 0 else "ges"] = act.text(strip=True).upper()[:1]
    full = f"{out.get('title') or ''} {out.get('description') or ''}"
    out.update(has_garage=tp.tri_garage(full), has_garden=tp.tri_garden(full), has_bathtub=tp.tri_bathtub(full),
               works=tp.detect_works(full), property_type=tp.detect_type(full))
    return out


def _clean(s):
    return re.sub(r"\s+", " ", unescape(s).replace("**", "")).strip() if isinstance(s, str) else s


def _num(s):
    try:
        return int(float(re.sub(r"[\s\u202f\xa0]", "", str(s)).replace(",", "."))) if s not in (None, "") else None
    except ValueError:
        return None


def _int(s):
    v = _num(s)
    return int(v) if v is not None else None


def _is_stale(raw: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(raw.get("fetched_at"))
    except (TypeError, ValueError):
        return True
    return (datetime.now(timezone.utc) - fetched).days >= REFRESH_DAYS


def fetch_city(session, city: str, zone: dict, known: dict, seen: set,
               zone_slugs: set | None = None, zone_cps: set | None = None) -> list[dict]:
    """Toutes les annonces maison d'une ville (pages successives) ; fiches détail pour les nouvelles."""
    gid = gid_for(session, city)
    if not gid:
        raise RuntimeError(f"ville introuvable dans l'autocomplétion PAP : {city}")
    cp = (zone.get("postal_codes") or [""])[0]
    url = f"{BASE}/annonce/vente-maisons-{slug(city)}-{cp}-g{gid}"
    out = []
    for _ in range(MAX_PAGES):
        html = _get(session, url)
        cards = parse_list(html, zone_slugs, zone_cps)
        for card in cards:
            ext = f"pap:{card['id']}"
            if ext in seen:
                continue
            seen.add(ext)
            prev = known.get(ext)
            if prev and not _is_stale(prev):
                out.append(dict(source="pap", external_id=ext, url=card["url"]))     # connue et fraîche : « vue »
                continue
            try:
                time.sleep(DELAY)
                item = parse_detail(_get(session, card["url"]))
            except Exception as e:
                log.warning("pap fiche %s en échec : %s", card["url"], e)
                continue
            if not item:
                continue
            item.update(source="pap", external_id=ext, url=card["url"],
                        raw={"fetched_at": datetime.now(timezone.utc).isoformat(), "city": city,
                             "price": item.get("price")})
            for k in ("city", "postal_code", "surface_m2", "rooms", "bedrooms", "dpe"):
                if not item.get(k) and card.get(k):
                    item[k] = card[k]                                              # repli sur la carte
            if not item.get("photo_urls") and card.get("photo_urls"):
                item["photo_urls"] = card["photo_urls"]
            if not item.get("description") and card.get("description"):
                item["description"] = card["description"]
            if not item.get("title"):
                item["title"] = f"Maison {item.get('rooms') or ''} pièces {item.get('surface_m2') or ''} m² — {city}".replace("  ", " ").strip()
            out.append(item)
        nxt = next_page(html)
        if not nxt:
            break
        url = nxt
        time.sleep(DELAY)
    return out


def fetch(client, crit, known: dict | None = None, **_):
    """known = {external_id: raw} des annonces PAP actives en base (villes, fetched_at)."""
    known = known or {}
    session = _session()
    out, seen, failed = [], set(), set()
    zones = crit.get("zones") or {}
    zone_slugs = {slug(c) for c in zones}
    zone_cps = {cp for z in zones.values() for cp in (z.get("postal_codes") or [])}
    for city, zone in zones.items():
        try:
            out += fetch_city(session, city, zone, known, seen, zone_slugs, zone_cps)
        except Exception as e:
            log.warning("pap ville %s en échec : %s", city, e)
            failed.add(city)
        time.sleep(DELAY)
    have = {i["external_id"] for i in out}
    for ext, raw in known.items():          # ville en échec : ses annonces restent « vues »
        if raw.get("city") in failed and ext not in have:
            out.append(dict(source="pap", external_id=ext, url=raw.get("url")))
    return out
