"""Archive files under the EURL folders without ever overwriting different content."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .domain import money
from .invoice_parser import InvoiceParseError, OrangeInvoice, parse_invoice_pdf

FILE_MODE = 0o660  # same group access as the existing OneDrive documents


class InvoiceConflict(RuntimeError):
    """A file with the same canonical name already exists with other bytes."""


@dataclass(frozen=True)
class StoredFile:
    path: Path
    created: bool


# Archive name = metadata (issue date, service, TTC amount), readable by the user and by collect_month_invoices
# without parsing the PDF: Orange PDFs have a column layout, and a renamed manual drop works the same way.
# Current form « 2026-10-05 - Facture Orange internet fibre 43,35€.pdf » (CLOUD_NAS naming since 2026-10-30);
# the legacy « 2026-10-05_Orange_internet_43,35EUR.pdf » is still read.
CANONICAL_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) - Facture Orange(?: (internet fibre|mobile))? (\d+,\d{2})€\.pdf$")
LEGACY_CANONICAL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_Orange_(internet|mobile|orange)_(\d+,\d{2})EUR\.pdf$")
SERVICE_NAMES = {"internet": "internet fibre", "mobile": "mobile"}
RENAME_HINT = ("renommer le PDF en « AAAA-MM-JJ - Facture Orange <internet fibre|mobile> <montant TTC>€.pdf » "
               "pour le prendre en compte")


def _amount(invoice: OrangeInvoice) -> str:
    return f"{invoice.total_ttc:.2f}".replace(".", ",")


def canonical_name(invoice: OrangeInvoice) -> str:
    service = SERVICE_NAMES.get(invoice.service)
    label = f"Facture Orange {service}" if service else "Facture Orange"
    return f"{invoice.issue_date:%Y-%m-%d} - {label} {_amount(invoice)}€.pdf"


def legacy_canonical_name(invoice: OrangeInvoice) -> str:
    return f"{invoice.issue_date:%Y-%m-%d}_Orange_{invoice.service}_{_amount(invoice)}EUR.pdf"


def _match(name: str) -> re.Match[str] | None:
    return CANONICAL_RE.match(name) or LEGACY_CANONICAL_RE.match(name)


def _from_canonical_name(path: Path, match: re.Match[str]) -> OrangeInvoice:
    content = path.read_bytes()
    if not content.startswith(b"%PDF"):
        raise InvoiceParseError("ce fichier n'est pas un PDF")
    year, month, day, service, amount = match.groups()
    service = {"internet fibre": "internet", None: "orange"}.get(service, service)
    try:
        issued = date(int(year), int(month), int(day))
    except ValueError as exc:
        raise InvoiceParseError("date invalide dans le nom du fichier") from exc
    return OrangeInvoice(
        service=service, invoice_id=None, issue_date=issued, period_start=None, period_end=None,
        total_ttc=money(amount), source_filename=path.name, sha256=hashlib.sha256(content).hexdigest(),
    )


def write_atomic(path: Path, content: bytes) -> None:
    """Write next to the target then rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".tmp-", suffix=path.suffix, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def store_orange_pdf(invoice: OrangeInvoice, content: bytes, orange_dir: str | Path) -> StoredFile:
    if not content.startswith(b"%PDF"):
        raise ValueError("Le document Orange téléchargé n'est pas un PDF")
    digest = hashlib.sha256(content).hexdigest()
    if invoice.sha256 and invoice.sha256 != digest:
        raise InvoiceConflict("Le PDF ne correspond pas à la facture analysée")
    destination = Path(orange_dir) / canonical_name(invoice)
    legacy = Path(orange_dir) / legacy_canonical_name(invoice)
    if not destination.exists() and legacy.exists():
        destination = legacy
    if destination.exists():
        # Same issue date, service and amount = same invoice. Orange regenerates some PDFs on each
        # download (different bytes): the copy already archived (or dropped by hand) is kept untouched.
        return StoredFile(destination, False)
    write_atomic(destination, content)
    return StoredFile(destination, True)


def collect_month_invoices(
    orange_dir: str | Path, year: int, month: int
) -> tuple[list[tuple[OrangeInvoice, Path]], list[str]]:
    """Invoices issued in (year, month) found in the archive, deduplicated by content.

    Any PDF dropped by hand in the folder is taken into account too, which is the
    fallback when the Orange portal cannot be automated.
    """
    root = Path(orange_dir)
    if not root.is_dir():
        return [], []
    found: dict[str, tuple[OrangeInvoice, Path]] = {}
    problems: list[str] = []
    # Canonical names first so they win over manual copies of the same file.
    paths = sorted(
        (p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith(".")),
        key=lambda p: (not _match(p.name), p.name),
    )
    for path in paths:
        match = _match(path.name)
        try:
            invoice = _from_canonical_name(path, match) if match else parse_invoice_pdf(path)
        except (InvoiceParseError, OSError) as exc:
            problems.append(f"{path.name} : {exc} — {RENAME_HINT}")
            continue
        if (invoice.issue_date.year, invoice.issue_date.month) == (year, month):
            found.setdefault(invoice.sha256, (invoice, path))
    return sorted(found.values(), key=lambda item: (item[0].issue_date, item[1].name)), problems
