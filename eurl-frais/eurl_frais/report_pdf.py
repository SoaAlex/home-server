"""One-page French report: what to enter in Tiime this month, with sources."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .domain import format_eur, month_name_fr
from .storage import write_atomic
from .summary import MonthlySummary

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#4b5563")
LINE = colors.HexColor("#d1d5db")
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11.5, textColor=MUTED)
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")
RIGHT = ParagraphStyle("right", parent=BODY, alignment=2)
RIGHT_BOLD = ParagraphStyle("right_bold", parent=RIGHT, fontName="Helvetica-Bold")
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, leading=23, textColor=INK, spaceAfter=2)
HEADING = ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=INK,
                         spaceBefore=12, spaceAfter=5)


def report_filename(year: int, month: int) -> str:
    return f"RAPPORT_FRAIS_{year:04d}-{month:02d}.pdf"


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _status_box(summary: MonthlySummary) -> Table:
    background, ink = ("#dcfce7", "#166534") if summary.complete else ("#ffedd5", "#9a3412")
    style = ParagraphStyle("status", parent=BOLD, textColor=colors.HexColor(ink))
    box = Table([[_p(summary.status_text, style)]], colWidths=[160 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def _charges_table(summary: MonthlySummary) -> Table:
    rows = [[_p("Poste", BOLD), _p("Justificatif", BOLD), _p("TTC facture", RIGHT_BOLD), _p("Part EURL", RIGHT_BOLD)]]
    for line in summary.orange_lines:
        inv = line.invoice
        source = f"Facture {inv.invoice_id or 'sans numéro'} du {inv.issue_date:%d/%m/%Y}"
        if inv.period_start and inv.period_end:
            source += f", période du {inv.period_start:%d/%m/%Y} au {inv.period_end:%d/%m/%Y}"
        service = {"internet": "Orange Internet", "mobile": "Orange Mobile"}.get(inv.service, "Orange")
        rows.append([
            _p(service), _p(f"{source} — CHARGES/ORANGE/{line.path.name}", SMALL),
            _p(format_eur(inv.total_ttc), RIGHT), _p(f"{format_eur(line.share_amount)} ({summary.share_percent})", RIGHT),
        ])
    missing = summary.expected_invoices - len(summary.orange_lines)
    for _ in range(max(missing, 0)):
        rows.append([_p("Orange"), _p("FACTURE MANQUANTE — non comptée", BOLD), _p("—", RIGHT), _p("—", RIGHT)])
    for label, amount in summary.fixed_charges:
        rows.append([_p(label), _p("Prorata mensuel de l'échéancier annuel (pas de facture mensuelle)", SMALL),
                     _p("—", RIGHT), _p(format_eur(amount), RIGHT)])
    rows.append([_p(f"Total « {summary.charges_note_title} »", BOLD), "", "",
                 _p(format_eur(summary.charges_total), RIGHT_BOLD)])
    table = Table(rows, colWidths=[34 * mm, 78 * mm, 23 * mm, 25 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("LINEABOVE", (0, -1), (-1, -1), 1, INK),
        ("SPAN", (0, -1), (2, -1)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def generate_report_pdf(
    summary: MonthlySummary,
    output_path: str | Path,
    *,
    generated_on: date,
    receipt_name: str | None,
    payment_date: date | None,
) -> Path:
    pay_year, pay_month = summary.reimbursement_month
    start, end = summary.period
    title = f"Frais EURL — {summary.month_label} {summary.year}"
    elements = [
        _p(title, TITLE),
        _p(f"Notes de frais à saisir dans Tiime en {month_name_fr(pay_month)} {pay_year} · "
           f"rapport généré le {generated_on:%d/%m/%Y}", SMALL),
        Spacer(1, 8),
        _status_box(summary),
        _p(f"1. Note de frais « {summary.charges_note_title} »", HEADING),
        _charges_table(summary),
        Spacer(1, 5),
        _p(f"Saisie Tiime : montant HT = TTC = {format_eur(summary.charges_total)} ; TVA = 0,00 € "
           "(TVA non déduite).", BOLD),
        _p(f"2. Note de frais « {summary.rent_note_title} »", HEADING),
        _p(f"Loyer de sous-location du {start:%d/%m/%Y} au {end:%d/%m/%Y} : {format_eur(summary.rent_amount)}."),
        _p(f"Saisie Tiime : montant HT = TTC = {format_eur(summary.rent_amount)} ; TVA = 0,00 € (net de taxe).", BOLD),
    ]
    if receipt_name:
        paid = f", paiement déclaré le {payment_date:%d/%m/%Y}" if payment_date else ""
        elements.append(_p(f"Justificatif : LOYER/{receipt_name}{paid}.", SMALL))
    else:
        elements.append(_p("Quittance non générée lors de ce passage (voir points d'attention).", SMALL))
    elements += [
        _p("3. Total remboursé par l'EURL", HEADING),
        _p(f"{summary.charges_note_title} {format_eur(summary.charges_total)} + {summary.rent_note_title} "
           f"{format_eur(summary.rent_amount)} = {format_eur(summary.grand_total)}", BOLD),
    ]
    if summary.problems:
        elements.append(_p("Points d'attention", HEADING))
        elements += [_p(f"• {problem}") for problem in summary.problems]
    elements += [Spacer(1, 14), _p("Indemnités kilométriques : hors périmètre de ce rapport.", SMALL)]

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm, topMargin=20 * mm,
                      bottomMargin=20 * mm, title=title, author="", creator="eurl-frais").build(elements)
    output = Path(output_path)
    write_atomic(output, buffer.getvalue())
    return output
