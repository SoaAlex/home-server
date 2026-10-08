"""Alertes e-mail (leboncoin, SeLoger, PAP, Jinka…) lues en IMAP sur un Gmail dédié.

On n'interroge JAMAIS leboncoin/SeLoger directement : seuls les liens présents dans les e-mails
sont extraits. Les fiches restent partielles (pas de ville) -> complétées par l'agent Hermes.
Les regex d'URL sont à ajuster sur des e-mails réels (tests/fixtures/*_mail.eml).
"""
import email
import imaplib
import os
import re
from email import policy
from selectolax.parser import HTMLParser

PATTERNS = {
    "leboncoin": re.compile(r"https?://(?:www\.)?leboncoin\.fr/(?:ad/)?ventes_immobilieres/(\d+)"),
    "seloger": re.compile(r"https?://(?:www\.)?seloger\.com/annonces/achat/[^\s\"'<>?]*?/(\d+)\.htm"
                          r"|https?://(?:www\.)?seloger\.com/annonce/[^\s\"'<>?]*?/([A-Za-z0-9]{8,})"),
    "pap": re.compile(r"https?://(?:www\.)?pap\.fr/annonces/[^\s\"'<>?]*-r(\d+)"),
    "jinka": re.compile(r"https?://(?:www\.)?jinka\.fr/[^\s\"'<>]*?(?:ad|alert_result)[^\s\"'<>]*?[?&/]ad=?/?(\d+)"),
}
SENDERS = ("leboncoin", "seloger", "pap.fr", "jinka")
PRICE = re.compile(r"(\d{2,3}(?:[\s\u202f\xa0.]\d{3})+)\s*€")
TRACKING_HOSTS = re.compile(r"click|track|link|email|mail|lnk|r\.", re.I)


def _html(msg):
    part = msg.get_body(preferencelist=("html", "plain"))
    return part.get_content() if part else ""


def _match(url):
    for src, pat in PATTERNS.items():
        m = pat.search(url)
        if m:
            return src, next(g for g in m.groups() if g), m.group(0)
    return None


def parse_message(raw: bytes, client=None) -> list[dict]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    html = _html(msg)
    is_html = "<" in html
    text = " ".join(HTMLParser(html).text(separator=" ").split()) if is_html else html
    links = re.findall(r"href=[\"']([^\"']+)", html) if is_html else re.findall(r"https?://\S+", html)
    links = [l.replace("&amp;", "&") for l in links]
    if client is not None:       # résoudre les liens de redirection/tracking (max 30)
        extra = []
        for u in [l for l in links if not _match(l) and TRACKING_HOSTS.search(l)][:30]:
            try:
                extra.append(str(client.head(u, follow_redirects=True, timeout=15).url))
            except Exception:
                pass
        links += extra
    items, seen = [], set()
    for u in links:
        m = _match(u)
        if not m or (m[0], m[1]) in seen:
            continue
        seen.add((m[0], m[1]))
        items.append(dict(source=m[0], external_id=m[1], url=m[2],
                          title=str(msg["subject"] or "")[:200],
                          raw={"subject": str(msg["subject"] or ""), "from": str(msg["from"] or "")}))
    prices = [int(re.sub(r"\D", "", p)) for p in PRICE.findall(text)]
    if items and len(prices) == len(items):   # association uniquement si 1 prix par annonce
        for it, p in zip(items, prices):
            it["price"] = p
    return items


def fetch(client, crit, **_) -> list[dict]:
    if not os.environ.get("IMAP_USER") or not os.environ.get("IMAP_PASSWORD"):
        return []
    m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
    m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    m.select("INBOX")
    out = []
    for frm in SENDERS:
        _, ids = m.search(None, "UNSEEN", "FROM", frm)
        for i in ids[0].split():
            _, d = m.fetch(i, "(RFC822)")      # marque comme lu
            out += parse_message(d[0][1], client)
    m.logout()
    return out
