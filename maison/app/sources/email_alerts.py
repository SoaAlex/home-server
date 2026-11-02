"""Alertes e-mail (leboncoin, SeLoger, PAP, Jinka…) lues en IMAP sur un Gmail dédié.

On n'interroge JAMAIS leboncoin/SeLoger directement : on lit les e-mails. Les liens de tracking
(click.by.seloger.com…) sont résolus en lisant l'en-tête Location SANS suivre la redirection vers le portail.
Chaque annonce récupère le texte de sa « carte » dans l'e-mail (prix, pièces, surface, terrain, ville).
Les messages sont suivis par UID (pas par le drapeau « lu ») : lire la boîte sur un téléphone ne fait rien perdre.
Fixtures réelles : tests/fixtures/{leboncoin,seloger}_mail.eml.
"""
import email
import imaplib
import json
import logging
import os
import re
from email import policy
from html import unescape
from app import settings
from app import textparse as tp

log = logging.getLogger(__name__)
PATTERNS = {
    "leboncoin": re.compile(r"https?://(?:www\.)?leboncoin\.fr/(?:vi/|(?:ad/)?ventes_immobilieres/)(\d+)"),
    # Nouveau format : l'identifiant est le DERNIER segment du chemin. Sans le lookahead, « …/nord-59/lambersart-59130/<ID> »
    # donnait « lambersart » (1er segment de ≥ 8 caractères alphanumériques) : toutes les annonces de la ville
    # s'écrasaient sur une seule ligne.
    "seloger": re.compile(r"https?://(?:www\.)?seloger\.com/annonces/achat/[^\s\"'<>?#]*?/(\d+)\.htm"
                          r"|https?://(?:www\.)?seloger\.com/annonce/[^\s\"'<>?#]*?/([A-Za-z0-9]{8,})(?=/?(?:[?#\s\"'<>]|$))"),
    "pap": re.compile(r"https?://(?:www\.)?pap\.fr/annonces/[^\s\"'<>?]*-r(\d+)"),
    "jinka": re.compile(r"https?://(?:www\.)?jinka\.fr/[^\s\"'<>]*?(?:ad|alert_result)[^\s\"'<>]*?[?&/]ad=?/?(\d+)"),
}
SENDERS = ("leboncoin", "seloger", "pap.fr", "jinka")
TRACKING_HOSTS = re.compile(r"https?://(?:click|track|links?|email|mail|lnk|r)\.[^/]+|https?://[^/]*(?:click|track)[^/]*/",
                            re.I)
PRICE = re.compile(r"(\d{2,3}(?:[\s\u202f\xa0.]\d{3})+)\s*€(?!\s*/)")
# Vignettes d'annonce dans les e-mails (souvent en background-image), pas les logos/icônes
PHOTO = re.compile(r"https://(?:cdnihddipa\.cloudimg\.io/[^\"'()\s]+?\.jpe?g[^\"'()\s]*"
                   r"|img\.leboncoin\.fr/api/v1/[^\"'()\s]+?\.jpe?g[^\"'()\s]*"
                   r"|cdn\.pap\.fr/photos/[^\"'()\s]+|[a-z0-9.-]*jinka[^\"'()\s]*/[^\"'()\s]+\.jpe?g[^\"'()\s]*)", re.I)
MAX_MESSAGES_PER_RUN = 40


def _html(msg):
    part = msg.get_body(preferencelist=("html", "plain"))
    return part.get_content() if part else ""


def _match(url):
    for src, pat in PATTERNS.items():
        m = pat.search(url)
        if m:
            ext = next(g for g in m.groups() if g)
            canon = f"https://www.leboncoin.fr/vi/{ext}.htm" if src == "leboncoin" else m.group(0)
            return src, ext, canon
    return None


def _num(s):
    return int(float(re.sub(r"[\s\u202f\xa0]", "", s).replace(",", "."))) if s else None


def _best_photos(urls):
    """Une URL par image (la plus grande variante : cloudimg ?w=540 plutôt que ?w=270)."""
    best = {}
    for u in urls:
        base = u.split("?")[0]
        w = int((re.search(r"[?&]w=(\d+)", u) or [0, 0])[1])
        if base not in best or w > best[base][0]:
            best[base] = (w, u)
    return [u for _, u in best.values()]


def parse_card(text: str, cities=()) -> dict:
    """Texte de la « carte » d'une annonce dans l'e-mail -> champs (best effort)."""
    d = {}
    if m := PRICE.search(text):
        d["price"] = _num(m.group(1))
    if m := re.search(r"Terrain\s*:?\s*(\d[\d\s]*)\s*m²", text):
        d["land_m2"] = _num(m.group(1))
    if m := re.search(r"(?<!: )\b(\d+(?:[.,]\d+)?)\s*m²(?!\s*/)", text):
        d["surface_m2"] = _num(m.group(1))
    if m := re.search(r"(\d+)\s*pi[èe]ces?", text):
        d["rooms"] = int(m.group(1))
    if m := re.search(r"(\d+)\s*chambres?", text):
        d["bedrooms"] = int(m.group(1))
    cps = re.findall(r"\b(59\d{3}|62\d{3})\b", text)
    if cps:
        d["postal_code"] = cps[-1]
    n = tp.norm(text).replace("-", " ")
    for c in sorted(cities, key=len, reverse=True):          # ville connue la plus longue présente dans la carte
        k = tp.norm(c).replace("-", " ")
        if k and re.search(rf"\b{re.escape(k)}\b", n):
            d["city"] = c
            break
    else:
        if m := re.search(r"([A-ZÀ-Ý][\w'À-ÿ-]+(?:[ ,-]+[A-ZÀ-Ýa-zà-ÿ'-]+){0,3})\s*\(?\b(?:59|62)\d{3}\b", text):
            d["city"] = m.group(1).split(",")[-1].strip()
    if re.search(r"\bjardin\b", n):
        d["has_garden"] = True
    if re.search(r"\bgarage\b", n):
        d["has_garage"] = True
    d.update(tp.type_fields(text))
    return d


