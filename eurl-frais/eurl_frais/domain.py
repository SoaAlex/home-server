"""Pure calendar and money rules (no I/O, no personal amounts)."""
from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

CENT = Decimal("0.01")
_MONTHS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def money(value: str | Decimal | int) -> Decimal:
    """Parse '1 234,50 €' / '56.22' / Decimal into a Decimal rounded to cents."""
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip()
        for char in ("\u00a0", "\u202f", " ", "€"):
            text = text.replace(char, "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            raise ValueError(f"Montant invalide: {value!r}")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Montant invalide: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"Montant non fini: {value!r}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_eur(amount: Decimal) -> str:
    """French display: 1 234,50 € (no-break spaces, present in standard PDF fonts)."""
    value = money(amount)
    sign = "-" if value < 0 else ""
    units, cents = f"{abs(value):.2f}".split(".")
    grouped = f"{int(units):,}".replace(",", "\u00a0")
    return f"{sign}{grouped},{cents}\u00a0€"


def month_name_fr(month: int) -> str:
    if not 1 <= month <= 12:
        raise ValueError("Le mois doit être compris entre 1 et 12")
    return _MONTHS_FR[month - 1]


def ascii_upper(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def month_period(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise ValueError("Le mois doit être compris entre 1 et 12")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def previous_month(on_date: date) -> tuple[int, int]:
    previous = date(on_date.year, on_date.month, 1) - timedelta(days=1)
    return previous.year, previous.month


def next_month(year: int, month: int) -> tuple[int, int]:
    month_period(year, month)  # validates the month
    return (year + 1, 1) if month == 12 else (year, month + 1)


def orange_invoice_month(year: int, month: int) -> tuple[int, int]:
    """For expenses of M, select invoices issued in M+1 (the regular report's current month).
    An explicit historical expense month keeps the same mapping, even when rerun later."""
    return next_month(year, month)


def format_percent(value: Decimal) -> str:
    """80.00 -> '80', 75.50 -> '75,5', 100 -> '100' (French decimal comma, no trailing zeros)."""
    return f"{Decimal(value).quantize(CENT):f}".rstrip("0").rstrip(".").replace(".", ",")


def parse_year_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Mois attendu au format AAAA-MM: {value!r}")
    year, month = int(match.group(1)), int(match.group(2))
    month_period(year, month)
    return year, month


def share_of(amount: Decimal, share: Decimal) -> Decimal:
    """EURL share of one invoice, rounded half-up to the cent (no VAT logic)."""
    if not Decimal("0") < share <= Decimal("1"):
        raise ValueError("La quote-part doit être comprise entre 0 (exclu) et 1")
    return (money(amount) * share).quantize(CENT, rounding=ROUND_HALF_UP)


def charges_total(
    orange_amounts: Iterable[Decimal], share: Decimal, fixed_amounts: Iterable[Decimal]
) -> Decimal:
    """Each Orange invoice is rounded on its own, then fixed monthly amounts are added."""
    total = sum((share_of(amount, share) for amount in orange_amounts), Decimal("0.00"))
    total += sum((money(amount) for amount in fixed_amounts), Decimal("0.00"))
    return money(total)
