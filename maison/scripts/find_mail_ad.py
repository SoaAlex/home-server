"""Cherche une annonce leboncoin par son identifiant dans la boîte d'alertes (sans marquer lu)."""
import email
import imaplib
import os
import re
import sys
from email import policy

ad = sys.argv[1]
m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
m.select("INBOX", readonly=True)
_, ids = m.search(None, "TEXT", ad)
uids = ids[0].split()
print(f"{len(uids)} message(s) contenant {ad}")
from app.sources.email_alerts import parse_message
for i in uids[-3:]:
    _, d = m.fetch(i, "(BODY.PEEK[])")
    raw = d[0][1]
    msg = email.message_from_bytes(raw, policy=policy.default)
    print("---", msg["date"], "|", msg["subject"])
    items = parse_message(raw, cities=[])
    for it in items:
        if it["external_id"] == ad:
            print("TROUVÉ :", {k: it.get(k) for k in ("source", "url", "title", "price", "surface_m2", "land_m2",
                                                      "rooms", "bedrooms", "city", "postal_code", "photo_urls")})
            print("CARTE :", (it.get("raw") or {}).get("card"))
m.logout()
