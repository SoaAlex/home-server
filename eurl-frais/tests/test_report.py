from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from eurl_frais.invoice_parser import OrangeInvoice
from eurl_frais.report_pdf import generate_report_pdf
from eurl_frais.summary import build_summary

FIXED = (("Gaz", Decimal("10.00")), ("Électricité", Decimal("5.00")))


def invoice(ref, total, service="internet"):
    return OrangeInvoice(
        service=service, invoice_id=ref, issue_date=date(2026, 9, 5), period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30), total_ttc=Decimal(total), source_filename=f"{ref}.pdf", sha256=ref,
    )


def summary(invoices, expected=2, problems=()):
    return build_summary(
        2026, 9, [(inv, Path(f"/eurl/CHARGES/ORANGE/{inv.invoice_id}.pdf")) for inv in invoices],
        share=Decimal("0.80"), fixed_charges=FIXED, rent_amount=Decimal("200.00"),
        expected_invoices=expected, problems=problems,
    )


def test_summary_totals_without_vat():
    result = summary([invoice("OR-1", "49.99"), invoice("OR-2", "15.99", "mobile")])

    assert [line.share_amount for line in result.orange_lines] == [Decimal("39.99"), Decimal("12.79")]
    assert result.orange_total == Decimal("52.78")
    assert result.charges_total == Decimal("67.78")
    assert result.grand_total == Decimal("267.78")
    assert result.complete is True
    assert result.charges_note_title == "Charges Septembre"
    assert result.rent_note_title == "Loyer Septembre"
    assert result.reimbursement_month == (2026, 10)


def test_summary_is_incomplete_when_an_expected_invoice_is_missing():
    result = summary([invoice("OR-1", "49.99")])

    assert result.complete is False
    assert "1 facture(s) Orange sur 2" in result.status_text


def test_report_pdf_lists_both_tiime_notes_and_vat_rule(tmp_path):
    output = tmp_path / "RAPPORTS" / "RAPPORT_FRAIS_2026-09.pdf"

    generate_report_pdf(
        summary([invoice("OR-1", "49.99"), invoice("OR-2", "15.99", "mobile")]), output,
        generated_on=date(2026, 10, 10), receipt_name="QUITTANCE DE LOYER SEPTEMBRE 2026.pdf",
        payment_date=date(2026, 10, 6),
    )

    text = " ".join(PdfReader(output).pages[0].extract_text().split())
    assert "COMPLET" in text and "INCOMPLET" not in text
    assert "Charges Septembre" in text and "67,78 €" in text
    assert "Loyer Septembre" in text and "200,00 €" in text
    assert "OR-1" in text and "39,99 €" in text and "12,79 €" in text
    assert "TVA" in text and "HT = TTC" in text
    assert "267,78 €" in text
    assert "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf" in text


def test_report_pdf_flags_incomplete_month_and_problems(tmp_path):
    output = tmp_path / "rapport.pdf"

    generate_report_pdf(
        summary([invoice("OR-1", "49.99")], problems=("scan.pdf : PDF sans texte",)), output,
        generated_on=date(2026, 10, 10), receipt_name=None, payment_date=None,
    )

    text = " ".join(PdfReader(output).pages[0].extract_text().split())
    assert "INCOMPLET" in text
    assert "scan.pdf" in text
