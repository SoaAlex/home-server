import re
import unicodedata


def norm(s: str | None) -> str:
    s = (s or "").replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def city_key(city: str | None, postal_code: str | None = None) -> str:
    k = norm(city)
    inner = re.search(r"\(([^)]+)\)", k)       # « Lille (Lomme) » -> lomme
    if inner:
        k = inner.group(1)
    k = k.replace("-", " ").replace("'", " ")
    k = re.sub(r"\bst\b", "saint", k)
    k = re.sub(r"\b(les|lez)\b", "lez", k)
    k = re.sub(r"\s+", " ", k).strip()
    if k == "lille" and str(postal_code or "").startswith("59160"):
        return "lomme"
    return k


def _tri(text: str | None, pos: str, neg: str) -> bool | None:
    t = norm(text)
    if re.search(neg, t):
        return False
    if re.search(pos, t):
        return True
    return None


def tri_garage(text):
    return _tri(text, r"\bgarages?\b|\bbox ferme", r"\b(sans|pas de|aucun) garage")


def tri_garden(text):
    return _tri(text, r"\bjardin|\bpelouse|\bterrain (arbore|clos|de \d)",
                r"\b(sans|pas de|aucun) jardin")


def tri_bathtub(text):
    return _tri(text, r"\bbaignoire", r"\bsans baignoire")


# Type de maison déclaré par le texte (design D7 du change improve-adjacency-detection) : motifs testés dans
# l'ordre, le premier trouvé gagne. « indépendante » seul (cuisine, entrée…) ne dit rien du type de maison.
_MIT = r"mitoyen(?:ne)?s?"
_LINK = re.compile(rf"\b(?:{_MIT}|jumelee?s?|accolee?s?|reliee?s?)\b(?: [\w']+){{0,3}}? (?:par|cote|via|au niveau d[ue]) "
                   r"(?:le |la |les |un |une |son |sa |ses |leurs? |des |du |l')?(?:\w+ )?(?:garages?|annexes?)\b")
_SEMI = re.compile(rf"\bsemi[- ]?(?:individuelle|{_MIT})s?\b"
                   rf"|\b{_MIT} (?:que |uniquement |seulement )?(?:d'?|sur |par )(?:un|1) (?:seul )?cote\b"
                   r"|\bjumelee?s?\b")
_TERRACED = re.compile(rf"\b{_MIT} (?:des|de|sur les) (?:deux|2) cotes\b|\b{_MIT} de chaque cote\b"
                       r"|\b(?:maisons?|habitations?|pavillons?)(?: \w+){0,2} en bande\b")
_NOT_ATTACHED = re.compile(rf"\b(?:non|pas|jamais)[- ]{_MIT}\b|\b(?:sans|aucune|ni|pas de) mitoyennete\b"
                           r"|\b(?:4|quatre) facades\b")
_INDIVIDUAL = re.compile(r"\bindividuelle\b")
_ANY_MIT = re.compile(rf"\b{_MIT}\b")
# « villa » n'est plus un indice : sur 30 annonces du spike à adresse connue, aucune n'était isolée (23 mitoyennes).
_WEAK = (("terraced", re.compile(rf"\b{_MIT}\b|\bmaison de ville\b|\bbel[- ]etage\b")),)


def detect_type(text) -> tuple[str, str | None, str | None]:
    """(type, force, expression) déduits du titre + description.
    type : detached | link_detached | semi | terraced | unknown ; force : explicit | weak | None.
    « individuelle » n'est retenu que si le texte ne dit pas aussi « mitoyenne » : beaucoup d'agences écrivent
    « maison individuelle mitoyenne » au sens de maison unifamiliale (spike 2026-11-01)."""
    t = norm(text)
    for typ, rx in (("link_detached", _LINK), ("semi", _SEMI), ("terraced", _TERRACED), ("detached", _NOT_ATTACHED)):
        if m := rx.search(t):
            return typ, "explicit", m.group(0)
    if (m := _INDIVIDUAL.search(t)) and not _ANY_MIT.search(t):
        return "detached", "explicit", m.group(0)
    for typ, rx in _WEAK:
        if m := rx.search(t):
            return typ, "weak", m.group(0)
    return "unknown", None, None


def type_fields(text) -> dict:
    """Colonnes `property_type` / `property_type_src` d'une annonce à partir de son texte."""
    typ, force, _ = detect_type(text)
    return {"property_type": typ, "property_type_src": f"text_{force}" if force else None}


NO_WORKS = re.compile(r"\b(?:sans|aucuns?|pas de|nul besoin de)\s+(?:aucuns?\s+|gros\s+|de\s+)?trava+ux(?:\s+a\s+(?:prevoir|faire))?")


def detect_works(text) -> str | None:
    t = norm(text)
    negated = bool(NO_WORKS.search(t))
    t = NO_WORKS.sub(" ", t)          # « aucun travaux à prévoir » ne doit pas matcher « travaux à prévoir »
    if re.search(r"trava+ux a prevoir|a renover|renovation (complete|totale)|a rehabiliter|gros trava+ux", t):
        return "heavy"
    if re.search(r"rafraichissement|a rafraichir|quelques trava+ux|petits trava+ux", t):
        return "light"
    if negated or re.search(r"refaite? a neuf|\brenovee?\b|clef? en main|posez (?:juste )?vos (?:meubles|valises)", t):
        return "none"
    return None
