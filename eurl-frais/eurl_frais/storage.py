"""Archive files under the EURL folders without ever overwriting different content."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .invoice_parser import InvoiceParseError, OrangeInvoice, parse_invoice_pdf

FILE_MODE = 0o660  # same group access as the existing OneDrive documents


class InvoiceConflict(RuntimeError):
    """A file with the same canonical name already exists with other bytes."""


@dataclass(frozen=True)
class StoredFile:
    path: Path
    created: bool


def _safe(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_value).strip("-_")[:80]


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
    reference = _safe(invoice.invoice_id or "") or digest[:10]
    issued = invoice.issue_date
    destination = Path(orange_dir) / f"{issued:%Y-%m}_Orange_{invoice.service}_{reference}.pdf"
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            return StoredFile(destination, False)
        raise InvoiceConflict(f"{destination.name} existe déjà avec un contenu différent")
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
        key=lambda p: (not re.match(r"\d{4}-\d{2}_Orange_", p.name), p.name),
    )
    for path in paths:
        try:
            invoice = parse_invoice_pdf(path)
        except InvoiceParseError as exc:
            problems.append(f"{path.name} : {exc}")
            continue
        if (invoice.issue_date.year, invoice.issue_date.month) == (year, month):
            found.setdefault(invoice.sha256, (invoice, path))
    return sorted(found.values(), key=lambda item: (item[0].issue_date, item[1].name)), problems
