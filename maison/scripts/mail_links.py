"""Sauve quelques e-mails réels comme fixtures et montre où mènent leurs liens."""
import email
import imaplib
import os
import re
from email import policy
from urllib.parse import unquote
import httpx
from app import settings
from app.sources import email_alerts

m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
m.select("INBOX", readonly=True)
saved = {}
for frm in ("seloger", "leboncoin"):
    _, ids = m.search(None, "FROM", frm)
    i = ids[0].split()[-1]
    _, d = m.fetch(i, "(BODY.PEEK[])")
    raw = d[0][1]
    open(f"/app/tests/fixtures/{frm}_mail.eml", "wb").write(raw)
    saved[frm] = raw
m.logout()

c = httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=20)
for frm, raw in saved.items():
    msg = email.message_from_bytes(raw, policy=policy.default)
    html = email_alerts._html(msg)
    links = [l.replace("&amp;", "&") for l in re.findall(r"href=[\"']([^\"']+)", html)]
    print("=====", frm, msg["subject"], len(links), "liens")
    shown = 0
    for l in dict.fromkeys(links):
        if any(k in l for k in ("unsubscribe", "desinscri", "facebook", "twitter", "apple", "google", "mailto")):
            continue
        print("  LINK", l[:220])
        dec = unquote(unquote(l))
        ids = re.findall(r"(?:ad|annonce|listing|id)[=/_-]?(\d{6,})", dec)
        if ids:
            print("     ids dans l'URL :", ids[:3])
        if shown < 6:
            try:
                r = c.get(l, follow_redirects=True)
                print("     ->", r.status_code, str(r.url)[:220], "| hist:", [str(h.headers.get('location'))[:120] for h in r.history][:3])
            except Exception as e:
                print("     -> ERR", type(e).__name__, str(e)[:120])
            shown += 1
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    print("  TEXTE:", text[:1500])
