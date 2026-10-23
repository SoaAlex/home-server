"""Monthly run: Orange invoices -> archive -> rent receipt -> report -> Discord."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterator

from .config import Config
from .discord_delivery import send_discord
from .domain import format_eur, month_name_fr
from .invoice_parser import InvoiceParseError, OrangeInvoice, pdf_text
from .orange_portal import OrangePortalError, download_invoices
from .rent_pdf import default_payment_date, generate_rent_receipt, rent_receipt_filename, template_rent_amount
from .storage import InvoiceConflict, collect_month_invoices, store_orange_pdf
from .summary import MonthlySummary, build_summary

EXIT_OK = 0
EXIT_INCOMPLETE = 3
SERVICE_LABELS = {"internet": "Orange Internet", "mobile": "Orange Mobile"}


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "run.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Un autre traitement eurl-frais est déjà en cours") from None
        yield


def _state_path(config: Config, kind: str, year: int, month: int) -> Path:
    return config.state_dir / kind / f"{year:04d}-{month:02d}.json"


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(path, 0o600)


def _quarantine(config: Config, path: Path) -> None:
    folder = config.state_dir / "orange" / "a-verifier"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, folder / path.name)


def fetch_orange(config: Config, year: int, month: int, downloader=download_invoices, *, debug: bool = False):
    """Download the month's invoices and archive them. Returns (new files, problems).

    Date, service and amount come from the customer area; when the PDF has a text layer it must contain
    that amount, otherwise the file is quarantined instead of being counted.
    """
    archived: list[str] = []
    problems: list[str] = []
    work = config.state_dir / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        found = downloader(config.orange_username, config.orange_password, config.state_dir / "orange",
                           Path(tmp), year, month, debug=debug)
        if not found:
            problems.append(f"Orange : aucune facture de {month_name_fr(month)} {year} sur l'espace client")
        seen: set[tuple] = set()
        for item in found:
            label = f"{SERVICE_LABELS.get(item.service, 'Orange')} du {item.issue_date:%d/%m/%Y}"
            amount = f"{item.total_ttc:.2f}".replace(".", ",")
            key = (item.service, item.issue_date, item.total_ttc)
            if key in seen:  # same archive name: the second one would be silently dropped
                _quarantine(config, item.path)
                problems.append(f"{label} : deux factures de {amount} € le même jour — la seconde n'est pas comptée, "
                                "copie dans state/orange/a-verifier (la renommer à la main si elle est distincte)")
                continue
            seen.add(key)
            try:
                text = re.sub(r"\s", "", pdf_text(item.path))
            except InvoiceParseError as exc:
                _quarantine(config, item.path)
                problems.append(f"{label} : {exc} — copie dans state/orange/a-verifier")
                continue
            if text and amount.replace(" ", "") not in text:
                _quarantine(config, item.path)
                problems.append(f"{label} : montant {amount} € de l'espace client absent du PDF — "
                                "copie dans state/orange/a-verifier, non comptée")
                continue
            content = item.path.read_bytes()
            invoice = OrangeInvoice(
                service=item.service, invoice_id=None, issue_date=item.issue_date, period_start=None,
                period_end=None, total_ttc=item.total_ttc, source_filename=item.path.name,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            try:
                stored = store_orange_pdf(invoice, content, config.orange_dir)
            except (InvoiceConflict, ValueError) as exc:
                _quarantine(config, item.path)
                problems.append(f"{label} : {exc} — copie dans state/orange/a-verifier")
                continue
            if stored.created:
                archived.append(stored.path.name)
    return archived, problems


def discord_message(summary: MonthlySummary, *, receipt_name: str | None, payment_date: date) -> str:
    pay_year, pay_month = summary.reimbursement_month
    ok = summary.complete and receipt_name is not None
    lines = [
        f"**Frais EURL — {summary.month_label} {summary.year}** · à saisir dans Tiime en {month_name_fr(pay_month)} {pay_year}",
        f"{'✅' if ok else '⚠️'} {summary.status_text}",
        "",
        f"**Note « {summary.charges_note_title} » : {format_eur(summary.charges_total)}** (HT = TTC, TVA 0)",
    ]
    for line in summary.orange_lines:
        invoice = line.invoice
        number = f" n° {invoice.invoice_id}" if invoice.invoice_id else ""
        lines.append(
            f"• {SERVICE_LABELS.get(invoice.service, 'Orange')} — facture{number} du "
            f"{invoice.issue_date:%d/%m/%Y} : {format_eur(invoice.total_ttc)} → {format_eur(line.share_amount)} "
            f"({summary.share_percent})"
        )
    lines += [f"• {label} (prorata échéancier) : {format_eur(amount)}" for label, amount in summary.fixed_charges]
    rent = f"**Note « {summary.rent_note_title} » : {format_eur(summary.rent_amount)}** (HT = TTC, TVA 0)"
    rent += f" — quittance jointe, paiement du {payment_date:%d/%m/%Y}" if receipt_name else " — quittance NON émise"
    total_label = "Total remboursé par l'EURL" if summary.complete else "Total provisoire (facture Orange manquante)"
    lines += [rent, f"**{total_label} : {format_eur(summary.grand_total)}**"]
    if summary.problems:
        lines += ["", "⚠️ Points d'attention :"] + [f"• {problem}" for problem in summary.problems]
    if receipt_name or summary.orange_lines:
        lines += ["", "📎 Justificatifs joints : quittance et factures Orange (archivés dans EURL/LOYER et EURL/CHARGES/ORANGE)."]
    return "\n".join(lines)


def run_monthly(
    config: Config,
    *,
    year: int,
    month: int,
    today: date,
    payment_date: date | None = None,
    dry_run: bool = False,
    notify: bool = True,
    use_orange: bool = True,
    resend: bool = False,
    debug: bool = False,
    downloader=download_invoices,
    sender=send_discord,
    out: Callable[[str], None] = print,
) -> int:
    config.require_amounts()
    sent_path = _state_path(config, "sent", year, month)
    with run_lock(config.state_dir):
        if sent_path.exists() and not (dry_run or resend):
            sent_at = _read_state(sent_path).get("sent_at", "?")
            out(f"Frais {year}-{month:02d} déjà envoyés sur Discord ({sent_at}) : rien à refaire (--resend pour renvoyer).")
            return EXIT_OK

        problems: list[str] = []
        if not dry_run:
            config.orange_dir.mkdir(parents=True, exist_ok=True)  # also the manual drop-off folder
        if use_orange and not dry_run:
            if config.has_orange_credentials:
                try:
                    archived, orange_problems = fetch_orange(config, year, month, downloader, debug=debug)
                    problems += orange_problems
                    out(f"Orange : {len(archived)} nouvelle(s) facture(s) archivée(s) {archived}")
                except OrangePortalError as exc:
                    problems.append(f"Orange : {exc}")
                except Exception as exc:  # never lose the rent receipt because of the portal
                    problems.append(f"Orange : erreur inattendue ({type(exc).__name__}: {str(exc)[:200]})")
            else:
                problems.append("Orange : identifiants absents du .env — seules les factures déjà déposées "
                                "dans CHARGES/ORANGE sont prises en compte")
        invoices, parse_problems = collect_month_invoices(config.orange_dir, year, month)
        problems += parse_problems

        rent_amount = template_rent_amount(config.template_path)
        receipt_path = config.loyer_dir / rent_receipt_filename(year, month)
        receipt_state = _state_path(config, "receipts", year, month)
        recorded = _read_state(receipt_state)
        payment = payment_date or (
            date.fromisoformat(recorded["payment_date"]) if "payment_date" in recorded
            else default_payment_date(year, month, config.rent_payment_day)
        )
        receipt_ready = receipt_path.exists()
        if receipt_ready:
            out(f"Quittance déjà présente, conservée telle quelle : {receipt_path.name}")
        elif today < payment:
            problems.append(f"Quittance non émise : paiement du loyer prévu le {payment:%d/%m/%Y}")
        elif dry_run:
            out(f"[simulation] quittance à émettre : {receipt_path.name} (paiement {payment:%d/%m/%Y}, émise le {today:%d/%m/%Y})")
        else:
            generate_rent_receipt(config.template_path, receipt_path, year, month, issued_on=today, payment_date=payment)
            _write_state(receipt_state, {"payment_date": payment.isoformat(), "issued_on": today.isoformat()})
            receipt_ready = True
            out(f"Quittance émise : {receipt_path.name}")

        summary = build_summary(
            year, month, invoices, share=config.orange_share, fixed_charges=config.fixed_charges,
            rent_amount=rent_amount, expected_invoices=config.orange_expected_invoices, problems=problems,
        )
        message = discord_message(summary, receipt_name=receipt_path.name if receipt_ready else None, payment_date=payment)
        if dry_run:
            out("[simulation] aucun fichier écrit, aucun envoi Discord.\n" + message)
            return EXIT_OK if summary.complete else EXIT_INCOMPLETE

        complete = summary.complete and receipt_ready
        if notify:  # the Discord message is the report; PDFs attached are the receipts for Tiime
            files = ([receipt_path] if receipt_ready else []) + [line.path for line in summary.orange_lines]
            sender(config.discord_webhook_url, message, files)
            if complete:
                _write_state(sent_path, {"sent_at": datetime.now().isoformat(timespec="seconds"),
                                         "charges_total": str(summary.charges_total),
                                         "rent": str(summary.rent_amount)})
        out(message)
        return EXIT_OK if complete else EXIT_INCOMPLETE
