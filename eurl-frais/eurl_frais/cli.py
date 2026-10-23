"""Command line: `python -m eurl_frais monthly|quittance|orange` (see README)."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from .config import Config, ConfigError, load_config
from .discord_delivery import send_discord
from .domain import parse_year_month, previous_month
from .monthly import EXIT_INCOMPLETE, EXIT_OK, fetch_orange, run_lock, run_monthly
from .orange_portal import OrangePortalError, download_invoices
from .rent_pdf import default_payment_date, generate_rent_receipt, rent_receipt_filename
from .storage import collect_month_invoices


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eurl-frais", description="Frais mensuels EURL : Orange, quittance, rapport.")
    commands = parser.add_subparsers(dest="command", required=True)

    monthly = commands.add_parser("monthly", help="traitement mensuel complet (défaut : frais du mois précédent)")
    monthly.add_argument("--month", help="mois des frais AAAA-MM (défaut : mois précédent)")
    monthly.add_argument("--payment-date", type=date.fromisoformat, help="date réelle du paiement du loyer AAAA-MM-JJ")
    monthly.add_argument("--dry-run", action="store_true", help="simulation : n'écrit rien, n'envoie rien")
    monthly.add_argument("--no-notify", action="store_true", help="ne pas publier sur Discord")
    monthly.add_argument("--skip-orange", action="store_true", help="ne pas se connecter à Orange (PDF déjà déposés)")
    monthly.add_argument("--resend", action="store_true", help="republier même si le mois a déjà été envoyé")
    monthly.add_argument("--debug", action="store_true", help="captures du portail Orange dans state/orange/debug")

    receipt = commands.add_parser("quittance", help="émettre une quittance (ex. copie corrigée), jamais d'écrasement")
    receipt.add_argument("--month", required=True, help="mois du loyer AAAA-MM")
    receipt.add_argument("--payment-date", type=date.fromisoformat)
    receipt.add_argument("--issued-on", type=date.fromisoformat, help="date d'émission (défaut : aujourd'hui)")
    receipt.add_argument("--output", help="chemin sous EURL, ex. 'LOYER/QUITTANCE DE LOYER FEVRIER - CORRIGEE.pdf'")

    orange = commands.add_parser("orange", help="télécharger et archiver seulement les factures Orange d'un mois")
    orange.add_argument("--month", help="mois AAAA-MM (défaut : mois précédent)")
    orange.add_argument("--debug", action="store_true", help="captures du portail Orange dans state/orange/debug")
    return parser


def _month(value: str | None, today: date) -> tuple[int, int]:
    return parse_year_month(value) if value else previous_month(today)


def _output_path(config: Config, value: str) -> Path | None:
    """Only LOYER/... or CHARGES/... PDF paths, no traversal."""
    parts = PurePosixPath(value).parts
    roots = {"LOYER": config.loyer_dir, "CHARGES": config.charges_dir}
    if len(parts) < 2 or parts[0] not in roots or ".." in parts or not value.lower().endswith(".pdf"):
        return None
    return roots[parts[0]].joinpath(*parts[1:])


def _quittance(config: Config, args, today: date) -> int:
    year, month = parse_year_month(args.month)
    payment = args.payment_date or default_payment_date(year, month, config.rent_payment_day)
    output = config.loyer_dir / rent_receipt_filename(year, month)
    if args.output:
        output = _output_path(config, args.output)
        if output is None:
            print("Erreur : --output doit être un PDF sous LOYER/ ou CHARGES/ (sans '..').")
            return 2
    with run_lock(config.state_dir):
        generate_rent_receipt(config.template_path, output, year, month,
                              issued_on=args.issued_on or today, payment_date=payment)
    print(f"Quittance écrite : {output}")
    return EXIT_OK


def _orange(config: Config, args, today: date, downloader) -> int:
    if not config.has_orange_credentials:
        print("Erreur : ORANGE_USERNAME / ORANGE_PASSWORD absents du .env")
        return 2
    year, month = _month(args.month, today)
    with run_lock(config.state_dir):
        archived, problems = fetch_orange(config, year, month, downloader, debug=args.debug)
    invoices, parse_problems = collect_month_invoices(config.orange_dir, year, month)
    print(f"Nouvelles factures archivées : {archived or 'aucune'}")
    for invoice, path in invoices:
        print(f"- {path.name} : {invoice.service}, facture {invoice.invoice_id or 'sans n°'} du "
              f"{invoice.issue_date:%d/%m/%Y}, {invoice.total_ttc} € TTC")
    for problem in problems + parse_problems:
        print(f"! {problem}")
    return EXIT_OK if len(invoices) >= config.orange_expected_invoices else EXIT_INCOMPLETE


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    today: date | None = None,
    downloader=download_invoices,
    sender=send_discord,
) -> int:
    args = _parser().parse_args(argv)
    today = today or datetime.now(ZoneInfo("Europe/Paris")).date()
    try:
        config = load_config(env)
        if args.command == "quittance":
            return _quittance(config, args, today)
        if args.command == "orange":
            return _orange(config, args, today, downloader)
        year, month = _month(args.month, today)
        return run_monthly(
            config, year=year, month=month, today=today, payment_date=args.payment_date, dry_run=args.dry_run,
            notify=not args.no_notify, use_orange=not args.skip_orange, resend=args.resend, debug=args.debug,
            downloader=downloader, sender=sender,
        )
    except ConfigError as exc:
        print(f"Configuration : {exc}")
        return 2
    except (OrangePortalError, FileExistsError, ValueError, RuntimeError, OSError) as exc:
        print(f"Erreur : {exc}")
        return 1
