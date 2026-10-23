"""Rent receipt (quittance) rendered as a faithful copy of the owner's Word template.

The template stays the single source of truth for names, addresses, contract date,
amount and signature: only the rent period, payment date and issue date change.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer

from .domain import ascii_upper, money, month_name_fr, month_period, next_month
from .storage import write_atomic

DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
VML_OFFICE = "urn:schemas-microsoft-com:office:office"
EMU_PER_POINT = 12700
FONT_SCALE = 11 / 12  # Word's Aptos 12 pt has about the width of Helvetica 11 pt
MARGIN = 1417 / 20  # template margins: 1417 twips = 2.5 cm
SPACE_AFTER = 8  # template paragraph spacing: 160 twips


class RentTemplateError(ValueError):
    """The Word template lacks something a valid receipt needs."""


@dataclass(frozen=True)
class Segment:
    text: str
    bold: bool
    italic: bool
    size: float


@dataclass(frozen=True)
class Block:
    kind: str  # text | rule | image | blank
    segments: tuple[Segment, ...] = ()
    image: bytes = b""
    image_size: tuple[float, float] | None = None

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)


def rent_receipt_filename(year: int, month: int) -> str:
    return f"QUITTANCE DE LOYER {ascii_upper(month_name_fr(month))} {year:04d}.pdf"


def default_payment_date(year: int, month: int, payment_day: int) -> date:
    pay_year, pay_month = next_month(year, month)
    return date(pay_year, pay_month, payment_day)


def _read_blocks(template_path: str | Path) -> list[Block]:
    try:
        document = Document(str(template_path))
    except Exception as exc:  # python-docx raises zipfile/lxml/KeyError variants
        raise RentTemplateError(f"Modèle Word illisible: {template_path}") from exc
    default_sizes = document.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:sz/@w:val")
    default_size = int(default_sizes[0]) / 2 if default_sizes else 11.0
    blocks: list[Block] = []
    for paragraph in document.paragraphs:
        element = paragraph._p
        embeds = element.xpath(".//a:blip/@r:embed")
        if embeds:
            extent = element.xpath(".//wp:extent")
            size = None
            if extent:
                size = (int(extent[0].get("cx")) / EMU_PER_POINT, int(extent[0].get("cy")) / EMU_PER_POINT)
            blocks.append(Block("image", image=document.part.related_parts[embeds[0]].blob, image_size=size))
            continue
        if any(rect.get(f"{{{VML_OFFICE}}}hr") == "t" for rect in element.xpath('.//*[local-name()="rect"]')):
            blocks.append(Block("rule"))
            continue
        style_font = paragraph.style.font if paragraph.style is not None else None
        segments: list[Segment] = []
        for run in paragraph.runs:
            if not run.text:
                continue
            bold = run.bold if run.bold is not None else bool(style_font and style_font.bold)
            italic = run.italic if run.italic is not None else bool(style_font and style_font.italic)
            if run.font.size is not None:
                size = run.font.size.pt
            elif style_font is not None and style_font.size is not None:
                size = style_font.size.pt
            else:
                size = default_size
            if segments and (segments[-1].bold, segments[-1].italic, segments[-1].size) == (bold, italic, size):
                segments[-1] = Segment(segments[-1].text + run.text, bold, italic, size)
            else:
                segments.append(Segment(run.text, bold, italic, size))
        blocks.append(Block("text", tuple(segments)) if segments else Block("blank"))
    return blocks


def _find(blocks: list[Block], prefix: str) -> int:
    for index, block in enumerate(blocks):
        if block.kind == "text" and block.text.strip().casefold().startswith(prefix.casefold()):
            return index
    raise RentTemplateError(f"Paragraphe « {prefix} » introuvable dans le modèle")


def _validate(blocks: list[Block]) -> None:
    _find(blocks, "QUITTANCE DE LOYER")
    labels = ("Propriétaire (Sous-Bailleur)", "Locataire (Sous-Locataire)", "DÉTAIL DU RÈGLEMENT")
    texts = [block.text.strip() for block in blocks if block.kind == "text"]
    for label in labels[:2]:
        position = next((i for i, text in enumerate(texts) if text.casefold().startswith(label.casefold())), None)
        following = texts[position + 1] if position is not None and position + 1 < len(texts) else ""
        if position is None or not following or any(following.casefold().startswith(x.casefold()) for x in labels):
            raise RentTemplateError(f"Nom du propriétaire ou du locataire absent du modèle après « {label} »")
    images = [block for block in blocks if block.kind == "image"]
    if len(images) != 1:
        raise RentTemplateError("Le modèle doit contenir exactement une image : la signature approuvée")


def template_rent_amount(template_path: str | Path) -> Decimal:
    blocks = _read_blocks(template_path)
    line = blocks[_find(blocks, "MONTANT TOTAL")].text
    match = re.search(r"MONTANT TOTAL\s*:\s*([\d\s\u00a0\u202f.,]+?)\s*€", line, re.I)
    if not match or "net de taxe" not in line.casefold():
        raise RentTemplateError("Le modèle doit indiquer « MONTANT TOTAL : <montant> € (... net de taxe) »")
    amount = money(match.group(1))
    if amount <= 0:
        raise RentTemplateError("Montant du loyer invalide dans le modèle")
    return amount


def _replace_dates(block: Block, new_dates: list[date]) -> Block:
    remaining = list(new_dates)

    def substitute(match: re.Match[str]) -> str:
        if not remaining:
            raise RentTemplateError(f"Date inattendue dans « {block.text.strip()} »")
        return remaining.pop(0).strftime("%d/%m/%Y")

    segments = tuple(
        Segment(DATE_RE.sub(substitute, s.text), s.bold, s.italic, s.size) for s in block.segments
    )
    if remaining:
        raise RentTemplateError(f"Date(s) introuvable(s) dans « {block.text.strip()} »")
    return Block("text", segments)


def _flowables(blocks: list[Block]) -> list:
    flowables = []
    for block in blocks:
        if block.kind == "rule":
            # Word draws the rule inside an otherwise empty paragraph: keep that air around it.
            flowables.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#a0a0a0"),
                                        spaceBefore=20, spaceAfter=14))
        elif block.kind == "blank":
            flowables.append(Spacer(1, 15))
        elif block.kind == "image":
            width, height = block.image_size or (126, 84)
            flowables.append(Image(io.BytesIO(block.image), width=width, height=height, hAlign="LEFT"))
        else:
            base = max(segment.size for segment in block.segments) * FONT_SCALE
            markup = []
            for segment in block.segments:
                text = escape(segment.text)
                if segment.size * FONT_SCALE != base:
                    text = f'<font size="{segment.size * FONT_SCALE:.1f}">{text}</font>'
                if segment.bold:
                    text = f"<b>{text}</b>"
                if segment.italic:
                    text = f"<i>{text}</i>"
                markup.append(text)
            style = ParagraphStyle(
                "template", fontName="Helvetica", fontSize=base,
                leading=base * (1.45 if base >= 16 else 1.38), spaceAfter=SPACE_AFTER,
            )
            flowables.append(Paragraph("".join(markup), style))
    return flowables


def generate_rent_receipt(
    template_path: str | Path,
    output_path: str | Path,
    year: int,
    month: int,
    *,
    issued_on: date,
    payment_date: date | None = None,
    payment_day: int = 6,
) -> Path:
    period_start, period_end = month_period(year, month)
    payment = payment_date or default_payment_date(year, month, payment_day)
    if (payment.year, payment.month) != next_month(year, month):
        raise ValueError("La date de paiement doit tomber le mois suivant la période louée (loyer payé en M pour M-1)")
    if issued_on < payment:
        raise ValueError("La date d'émission ne peut pas précéder la date de paiement")
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"{output.name} existe déjà : jamais écrasé")

    blocks = _read_blocks(template_path)
    _validate(blocks)
    template_rent_amount(template_path)
    replacements = {
        "Période du": [period_start, period_end],
        "Date du paiement": [payment],
        "Fait à": [issued_on],
    }
    for prefix, dates in replacements.items():
        index = _find(blocks, prefix)
        blocks[index] = _replace_dates(blocks[index], dates)

    buffer = io.BytesIO()
    title = f"Quittance de loyer {month_name_fr(month)} {year}"
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=MARGIN, title=title, author="", subject=title, creator="eurl-frais",
    ).build(_flowables(blocks))
    content = buffer.getvalue()

    page = PdfReader(io.BytesIO(content))
    if len(page.pages) != 1:
        raise RentTemplateError("La quittance doit tenir sur une seule page")
    text = " ".join((page.pages[0].extract_text() or "").split())
    for expected in (period_start, period_end, payment, issued_on):
        if expected.strftime("%d/%m/%Y") not in text:
            raise RentTemplateError(f"Date {expected:%d/%m/%Y} absente du PDF généré")
    if len(page.pages[0].images) != 1:
        raise RentTemplateError("Signature absente du PDF généré")
    if output.exists():
        raise FileExistsError(f"{output.name} existe déjà : jamais écrasé")
    write_atomic(output, content)
    return output
