"""Monthly figures shared by the PDF report and the Discord message."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .domain import charges_total, money, month_name_fr, month_period, next_month, share_of
from .invoice_parser import OrangeInvoice


@dataclass(frozen=True)
class OrangeLine:
    invoice: OrangeInvoice
    path: Path
    share_amount: Decimal


@dataclass(frozen=True)
class MonthlySummary:
    year: int
    month: int
    orange_lines: tuple[OrangeLine, ...]
    orange_share: Decimal
    fixed_charges: tuple[tuple[str, Decimal], ...]
    rent_amount: Decimal
    expected_invoices: int
    problems: tuple[str, ...]

    @property
    def month_label(self) -> str:
        return month_name_fr(self.month).capitalize()

    @property
    def period(self) -> tuple[date, date]:
        return month_period(self.year, self.month)

    @property
    def reimbursement_month(self) -> tuple[int, int]:
        return next_month(self.year, self.month)

    @property
    def share_percent(self) -> str:
        return format(self.orange_share * 100, "f").rstrip("0").rstrip(".").replace(".", ",") + " %"

    @property
    def orange_total(self) -> Decimal:
        return money(sum((line.share_amount for line in self.orange_lines), Decimal("0")))

    @property
    def charges_total(self) -> Decimal:
        return charges_total(
            [line.invoice.total_ttc for line in self.orange_lines],
            self.orange_share,
            [amount for _label, amount in self.fixed_charges],
        )

    @property
    def grand_total(self) -> Decimal:
        return money(self.charges_total + self.rent_amount)

    @property
    def complete(self) -> bool:
        return len(self.orange_lines) >= self.expected_invoices

    @property
    def status_text(self) -> str:
        found = len(self.orange_lines)
        text = f"{'COMPLET' if self.complete else 'INCOMPLET'} — {found} facture(s) Orange sur {self.expected_invoices} attendue(s)"
        if found > self.expected_invoices:
            text += " : plus que prévu, à vérifier"
        return text

    @property
    def charges_note_title(self) -> str:
        return f"Charges {self.month_label}"

    @property
    def rent_note_title(self) -> str:
        return f"Loyer {self.month_label}"


def build_summary(
    year: int,
    month: int,
    invoices: Iterable[tuple[OrangeInvoice, Path]],
    *,
    share: Decimal,
    fixed_charges: Iterable[tuple[str, Decimal]],
    rent_amount: Decimal,
    expected_invoices: int,
    problems: Iterable[str] = (),
) -> MonthlySummary:
    lines = tuple(OrangeLine(inv, Path(path), share_of(inv.total_ttc, share)) for inv, path in invoices)
    return MonthlySummary(
        year=year,
        month=month,
        orange_lines=lines,
        orange_share=share,
        fixed_charges=tuple((label, money(amount)) for label, amount in fixed_charges),
        rent_amount=money(rent_amount),
        expected_invoices=expected_invoices,
        problems=tuple(problems),
    )
