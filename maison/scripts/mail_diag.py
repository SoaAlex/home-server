"""Diagnostic boîte d'alertes : derniers messages (sans les marquer lus), expéditeurs, liens reconnus."""
import email
import imaplib
import os
import re
from email import policy
from email.header import decode_header, make_header
from app.sources import email_alerts

m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
m.select("INBOX", readonly=True)
_, all_ids = m.search(None, "ALL")
_, unseen = m.search(None, "UNSEEN")
ids = all_ids[0].split()
print(f"INBOX : {len(ids)} messages, {len(unseen[0].split())} non lus")
for i in ids[-40:]:
    _, d = m.fetch(i, "(FLAGS BODY.PEEK[])")
    flags = d[0][0].decode()
    msg = email.message_from_bytes(d[0][1], policy=policy.default)
    frm = str(make_header(decode_header(msg["from"] or "")))
    subj = str(make_header(decode_header(msg["subject"] or "")))
    items = email_alerts.parse_message(d[0][1])
    html = email_alerts._html(msg)
    hosts = sorted({h for h in re.findall(r"https?://([^/\"'\s>]+)", html)})[:8]
    print(f"- {msg['date'][:22]} | {'lu' if 'Seen' in flags else 'NON LU'} | {frm[:45]} | {subj[:60]} | annonces reconnues: {len(items)} | hôtes: {hosts}")
m.logout()
