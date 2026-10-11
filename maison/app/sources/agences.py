"""Petites agences locales aux sites « maison » (un parseur par site), regroupées sous la source « agences ».

- Référence Immobilière (Wambrechies) : liste HTML paginée + fiche detail-vente?id=N
- Immobilière de Croix (Winimmobilier) : POST ajax.listbien.php (toutes les annonces) + ajax.getbien.php
- Dreossi Immobilier (Wix, Wambrechies) : page /properties (rendu serveur) + fiche /properties/<slug>
- Immobilier & Compagnie (Lomme, Pont-à-Marcq, Quesnoy) : catalogue /nos-bien-a-vendre,c2/page-N/ + fiche ,bID/

Square Habitat n'est pas ici : son réseau Nord de France est devenu Citya (déjà couvert par Bien'ici).
"""
import hashlib
import html as htmlmod
import logging
import re
import time
from app import textparse as tp
from app.sources import maes

log = logging.getLogger(__name__)
SOURCE = "agences"


# ---------- utilitaires ----------
def _text(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>", " ", fragment or "", flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\u200b", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", t).strip()


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _num(s):
    if s is None:
        return None
    s = re.sub(r"[^\d,.]", "", str(s)).replace(",", ".")
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


def zone_index(crit) -> dict:
    """city_key -> (nom, insee, code postal) pour les villes de nos zones."""
    return {tp.city_key(name): (name, z.get("insee"), (z.get("postal_codes") or [None])[0])
            for name, z in crit["zones"].items()}


def _dpe(t: str):
    e = re.search(r"Classe [ée]nergie\s*:?\s*([A-G])\b", t) or re.search(r"[ée]nerg[ée]tique.{0,60}?Classe\s+([A-G])\b", t, re.S)
    g = re.search(r"Classe climat\s*:?\s*([A-G])\b", t) or re.search(r"effet de serre.{0,60}?Classe\s+([A-G])\b", t, re.S)
    return (e.group(1) if e else None), (g.group(1) if g else None)


def _flags(text: str) -> dict:
    return dict(has_garage=tp.tri_garage(text), has_garden=tp.tri_garden(text), has_bathtub=tp.tri_bathtub(text),
                works=tp.detect_works(text), property_type=tp.detect_type(text))


# ---------- Référence Immobilière ----------
REF_BASE = "https://www.reference-immobiliere.com"


def refimmo_parse_list(page: str) -> list[dict]:
    out = []
    for card in re.findall(r'<div class="property-information">(.*?)</ul>', page, re.S):
        idm = re.search(r"detail-vente\?id=(\d+)", card)
        title = _flat(_text((re.search(r"<h5>(.*?)</h5>", card, re.S) or [None, ""])[1]))
        price = re.search(r'class="price">([^<]*)<', card)
        ref = re.search(r"([\w-]+-REFERENCE)", card)
        m = re.match(r"Vente\s+(.*?)\s*à\s+(.+)$", title)
        if not (idm and ref):
            continue
        out.append(dict(id=idm.group(1), ref=ref.group(1), kind=(m.group(1) if m else "").strip(),
                        city=(m.group(2) if m else "").strip(), price=_num(price.group(1)) if price else None))
    return out


def refimmo_parse_detail(page: str) -> dict:
    t = _flat(_text(page))
    desc = re.search(r"Description\s*:\s*(.*?)\s*Infos\s*:", t)
    desc = desc.group(1) if desc else ""
    dpe, ges = _dpe(t)
    garage = re.search(r"Garage\s*:\s*(\d+|Oui|Non)\b", t, re.I)
    photos = re.findall(r'(?:src|href)="(https://www\.reference-immobiliere\.com/Immopilote/Annonces/[^"]+)"', page)
    d = dict(description=desc[:6000], dpe=dpe, ges=ges,
             surface_m2=_num((re.search(r"Surface habitable\s*:\s*([\d.,]+)", t) or [None, None])[1]),
             land_m2=_num((re.search(r"Surface (?:du )?terrain\s*:\s*([\d.,]+)", t) or [None, None])[1]),
             bedrooms=_num((re.search(r"Chambres?\s*:\s*(\d+)", t) or [None, None])[1]),
             photo_urls=list(dict.fromkeys(photos)), **_flags(desc))
    if garage:
        g = garage.group(1).lower()
        d["has_garage"] = True if (g == "oui" or (g.isdigit() and int(g) > 0)) else d["has_garage"]
    return d


def fetch_refimmo(client, crit, known):
    zones, max_price, seen, out = zone_index(crit), crit["price"]["max"], set(), []
    for page in range(1, 30):
        r = client.get(f"{REF_BASE}/ventes", params={"page": page})
        r.raise_for_status()
        cards = [c for c in refimmo_parse_list(r.text) if c["ref"] not in seen]
        if not cards:                       # au-delà de la dernière page, le site renvoie la page 1
            break
        for c in cards:
            seen.add(c["ref"])
            z = zones.get(tp.city_key(c["city"]))
            if c["kind"].lower() != "maison" or not z or (c["price"] or 0) > max_price:
                continue
            ext, url = f"refimmo:{c['ref']}", f"{REF_BASE}/detail-vente?id={c['id']}"
            base = dict(source=SOURCE, external_id=ext, url=url, agency="Référence Immobilière")
            if (known.get(ext) or {}).get("price") == c["price"]:
                out.append(base)                # inchangée : simple « vu »
                continue
            time.sleep(1.5)
            d = refimmo_parse_detail(client.get(url).text)
            out.append(dict(base, **d, title=f"Maison {c['city']} — {d.get('surface_m2') or '?'} m²",
                            price=c["price"], city=z[0], insee=z[1], postal_code=z[2],
                            raw={"price": c["price"], "ref": c["ref"]}))
        time.sleep(1)
    return out


# ---------- Immobilière de Croix ----------
CROIX_BASE = "http://www.immobiliere-de-croix.com/"


def croix_parse_list(page: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<article class="annonces[^"]*"([^>]*)>(.*?)</article>', page, re.S):
        attrs = dict(re.findall(r'data-([a-z]+)="([^"]*)"', m.group(1)))
        body = m.group(2)
        loc = re.search(r"<h4>\s*([^<(]+?)\s*\((\d{5})\)", body)
        img = re.search(r'data-img="([^"]+)"', body)
        pub = re.search(r'itemprop="releaseDate">(\d{8})<', body)
        out.append(dict(ref=attrs.get("ref"), pos=attrs.get("pos"), kind=attrs.get("recherche", ""),
                        price=_num(attrs.get("prix")), surface=_num(attrs.get("surfhab")),
                        rooms=_num(attrs.get("piece")), bedrooms=_num(attrs.get("chambre")),
                        city=loc.group(1).strip().title() if loc else None, cp=loc.group(2) if loc else None,
                        photo=img.group(1) if img else None,
                        published=f"{pub.group(1)[:4]}-{pub.group(1)[4:6]}-{pub.group(1)[6:]}" if pub else None))
    return out


def croix_parse_detail(page: str) -> dict:
    t = _flat(_text(page))
    desc = re.search(r"DESCRIPTION\s*-\s*(.*?)\s*INFORMATIONS", t)
    desc = desc.group(1) if desc else t[:3000]
    infos = (re.search(r"INFORMATIONS(.*?)(?:DETAIL DES PIECES|Diagnostic|$)", t) or [None, ""])[1]
    d = dict(description=desc[:6000],
             land_m2=_num((re.search(r"Surface (?:jardin|terrain)\s*:\s*([\d.,]+)", t) or [None, None])[1]),
             year_built=_num((re.search(r"Année de construction\s*:\s*(\d{4})", t) or [None, None])[1]),
             photo_urls=[CROIX_BASE + p for p in dict.fromkeys(re.findall(r'(?:src|data-img)="(Photo_\d+\.jpe?g)"', page))],
             **_flags(desc + " " + infos))
    if re.search(r"Stationnement\s*:[^:]*Garage", t, re.I):
        d["has_garage"] = True
    if re.search(r"Surface jardin\s*:\s*\d", t):
        d["has_garden"] = True
    return d


def fetch_croix(client, crit, known):
    zones, max_price, out = zone_index(crit), crit["price"]["max"], []
    client.get(CROIX_BASE)
    r = client.post(CROIX_BASE + "ajax.listbien.php", data={"ville": "Toutes les villes"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    for c in croix_parse_list(r.text):
        z = zones.get(tp.city_key(c["city"], c["cp"]))
        if not c["kind"].startswith("v:m") or not z or (c["price"] or 0) > max_price:
            continue                             # v:m = vente maison
        ext = f"croix:{c['ref']}"
        slug = re.sub(r"[^a-z0-9]+", "-", tp.norm(c["city"]))
        base = dict(source=SOURCE, external_id=ext, agency="Immobilière de Croix",
                    url=f"{CROIX_BASE}#!/annonce-immobiliere-list-maison-{slug}-{c['ref']}/")
        if (known.get(ext) or {}).get("price") == c["price"]:
            out.append(base)
            continue
        time.sleep(1.5)
        d = croix_parse_detail(client.post(CROIX_BASE + "ajax.getbien.php",
                                           data={"pos": c["pos"], "ind": c["pos"], "par": "list", "ref": c["ref"]},
                                           headers={"X-Requested-With": "XMLHttpRequest"}).text)
        if not d["photo_urls"] and c["photo"]:
            d["photo_urls"] = [CROIX_BASE + c["photo"]]
        out.append(dict(base, **d, title=f"Maison {z[0]} — {c['surface'] or '?'} m²", price=c["price"],
                        surface_m2=c["surface"], rooms=c["rooms"], bedrooms=c["bedrooms"], city=z[0], insee=z[1],
                        postal_code=c["cp"] or z[2], published_at=c["published"],
                        raw={"price": c["price"], "ref": c["ref"]}))
    return out


# ---------- Dreossi (Wix) ----------
DREOSSI_BASE = "https://www.dreossi-immobilier.com"
SOLD = re.compile(r"\bVENDUE?\b", re.I)


def dreossi_parse_list(page: str) -> list[dict]:
    body = page.split("<body", 1)[-1]
    out = []
    for block in re.split(r'<div role="listitem"', body)[1:]:
        block = re.split(r"<script", block)[0]
        slug = re.search(r'href="https://www\.dreossi-immobilier\.com/properties/([^"]+)"', block)
        txt = _flat(_text("<x " + block))
        txt = re.split(r"POUR NOUS CONTACTER", txt)[0]
        if slug:
            out.append(dict(slug=slug.group(1), text=txt))
    return out


def dreossi_parse_detail(page: str) -> dict:
    t = _flat(_text(page.split("<body", 1)[-1]))
    t = re.split(r"POUR NOUS CONTACTER|Vous souhaitez visiter", t)[0]
    t = t.split("< Retour", 1)[-1].strip()
    price = re.search(r"(?:Prix|prix de vente)\s*:?\s*(\d{2,3}(?:[\s.\u202f]?\d{3})+)\s*(?:€|euros)", t) or \
        re.search(r"(\d{3}[\s.\u202f]\d{3})\s*(?:€|euros)(?!\s*/\s*an)", t)
    dpe, ges = _dpe(t)
    photos = re.findall(r'https://static\.wixstatic\.com/media/(c3d335_[0-9a-f]+(?:~|%7E)mv2\.(?:jpe?g|png))', page)
    return dict(description=t[:6000], price=_num(price.group(1)) if price else None, dpe=dpe, ges=ges,
                surface_m2=_num((re.search(r"(\d{2,3})\s*m(?:2|²)\b", t) or [None, None])[1]),
                bedrooms=_num((re.search(r"(\d)\s+chambres", t) or [None, None])[1]),
                ref=(re.search(r"R[ée]f\.?\s*:?\s*([\w-]+)", t) or [None, None])[1],
                photo_urls=[f"https://static.wixstatic.com/media/{p.replace('%7E', '~')}"
                            for p in dict.fromkeys(photos)],
                **_flags(t))


def fetch_dreossi(client, crit, known):
    zones, max_price, out = zone_index(crit), crit["price"]["max"], []
    r = client.get(f"{DREOSSI_BASE}/properties")
    r.raise_for_status()
    for c in dreossi_parse_list(r.text):
        if SOLD.search(c["text"]) or "MAISON" not in c["text"].upper():
            continue
        ext, url = f"dreossi:{c['slug']}", f"{DREOSSI_BASE}/properties/{c['slug']}"
        sig = hashlib.sha1(c["text"].encode()).hexdigest()[:12]
        base = dict(source=SOURCE, external_id=ext, url=url, agency="Dreossi Immobilier")
        if (known.get(ext) or {}).get("sig") == sig:
            out.append(base)
            continue
        time.sleep(1.5)
        d = dreossi_parse_detail(client.get(url).text)
        ck = tp.city_key(c["text"])
        city = next((zones[k] for k in sorted(zones, key=len, reverse=True)
                     if k and re.search(rf"\b{re.escape(k)}\b", ck)), None)
        if not city or (d["price"] or 0) > max_price:
            continue
        d.pop("ref", None)
        under_offer = "SOUS OFFRE" in c["text"].upper()
        out.append(dict(base, **d, title=("[Sous offre] " if under_offer else "") + f"Maison {city[0]} (Dreossi)",
                        city=city[0], insee=city[1], postal_code=city[2], raw={"sig": sig}))
    return out


# ---------- Immobilier & Compagnie (Lomme, Pont-à-Marcq, Quesnoy-sur-Deûle) ----------
IEC_BASE = "https://www.immobilier-et-compagnie.fr"
IEC_LINK = re.compile(r'href="(https://www\.immobilier-et-compagnie\.fr/vente-([a-z]+)-([a-z0-9-]+),b(\d+)/)"')


def _ges_class(ges):
    return next((c for c, lim in zip("ABCDEF", (6, 11, 30, 50, 70, 100)) if ges <= lim), "G")


def iec_parse_list(page: str) -> list[dict]:
    out = []
    for card in re.split(r'<div class="bien-lm">', page)[1:]:
        m = IEC_LINK.search(card)
        if not m:
            continue
        price = re.search(r'<div class="price">\s*([\d\s\u202f.]+)\s*€', card)
        city = re.search(r'<span class="bien-city">\s*-\s*([^<]+)<', card)
        out.append(dict(url=m.group(1), kind=m.group(2), id=m.group(4),
                        city=htmlmod.unescape(city.group(1)).strip() if city else m.group(3).replace("-", " ").title(),
                        price=_num(price.group(1)) if price else None))
    return out


def iec_parse_detail(page: str) -> dict:
    body = page.split('<div id="bien"', 1)[-1]
    title = _flat(_text((re.search(r"<h1>(.*?)</h1>", body, re.S) or [None, ""])[1]))
    city = re.search(r'<span class="bien-city">\s*-\s*([^<]+)<', body)
    t = _flat(_text(body))
    desc = t.split(title, 1)[-1] if title else t
    desc = re.split(r"\bRef\.\s*[A-Z]+\d+", desc)[0].replace(city.group(1).strip() if city else "", "", 1).strip(" -")
    ref = re.search(r"\bRef\.\s*([A-Z]+\d+)", t)
    price = re.search(r"Ref\.\s*[A-Z]+\d+\s+(\d{2,3}(?:[\s\u202f.]\d{3})+)\s*€", t)
    dpe = re.search(r"Coût énergétique annuel\s*:\s*[\d\s\u202f.,]+€\s+(\d+)\s+(\d+)", t)
    dpe_date = re.search(r"Date diagnostic énergétique\s*:\s*(\d{2})/(\d{2})/(\d{4})", t)
    energy, ges = (int(dpe.group(1)), int(dpe.group(2))) if dpe else (None, None)
    full = title + " " + desc
    surface = re.search(r"(\d{2,3})\s*m²\s*(?:habitables|utiles)?", title) or re.search(r"(\d{2,3})\s*m²\s*(?:habitables|utiles)", desc)
    land = re.search(r"(?:parcelle|terrain)\s+(?:de\s+|d'environ\s+)?(\d{3,4})\s*m²", desc, re.I)
    beds = re.search(r"(\d{1,2})\s+[Cc]hambres", full)
    year = re.search(r"construite? en (\d{4})", desc)
    photos = re.findall(r'<a href="(https://www\.immobilier-et-compagnie\.fr/media/immobilier_bien/[^"]+-full\.(?:png|jpe?g))"', body)
    return dict(title=title, city=city.group(1).strip() if city else None, description=desc[:6000],
                ref=ref.group(1) if ref else None, price=_num(price.group(1)) if price else None,
                dpe=maes._dpe_class(energy, ges), dpe_value=energy,
                ges=_ges_class(ges) if ges is not None else None,
                dpe_date=f"{dpe_date.group(3)}-{dpe_date.group(2)}-{dpe_date.group(1)}" if dpe_date else None,
                surface_m2=_num(surface.group(1)) if surface else None, land_m2=_num(land.group(1)) if land else None,
                bedrooms=_num(beds.group(1)) if beds else None, year_built=_num(year.group(1)) if year else None,
                photo_urls=list(dict.fromkeys(photos)), **_flags(full))


def iec_item(client, url, zones, list_price=None):
    """Fiche détaillée -> item (None si hors zones)."""
    d = iec_parse_detail(client.get(url).text)
    z = zones.get(tp.city_key(d["city"]))
    if not z:
        return None
    bid = re.search(r",b(\d+)/?$", url).group(1)
    price = d.pop("price") or list_price
    d.pop("ref", None)
    return dict(d, source=SOURCE, external_id=f"iec:{bid}", url=url, agency="Immobilier & Compagnie",
                price=price, city=z[0], insee=z[1], postal_code=z[2], raw={"price": price, "id": bid})


def fetch_iec(client, crit, known):
    zones, max_price, out, seen = zone_index(crit), crit["price"]["max"], [], set()
    for page in range(1, 20):
        url = f"{IEC_BASE}/nos-bien-a-vendre,c2/" + (f"page-{page}/" if page > 1 else "")
        r = client.get(url, params={"tri": 3})
        r.raise_for_status()
        cards = [c for c in iec_parse_list(r.text) if c["id"] not in seen]
        if not cards:
            break
        for c in cards:
            seen.add(c["id"])
            ext = f"iec:{c['id']}"
            # Au-delà du budget : gardée seulement si déjà suivie (ajout manuel), pour ne pas la marquer disparue
            if c["kind"] != "maison" or not zones.get(tp.city_key(c["city"])) or \
                    ((c["price"] or 0) > max_price and ext not in known):
                continue
            base = dict(source=SOURCE, external_id=ext, url=c["url"], agency="Immobilier & Compagnie")
            if (known.get(ext) or {}).get("price") == c["price"]:
                out.append(base)
                continue
            time.sleep(1.5)
            item = iec_item(client, c["url"], zones, list_price=c["price"])
            if item:
                out.append(item)
        time.sleep(1)
    return out


AGENCIES = {"refimmo": fetch_refimmo, "croix": fetch_croix, "dreossi": fetch_dreossi, "iec": fetch_iec}
DETAIL_FETCHERS = {"immobilier-et-compagnie.fr": iec_item}     # ajout manuel d'une annonce par URL


def fetch(client, crit, known: dict | None = None, **_):
    """known = {external_id: raw (+url)} des annonces « agences » actives en base."""
    known = known or {}
    out, failed = [], set()
    for name, fn in AGENCIES.items():
        try:
            out += fn(client, crit, known)
        except Exception as e:      # site en panne : ses annonces connues restent « vues »
            log.warning("agence %s en échec : %s", name, e)
            failed.add(name)
    have = {i["external_id"] for i in out}
    for ext, raw in known.items():
        if ext.split(":")[0] in failed and ext not in have:
            out.append({"source": SOURCE, "external_id": ext, "url": raw.get("url")})
    return out
