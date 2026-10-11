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


def detect_type(text) -> str:
    t = norm(text)
    if re.search(r"semi[- ]?(individuelle|mitoyenne)|mitoyenne d.un (seul )?cote|jumelee", t):
        return "semi"
    if re.search(r"non mitoyenne|\bindividuelle\b|\bvilla\b|independante", t):
        return "detached"
    if re.search(r"mitoyenne|maison de ville|bel[- ]etage", t):
        return "terraced"
    return "unknown"


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
