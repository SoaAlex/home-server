from email.message import EmailMessage
from app.sources.email_alerts import parse_message


def mail(html, subject="Nouvelle annonce"):
    m = EmailMessage()
    m["From"] = "alertes@leboncoin.fr"
    m["Subject"] = subject
    m.set_content("texte")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


def test_leboncoin_links_and_prices():
    html = ('<a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2988776655?utm=x">Maison</a> 410 000 €'
            '<a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2988776655">dup</a>'
            '<a href="https://www.leboncoin.fr/ad/ventes_immobilieres/3011112222">Maison 2</a> 395 000 €')
    items = parse_message(mail(html))
    assert [i["external_id"] for i in items] == ["2988776655", "3011112222"]
    assert items[0]["source"] == "leboncoin" and items[0]["price"] == 410000


def test_seloger_link():
    html = '<a href="https://www.seloger.com/annonces/achat/maison/wambrechies-59/245678901.htm?x=1">M</a>'
    items = parse_message(mail(html))
    assert items[0]["source"] == "seloger" and items[0]["external_id"] == "245678901"
    assert "price" not in items[0] or items[0]["price"] is None
