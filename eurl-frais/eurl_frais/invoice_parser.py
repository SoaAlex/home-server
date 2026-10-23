"""Read the fields we need from an Orange invoice PDF.

Only the TTC total and the issue date are mandatory: the issue month decides the
expense month (invoice received in M = charge of M, reimbursed in M+1). Reference,
period and service are informative. Ambiguous totals are refused, never guessed.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium

from .domain import money

_MONTHS = {
    name: index
    for index, name in enumerate(
        ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
         "septembre", "octobre", "novembre", "decembre"),
        start=1,
    )
}
_MONTH_RE = "|".join(_MONTHS)
_NUM_DATE = r"(\d{2}/\d{2}/\d{4})"
_LONG_DATE = rf"(\d{{1,2}})(?:er)?\s+({_MONTH_RE})\s+(\d{{4}})"
_AMOUNT_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:[ \u00a0\u202f.]\d{3})*(?:[,.]\d{2})|\d+)\s*(?:€|eur\b)", re.I)

# Highest priority first: the amount actually due beats section subtotals.
_TOTAL_LABELS = (
    ("total a payer", "montant total a payer", "net a payer", "montant a payer", "montant a regler"),
    ("montant total ttc", "montant de votre facture", "total de votre facture", "total facture"),
    ("total ttc", "montant ttc", "montant total"),
)
_ISSUE_PATTERNS = (
    rf"date d'emission\s*:?\s*{_NUM_DATE}",
    rf"date de (?:la )?facture\s*:?\s*{_NUM_DATE}",
    rf"emise? le\s+{_NUM_DATE}",
    rf"facture\b[^\n]{{0,60}}?\bdu\s+{_NUM_DATE}",
    rf"date d'emission\s*:?\s*{_LONG_DATE}",
    rf"facture\b[^\n]{{0,60}}?\bdu\s+{_LONG_DATE}",
)
_ID_PATTERNS = (
    r"\bn\s*(?:de\s+)?facture\s*:?\s*([a-z0-9][a-z0-9/_-]{3,})",
    r"\bnumero de facture\s*:?\s*([a-z0-9][a-z0-9/_-]{3,})",
    r"\bfacture\s+n\s*:?\s*([a-z0-9][a-z0-9/_-]{3,})",
    r"\breference (?:de )?(?:la )?facture\s*:?\s*([a-z0-9][a-z0-9/_-]{3,})",
)
_PERIOD_RE = re.compile(rf"\bdu\s+{_NUM_DATE}\s+(?:au|-|–)\s+{_NUM_DATE}")


class InvoiceParseError(ValueError):
    """A required invoice field is missing, unreadable or ambiguous."""


@dataclass(frozen=True)
class ParsedInvoice:
    service: str
    invoice_id: str | None
    issue_date: date
    period_start: date | None
    period_end: date | None
    total_ttc: Decimal


@dataclass(frozen=True)
class OrangeInvoice(ParsedInvoice):
    source_filename: str
    sha256: str


def _normalize(text: str) -> str:
    text = text.replace("°", " ").replace("º", " ").replace("’", "'")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _to_date(match: re.Match[str]) -> date:
    groups = [g for g in match.groups() if g]
    try:
        if len(groups) == 1:
            return datetime.strptime(groups[0], "%d/%m/%Y").date()
        day, month_name, year = groups
        return date(int(year), _MONTHS[month_name], int(day))
    except (ValueError, KeyError) as exc:
        raise InvoiceParseError(f"date de facture invalide: {match.group(0)!r}") from exc


def _issue_date(text: str) -> date:
    for pattern in _ISSUE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return _to_date(match)
    raise InvoiceParseError("date d'émission de la facture introuvable")


def _invoice_id(text: str) -> str | None:
    for pattern in _ID_PATTERNS:
        match = re.search(pattern, text)
        if match and re.search(r"\d", match.group(1)):
            return match.group(1).upper()
    return None


def _total(text: str) -> Decimal:
    lines = text.splitlines()
    for labels in _TOTAL_LABELS:
        found: set[Decimal] = set()
        for line in lines:
            if not any(label in line for label in labels):
                continue
            amounts = _AMOUNT_RE.findall(line)
            if len(amounts) > 1:
                raise InvoiceParseError(f"montant total ambigu sur la ligne: {line.strip()!r}")
            if amounts:
                found.add(money(amounts[0]))
        if len(found) > 1:
            raise InvoiceParseError("montant total ambigu: plusieurs totaux différents")
        if found:
            total = found.pop()
            if total <= 0:
                raise InvoiceParseError("montant total non positif (avoir ?) : traitement manuel")
            return total
    raise InvoiceParseError("montant total TTC introuvable")


def _service(text: str) -> str:
    internet = bool(re.search(r"\b(livebox|fibre|internet|adsl|ligne fixe)\b", text))
    mobile = bool(re.search(r"\b(mobile|forfait|carte sim)\b", text))
    if internet and not mobile:
        return "internet"
    if mobile and not internet:
        return "mobile"
    return "orange"


def parse_invoice_text(raw_text: str) -> ParsedInvoice:
    text = _normalize(raw_text)
    period = _PERIOD_RE.search(text)
    period_start = period_end = None
    if period:
        period_start = datetime.strptime(period.group(1), "%d/%m/%Y").date()
        period_end = datetime.strptime(period.group(2), "%d/%m/%Y").date()
        if period_end < period_start:
            period_start = period_end = None
    return ParsedInvoice(
        service=_service(text),
        invoice_id=_invoice_id(text),
        issue_date=_issue_date(text),
        period_start=period_start,
        period_end=period_end,
        total_ttc=_total(text),
    )


def pdf_text(path: str | Path) -> str:
    """Text layer of every page ('' for scans). pdfium copes with the Orange PDFs that pdfminer chokes on."""
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise InvoiceParseError(f"PDF invalide: {Path(path).name}") from exc
    try:
        return "\n".join(page.get_textpage().get_text_bounded() for page in document)
    finally:
        document.close()


def parse_invoice_pdf(path: str | Path) -> OrangeInvoice:
    source = Path(path)
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise InvoiceParseError(f"PDF illisible: {source.name}") from exc
    if not content.startswith(b"%PDF"):
        raise InvoiceParseError(f"{source.name} n'est pas un PDF")
    text = pdf_text(source)
    if not text.strip():
        raise InvoiceParseError(f"PDF sans texte exploitable (scan ?) : {source.name}")
    parsed = parse_invoice_text(text)
    return OrangeInvoice(
        **parsed.__dict__,
        source_filename=source.name,
        sha256=hashlib.sha256(content).hexdigest(),
    )
