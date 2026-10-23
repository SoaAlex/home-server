from datetime import date
from decimal import Decimal
from pathlib import Path

from eurl_frais.invoice_parser import OrangeInvoice
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
