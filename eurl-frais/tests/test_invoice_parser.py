from datetime import date
from decimal import Decimal

import pytest
from reportlab.pdfgen import canvas

from eurl_frais.invoice_parser import InvoiceParseError, parse_invoice_pdf, parse_invoice_text


def make_pdf(path, lines):
    pdf = canvas.Canvas(str(path))
    text = pdf.beginText(40, 760)
    text.setFont("Helvetica", 10)
    for line in lines:
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()
    return path


def test_parses_pdf_metadata_amount_and_hash(tmp_path):
    path = make_pdf(tmp_path / "orange.pdf", [
        "Facture n° OR-2026-09123",
        "Date d'émission : 05/09/2026",
        "Période de facturation : du 01/09/2026 au 30/09/2026",
        "Abonnement Livebox Fibre",
        "Montant total TTC : 39,99 €",
    ])

    invoice = parse_invoice_pdf(path)

    assert invoice.service == "internet"
    assert invoice.invoice_id == "OR-2026-09123"
    assert invoice.issue_date == date(2026, 9, 5)
    assert (invoice.period_start, invoice.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert invoice.total_ttc == Decimal("39.99")
    assert len(invoice.sha256) == 64


def test_parses_long_french_issue_date_without_reference_or_period():
    parsed = parse_invoice_text("Votre facture mobile du 5 septembre 2026\nTotal à payer : 56,22 €")

    assert parsed.issue_date == date(2026, 9, 5)
    assert parsed.invoice_id is None
    assert parsed.period_start is None
    assert parsed.service == "mobile"
    assert parsed.total_ttc == Decimal("56.22")


def test_amount_due_label_wins_over_section_totals():
    parsed = parse_invoice_text(
        "Facture du 05/09/2026\nTotal TTC abonnements : 40,00 €\nTotal TTC options : 16,22 €\n"
        "Total à payer : 56,22 €"
    )

    assert parsed.total_ttc == Decimal("56.22")


def test_repeated_label_with_same_amount_is_accepted():
    parsed = parse_invoice_text("Facture du 05/09/2026\nTotal à payer : 56,22 €\nTotal à payer 56,22 €")

    assert parsed.total_ttc == Decimal("56.22")


def test_conflicting_amounts_for_the_same_label_are_ambiguous():
    with pytest.raises(InvoiceParseError, match="ambigu"):
        parse_invoice_text("Facture du 05/09/2026\nTotal TTC : 40,00 €\nTotal TTC : 16,22 €")


def test_two_amounts_on_the_total_line_are_ambiguous():
    with pytest.raises(InvoiceParseError, match="ambigu"):
        parse_invoice_text("Facture du 05/09/2026\nMontant total : 39,99 € dont remise 5,00 €")


def test_missing_total_is_rejected():
    with pytest.raises(InvoiceParseError, match="montant"):
        parse_invoice_text("Facture du 05/09/2026\nAbonnement Livebox")


def test_missing_issue_date_is_rejected():
    with pytest.raises(InvoiceParseError, match="date"):
        parse_invoice_text("Abonnement Livebox\nTotal à payer : 56,22 €")


def test_invoice_mentioning_internet_and_mobile_is_labelled_orange():
    parsed = parse_invoice_text("Facture du 05/09/2026\nLivebox Fibre + forfait mobile\nTotal à payer : 80,00 €")

    assert parsed.service == "orange"


def test_non_pdf_file_is_rejected(tmp_path):
    path = tmp_path / "facture.pdf"
    path.write_text("not a pdf")

    with pytest.raises(InvoiceParseError, match="PDF"):
        parse_invoice_pdf(path)
