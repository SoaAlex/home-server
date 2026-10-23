from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from eurl_frais.invoice_parser import OrangeInvoice
from eurl_frais.summary import build_summary

FIXED = (("Gaz", Decimal("10.00")), ("Électricité", Decimal("5.00")))


def invoice(ref, total, service="internet"):
    return OrangeInvoice(
        service=service, invoice_id=ref, issue_date=date(2026, 9, 5), period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30), total_ttc=Decimal(total), source_filename=f"{ref}.pdf", sha256=ref,
    )


def summary(invoices, expected=2, problems=(), share="0.80"):
    return build_summary(
        2026, 9, [(inv, Path(f"/eurl/CHARGES/ORANGE/{inv.invoice_id}.pdf")) for inv in invoices],
        share=Decimal(share), fixed_charges=FIXED, rent_amount=Decimal("200.00"),
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


def test_tiime_comments_one_per_service_in_report_order():
    result = summary([invoice("OR-1", "49.99"), invoice("OR-2", "15.99", "mobile"), invoice("OR-3", "9.99", "mobile")])

    assert result.tiime_comments == ("INTERNET PRORATA 80/20", "MOBILE PRORATA 80/20")


@pytest.mark.parametrize("share,ratio,percent", [("0.755", "75,5/24,5", "75,5 %"), ("1", "100/0", "100 %")])
def test_share_wording_follows_the_configured_share(share, ratio, percent):
    result = summary([invoice("OR-1", "49.99", "orange")], share=share)

    assert result.tiime_comments == (f"ORANGE PRORATA {ratio}",)
    assert result.share_percent == percent