def parse_message(raw: bytes, resolve=None, cities=()) -> list[dict]:
    """resolve(url) -> URL finale d'un lien de tracking (ou None)."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    html = _html(msg)
    subject = str(msg["subject"] or "")
    if "<" not in html:                                       # e-mail texte brut
        html = re.sub(r"(https?://\S+)", r'<a href="\1">', html)
    html = re.sub(r"<style.*?</style>|<head.*?</head>", " ", html, flags=re.S | re.I)
    parts = re.split(r"<a\s[^>]*?href=[\"']([^\"']+)[\"'][^>]*>", html)   # texte, lien, texte, lien…
    # Les vignettes précèdent le lien de LEUR annonce (vérifié sur leboncoin/SeLoger) : une image est
    # mise en attente puis rattachée au prochain lien d'annonce (ou à l'annonce courante si le lien suivant
    # n'en est pas une).
    cards, order, current, pending = {}, [], None, []
    for idx, chunk in enumerate(parts):
        if idx % 2 == 1:                                      # un lien
            url = unescape(chunk)
            m = _match(url)
            if not m and resolve and TRACKING_HOSTS.match(url):
                final = resolve(url)
                m = _match(final) if final else None
            key = (m[0], m[1]) if m else None
            if key and key not in cards:
                cards[key] = {"url": m[2], "text": [], "photos": []}
                order.append(key)
            target = key or current
            if target and pending:
                cards[target]["photos"] += pending
                pending = []
            current = key
            continue
        pending += [unescape(p) for p in PHOTO.findall(chunk)]
        if current:
            txt = " ".join(unescape(re.sub(r"<[^>]+>", " ", chunk)).split())
            if txt:
                cards[current]["text"].append(txt)
    items = []
    for src, ext in order:
        text = " ".join(cards[(src, ext)]["text"])[:600]
        f = parse_card(text, cities)
        kind = "Maison" if "maison" in tp.norm(text) else "Bien"
        title = " ".join(x for x in (kind, f"{f['rooms']} pièces" if f.get("rooms") else "",
                                      f"{f['surface_m2']} m²" if f.get("surface_m2") else "",
                                      f"— {f.get('city') or '?'}") if x)
        items.append(dict(source=src, external_id=ext, url=cards[(src, ext)]["url"], title=title,
                          description=text or None, photo_urls=_best_photos(cards[(src, ext)]["photos"]), **f,
                          raw={"subject": subject, "from": str(msg["from"] or ""), "card": text}))
    return items


def make_resolver(client, limit=60):
    """Lit l'en-tête Location des liens de tracking (sans suivre jusqu'au portail). Cache + plafond."""
    cache, count = {}, [0]

    def resolve(url):
        if url in cache:
            return cache[url]
        if count[0] >= limit:
            return None
        count[0] += 1
        loc = url
        try:
            for _ in range(3):                                 # tracker -> tracker -> portail
                nxt = client.get(loc, follow_redirects=False, timeout=15).headers.get("location")
                if not nxt:
                    break
                loc = nxt
                if _match(loc) or not TRACKING_HOSTS.match(loc):
                    break
        except Exception:
            loc = None
        cache[url] = loc
        return loc
    return resolve


def _state_path():
    return settings.DATA_DIR / "imap_state.json"


def fetch(client, crit, **_) -> list[dict]:
    if not os.environ.get("IMAP_USER") or not os.environ.get("IMAP_PASSWORD"):
        return []
    cities = [*crit.get("zones", {}).keys(), *crit.get("excluded_cities", [])]
    try:
        state = json.loads(_state_path().read_text())
    except Exception:
        state = {}
    m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
    m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    m.select("INBOX", readonly=True)
    uv = (m.response("UIDVALIDITY")[1] or [b""])[0]
    uidvalidity = uv.decode() if isinstance(uv, bytes) else str(uv or "")
    last = state.get("last_uid", 0) if state.get("uidvalidity") == uidvalidity else 0
    _, ids = m.uid("search", None, f"UID {last + 1}:*")
    uids = sorted(int(u) for u in ids[0].split() if int(u) > last)[:MAX_MESSAGES_PER_RUN]
    out = []
    for uid in uids:
        _, d = m.uid("fetch", str(uid), "(BODY.PEEK[])")
        raw = next((p[1] for p in d if isinstance(p, tuple)), b"")
        frm = str(email.message_from_bytes(raw, policy=policy.default)["from"] or "").lower()
        if any(s in frm for s in SENDERS):
            out += parse_message(raw, make_resolver(client), cities)
        last = uid
    m.logout()
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps({"uidvalidity": uidvalidity, "last_uid": last}))
    log.info("emails: %d messages traités (dernier UID %d)", len(uids), last)
    return out
