import os
from email.message import EmailMessage
import pytest
from app.sources.email_alerts import parse_message, make_resolver

FIX = "tests/fixtures/"
CITIES = ["Wambrechies", "Lambersart", "Croix", "Marcq-en-Barœul", "Lille", "La Madeleine", "Roubaix"]


def mail(html, subject="Nouvelle annonce"):
    m = EmailMessage()
    m["From"] = "alertes@leboncoin.fr"
    m["Subject"] = subject
    m.set_content("texte")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


def test_leboncoin_links_and_cards():
    html = ('<a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2988776655?utm=x">Maison</a> 410 000 € '
            'Maison · 5 pièces · 120 m² Jardin Wambrechies 59118'
            '<a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2988776655">dup</a>'
            '<a href="https://www.leboncoin.fr/vi/3011112222.htm#at_medium=email">Maison 2</a> 395 000 € 3 291 €/m² '
            'Maison · 4 pièces · 120 m² Terrain : 300m² Lambersart 59130')
    items = parse_message(mail(html), cities=CITIES)
    assert [i["external_id"] for i in items] == ["2988776655", "3011112222"]
    a, b = items
    assert a["source"] == "leboncoin" and a["price"] == 410000 and a["city"] == "Wambrechies" and a["has_garden"]
    assert b["url"] == "https://www.leboncoin.fr/vi/3011112222.htm" and b["price"] == 395000
    assert b["land_m2"] == 300 and b["surface_m2"] == 120 and b["rooms"] == 4 and b["postal_code"] == "59130"


def test_seloger_tracking_links_resolved_without_visiting_portal():
    target = "https://www.seloger.com/annonce/achat/hauts-de-france/nord-59/lille-59000/26UQCXVM9PZQ?utm_source=x"
    html = ('<a href="https://click.by.seloger.com/?qs=AAA">575 000 € 3 833,33 €/m²</a>'
            '<a href="https://click.by.seloger.com/?qs=BBB">6 pièces · 150 m² · 59 m²</a>'
            '<a href="https://click.by.seloger.com/?qs=CCC">Saint Maurice-Pellevoisin, Lille (59800)</a>'
            '<a href="https://click.by.seloger.com/?qs=ZZZ">Gérer mes alertes</a>')
    seen = []

    class Client:
        def get(self, url, follow_redirects=True, timeout=None):
            seen.append((url, follow_redirects))
            loc = "https://www.seloger.com/mes-alertes" if url.endswith("ZZZ") else target
            return type("R", (), {"headers": {"location": loc}})()

    items = parse_message(mail(html), make_resolver(Client()), CITIES)
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "seloger" and it["external_id"] == "26UQCXVM9PZQ"
    assert it["price"] == 575000 and it["surface_m2"] == 150 and it["rooms"] == 6
    assert it["city"] == "Lille" and it["postal_code"] == "59800"
    assert all(not f for _, f in seen) and all("seloger.com/annonce" not in u for u, _ in seen)


@pytest.mark.skipif(not os.path.exists(FIX + "leboncoin_mail.eml"), reason="fixture privée")
def test_real_leboncoin_mail():
    items = parse_message(open(FIX + "leboncoin_mail.eml", "rb").read(), cities=CITIES)
    assert [i["external_id"] for i in items] == ["3286052170", "3250796215", "3285994202"]
    assert [i["price"] for i in items] == [366000, 539900, 280000]
    assert [i["city"] for i in items] == ["Croix", "Marcq-en-Barœul", "Croix"]
    assert items[1]["land_m2"] == 535 and items[0]["surface_m2"] == 125


@pytest.mark.skipif(not os.path.exists(FIX + "leboncoin_mail.eml"), reason="fixture privée")
def test_real_mails_carry_ad_thumbnails():
    lbc = parse_message(open(FIX + "leboncoin_mail.eml", "rb").read(), cities=CITIES)
    photos = [i["photo_urls"] for i in lbc]
    assert all(len(p) == 1 and "img.leboncoin.fr/api/" in p[0] for p in photos)
    assert len({p[0] for p in photos}) == 3                     # une vignette différente par annonce

    class Client:   # le 1er lien (logo) mène à l'accueil, les autres à l'annonce
        n = 0

        def get(self, url, **_):
            Client.n += 1
            loc = ("https://www.seloger.com/" if Client.n == 1 else
                   "https://www.seloger.com/annonce/achat/hauts-de-france/nord-59/lille-59000/26UQCXVM9PZQ")
            return type("R", (), {"headers": {"location": loc}})()

    sl = parse_message(open(FIX + "seloger_mail.eml", "rb").read(), make_resolver(Client()), CITIES)
    assert len(sl) == 1 and len(sl[0]["photo_urls"]) == 1 and "w=540" in sl[0]["photo_urls"][0]
