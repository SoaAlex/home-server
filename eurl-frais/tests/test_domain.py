from datetime import date
from decimal import Decimal

import pytest

from eurl_frais.domain import (
    ascii_upper,
    charges_total,
    format_eur,
    money,
    month_name_fr,
    month_period,
    next_month,
    parse_year_month,
    previous_month,
    share_of,
)

SHARE = Decimal("0.80")


def test_month_name_is_french_and_rejects_invalid_month():
    assert month_name_fr(9) == "septembre"
    with pytest.raises(ValueError):
        month_name_fr(13)


def test_ascii_upper_strips_accents_for_file_names():
    assert ascii_upper("février") == "FEVRIER"
    assert ascii_upper("août") == "AOUT"
    assert ascii_upper("décembre") == "DECEMBRE"


def test_month_period_uses_28_days_for_february_2026():
    assert month_period(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))


def test_month_period_uses_29_days_for_leap_february():
    assert month_period(2028, 2)[1] == date(2028, 2, 29)


def test_month_period_uses_30_days_for_september():
    assert month_period(2026, 9) == (date(2026, 9, 1), date(2026, 9, 30))


def test_month_period_rejects_month_outside_calendar():
    with pytest.raises(ValueError):
        month_period(2026, 13)


def test_previous_month_rolls_january_back_to_previous_year():
    assert previous_month(date(2027, 1, 10)) == (2026, 12)


def test_next_month_rolls_december_forward_to_next_year():
    assert next_month(2026, 12) == (2027, 1)
    assert next_month(2026, 9) == (2026, 10)


def test_parse_year_month_accepts_iso_month():
    assert parse_year_month("2026-09") == (2026, 9)


@pytest.mark.parametrize("value", ["2026-13", "26-09", "2026/09", "septembre"])
def test_parse_year_month_rejects_other_formats(value):
    with pytest.raises(ValueError):
        parse_year_month(value)


def test_money_parses_french_number_format():
    assert money("1 234,50 €") == Decimal("1234.50")


def test_format_eur_uses_french_separators():
    assert format_eur(Decimal("63.1")) == "63,10\u00a0€"
    assert format_eur(Decimal("1234.5")) == "1\u00a0234,50\u00a0€"


def test_share_of_rounds_to_cents():
    assert share_of(Decimal("49.99"), SHARE) == Decimal("39.99")


@pytest.mark.parametrize("share", [Decimal("0"), Decimal("1.01"), Decimal("-0.5")])
def test_share_of_rejects_share_outside_zero_to_one(share):
    with pytest.raises(ValueError):
        share_of(Decimal("10.00"), share)


def test_charges_total_adds_fixed_amounts_without_any_vat():
    total = charges_total([Decimal("49.99")], SHARE, [Decimal("10.00"), Decimal("5.00")])

    assert total == Decimal("54.99")


def test_each_orange_invoice_is_rounded_before_summing():
    assert charges_total([Decimal("0.03"), Decimal("0.03")], SHARE, []) == Decimal("0.04")
