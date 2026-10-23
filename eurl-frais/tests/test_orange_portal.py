import pytest

from eurl_frais.orange_portal import ManualAuthRequired, manual_auth_required, month_matches, select_invoice_links


def test_month_matches_french_names_and_numeric_dates():
    assert month_matches("Facture du 5 septembre 2026", 2026, 9)
    assert month_matches("Facture du 05/09/2026 - 56,22 €", 2026, 9)
    assert month_matches("Facture août 2026", 2026, 8)
    assert month_matches("facture aout 2026", 2026, 8)
    assert not month_matches("Facture du 5 septembre 2025", 2026, 9)
    assert not month_matches("Facture du 05/10/2026", 2026, 9)


def test_selects_only_download_links_of_the_requested_month():
    links = [
        {"index": 0, "href": "https://espace-client.orange.fr/x/facture.pdf?id=1", "text": "Télécharger",
         "context": "Facture du 05/09/2026 56,22 €"},
        {"index": 1, "href": "https://espace-client.orange.fr/x/facture.pdf?id=0", "text": "Télécharger",
         "context": "Facture du 05/08/2026 56,22 €"},
        {"index": 2, "href": "https://espace-client.orange.fr/x/detail", "text": "Voir le détail",
         "context": "Facture du 05/09/2026 56,22 €"},
        {"index": 3, "href": "", "text": "Télécharger la facture (PDF)", "context": "Facture mobile du 12 septembre 2026"},
    ]

    selected = select_invoice_links(links, 2026, 9)

    assert [link["index"] for link in selected] == [0, 3]


@pytest.mark.parametrize("url,text", [
    ("https://login.orange.fr/front/otp", "Saisissez le code reçu par SMS"),
    ("https://login.orange.fr/", "Vérifiez que vous n'êtes pas un robot (captcha)"),
    ("https://login.orange.fr/", "Validez la connexion sur votre mobile avec Mobile Connect"),
    ("https://login.orange.fr/orange-et-moi", "Identification avec votre mobile. Pour vous identifier, nous allons "
     "envoyer une demande de connexion sur votre mobile de confiance. Envoyer la demande"),
])
def test_mfa_captcha_and_mobile_validation_require_a_human(url, text):
    with pytest.raises(ManualAuthRequired):
        manual_auth_required(url, text)


def test_plain_login_page_is_not_manual_auth():
    assert manual_auth_required("https://login.orange.fr/front/login", "Identifiez-vous avec votre adresse e-mail") is False
