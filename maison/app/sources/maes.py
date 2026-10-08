import html
import re
import time
from selectolax.parser import HTMLParser
from app import textparse as tp

LIST = "https://www.maesimmobilier.fr/wp-json/wp/v2/biens"
AGENCY = "Maes Immobilier"


def _num(s):
    return int(re.sub(r"[^\d]", "", s)) if s else None


def _float(m):
    return float(m.group(1).replace(",", ".")) if m else None


def _dpe_class(energy, ges):
    """Étiquette DPE 2021 = la pire des deux classes énergie / climat."""
    if energy is None:
        return None
    e = next((c for c, lim in zip("ABCDEF", (70, 110, 180, 250, 330, 420)) if energy <= lim), "G")
    if ges is None:
        return e
    g = next((c for c, lim in zip("ABCDEF", (6, 11, 30, 50, 70, 100)) if ges <= lim), "G")
    return max(e, g)


def parse_detail(html: str) -> dict:
    doc = HTMLParser(html)
    for n in doc.css("script,style,noscript,header,footer,nav"):
        n.decompose()
    t = " ".join((doc.body.text(separator=" ") if doc.body else "").split())
    t = t.split("Ce bien vous intéresse")[0]          # couper « Autres biens »
    t = re.sub(r"R[ée]f[ée]rence\s*:\s*\S+", " ", t)  # « Référence : 201 384 000 € »
    price = re.search(r"(\d{2,3}(?:[\s\u202f.]\d{3})+)\s*€", t)
    city = re.search(r"charge de l.acqu[ée]reur\s+([A-ZÀ-Ý][\w\-'À-ÿ ]+?)\s+Accueil", t)
    dpe = re.search(r"(\d{1,3})\s+(\d{1,3})(?:\s+\d{1,3})?\s+Date de r[ée]alisation du diagnostic", t)
    dpe_date = re.search(r"diagnostic [ée]nerg[ée]tique\s*:\s*(\d{4}-\d{2}-\d{2})", t)
    energy, ges = (int(dpe.group(1)), int(dpe.group(2))) if dpe else (None, None)
    garage_field = re.search(r"Garage / Box\s*:\s*(oui|non)", t, re.I)
    garden_field = re.search(r"Jardin\s*:\s*(oui|non)", t, re.I)
    status = re.match(r"(Sous Offre|Sous Compromis|Vendu)", t, re.I)
    imgs = [i.attributes.get("src") or i.attributes.get("data-src") or "" for i in doc.css("img")]
    return dict(
        price=_num(price.group(1)) if price else None,
        city=city.group(1).strip() if city else None,
        surface_m2=_float(re.search(r"Surface\s*:\s*(\d+(?:[.,]\d+)?)\s*m", t)),
        land_m2=_float(re.search(r"[Tt]errain\s*:\s*(\d+(?:[.,]\d+)?)\s*m", t)),
        bedrooms=_num(m.group(1)) if (m := re.search(r"Nombre de chambres\s*:\s*(\d+)", t)) else None,
        rooms=_num(m.group(1)) if (m := re.search(r"Nombre de pi[èe]ces\s*:\s*(\d+)", t)) else None,
        year_built=_num(m.group(1)) if (m := re.search(r"Construction\s*:\s*(\d{4})", t)) else None,
        dpe=_dpe_class(energy, ges), dpe_value=energy,
        dpe_date=dpe_date.group(1) if dpe_date else None,
        description=t[:6000],
        has_garage=(garage_field.group(1).lower() == "oui") if garage_field else tp.tri_garage(t),
        has_garden=(garden_field.group(1).lower() == "oui") if garden_field else tp.tri_garden(t),
        has_bathtub=tp.tri_bathtub(t), works=tp.detect_works(t), property_type=tp.detect_type(t),
        sale_status=status.group(1).title() if status else None,
        photo_urls=list(dict.fromkeys(x for x in imgs if "wp-content/uploads" in x)))


def _is_house_sale(b):
    c = b.get("class_list") or []
    return "type_offre-vente" in c and "type_bien-maison" in c


def fetch(client, crit, known: dict | None = None, **_):
    """known = {external_id: modified} déjà en base : on ne re-télécharge pas le détail inchangé."""
    known = known or {}
    out, page = [], 1
    while True:
        r = client.get(LIST, params={"per_page": 100, "page": page,
                                     "_fields": "id,link,modified,class_list,title"})
        if r.status_code == 400:   # page au-delà de la dernière
            break
        r.raise_for_status()
        for b in r.json():
            if not _is_house_sale(b):
                continue
            base = dict(source="maes", external_id=b["id"], url=b["link"], agency=AGENCY)
            if known.get(str(b["id"])) == b.get("modified"):
                out.append(base)               # inchangé : juste « vu »
                continue
            time.sleep(2)
            d = client.get(b["link"], follow_redirects=True)
            if d.status_code != 200:
                continue
            item = parse_detail(d.text)
            status = item.pop("sale_status")
            title = html.unescape((b.get("title") or {}).get("rendered") or "Maison")
            item.update(base, title=f"[{status}] {title}" if status else title,
                        raw={"id": b["id"], "modified": b.get("modified"), "sale_status": status})
            out.append(item)
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return out
