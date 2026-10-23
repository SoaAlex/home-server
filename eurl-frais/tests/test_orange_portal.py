from datetime import date

import pytest

from eurl_frais.orange_portal import ManualAuthRequired, _service_from_labels, invoice_label_date, manual_auth_required


@pytest.mark.parametrize("labels,service", [
    # Labels of the « Factures » links seen on espace-client.orange.fr (23/10/2026)
    ("Consulter votre facture Forfait 100Go 5G | Factures6 octobre 2026 : 21,00 €Consultez, gérez", "mobile"),
    ("Consulter votre facture Livebox Fibre | Factures5 octobre 2026 : 33,33 €", "internet"),
    ("Factures de Forfait mobile de Test", "mobile"),
    ("Factures de Offre internet de Test", "internet"),
    ("Factures 6 octobre 2026 : 21,00 €", "orange"),
])
def test_service_from_labels(labels, service):
    assert _service_from_labels(labels) == service


@pytest.mark.parametrize("label,expected", [
    ("Voir la facture du 7 septembre 2026 au format PDF", date(2026, 9, 7)),
    ("Voir la facture du 1er août 2026 au format PDF", date(2026, 8, 1)),
    ("Voir la facture du 8 décembre 2025 au format PDF", date(2025, 12, 8)),
    ("Consulter la dernière facture", None),
    ("Voir la facture du 7 brumaire 2026 au format PDF", None),
])
def test_invoice_label_date(label, expected):
    assert invoice_label_date(label) == expected


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
