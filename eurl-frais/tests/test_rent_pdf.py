from datetime import date
from decimal import Decimal

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.shared import Pt
from PIL import Image, ImageDraw
from pypdf import PdfReader

from eurl_frais.rent_pdf import (
    RentTemplateError,
    default_payment_date,
    generate_rent_receipt,
    rent_receipt_filename,
    template_rent_amount,
)

HR_RUN = (
    '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
    '<w:pict><v:rect style="width:453.6pt;height:.05pt" o:hralign="center" o:hrstd="t" '
    'o:hr="t" fillcolor="#a0a0a0" stroked="f"/></w:pict></w:r>'
)


def runs(paragraph, *parts):
    for text, bold, italic in parts:
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic


def make_template(tmp_path, *, owner="M. TEST PROPRIETAIRE", signature=True):
    """Synthetic copy of the real template structure (dates split over runs, VML rules)."""
    doc = Document()
    title = doc.add_paragraph().add_run("QUITTANCE DE LOYER")
    title.bold, title.font.size = True, Pt(22)
    runs(doc.add_paragraph(), ("Période du :", True, False), (" [01/", False, False), ("0", False, False),
         ("8", False, False), ("/2026] ", False, False), ("au :", True, False), (" [3", False, False),
         ("1/08/2026]", False, False))
    runs(doc.add_paragraph(), ("Propriétaire (Sous-Bailleur) :", True, False))
    if owner:
        doc.add_paragraph(owner)
    runs(doc.add_paragraph(), ("Locataire (Sous-Locataire) :", True, False))
    doc.add_paragraph("Entreprise TEST EURL, 1 rue Test, 59000 Lille.")
    doc.add_paragraph()._p.append(parse_xml(HR_RUN))
    runs(doc.add_paragraph(), ("DÉTAIL DU RÈGLEMENT", True, False))
    doc.add_paragraph("Le Propriétaire reconnaît avoir reçu, au titre du contrat de sous-location "
                      "professionnelle du 09/02/2026, la somme de :")
    runs(doc.add_paragraph(), ("MONTANT TOTAL : 200,00 €", True, False), (" ", False, False),
         ("(Deux cents euros net de taxe)", False, True))
    runs(doc.add_paragraph(), ("Date du paiement :", True, False), (" 0", False, False), ("7/09/2026", False, False),
         (" ", False, False), ("Mode de règlement :", True, False), (" Virement bancaire", False, False))
    doc.add_paragraph()._p.append(parse_xml(HR_RUN))
    doc.add_paragraph("Cette quittance annule tout reçu à valoir précédemment délivré.")
    doc.add_paragraph("Fait à TESTVILLE, le 07/09/2026.")
    runs(doc.add_paragraph(), ("(Signature du Sous-Bailleur)", False, True))
    if signature:
        image_path = tmp_path / "signature.png"
        image = Image.new("RGB", (300, 200), "white")
        ImageDraw.Draw(image).line((10, 150, 80, 40, 140, 160, 290, 30), fill="black", width=4)
        image.save(image_path)
        doc.add_picture(str(image_path), width=Pt(126))
    path = tmp_path / "modele.docx"
    doc.save(path)
    return path


def pdf_text(path):
    reader = PdfReader(path)
    return reader, " ".join(reader.pages[0].extract_text().split())


def test_copies_the_template_with_new_period_payment_and_issue_dates(tmp_path):
    output = tmp_path / "LOYER" / "receipt.pdf"

    generate_rent_receipt(make_template(tmp_path), output, 2026, 9, issued_on=date(2026, 10, 23))

    reader, text = pdf_text(output)
    assert len(reader.pages) == 1
    assert "Période du : [01/09/2026] au : [30/09/2026]" in text
    assert "Date du paiement : 06/10/2026" in text
    assert "Fait à TESTVILLE, le 23/10/2026." in text
    assert "sous-location professionnelle du 09/02/2026" in text
    assert "MONTANT TOTAL : 200,00 €" in text and "net de taxe" in text
    assert "M. TEST PROPRIETAIRE" in text
    assert "07/09/2026" not in text and "31/08/2026" not in text
    assert len(reader.pages[0].images) == 1


def test_explicit_payment_date_is_used(tmp_path):
    output = tmp_path / "receipt.pdf"

    generate_rent_receipt(make_template(tmp_path), output, 2026, 2,
                          issued_on=date(2026, 3, 6), payment_date=date(2026, 3, 6))

    _reader, text = pdf_text(output)
    assert "[01/02/2026] au : [28/02/2026]" in text
    assert "Date du paiement : 06/03/2026" in text and "le 06/03/2026." in text


def test_filename_uses_unaccented_french_month_and_year():
    assert rent_receipt_filename(2026, 9) == "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf"
    assert rent_receipt_filename(2027, 2) == "QUITTANCE DE LOYER FEVRIER 2027.pdf"


def test_default_payment_date_is_the_configured_day_of_next_month():
    assert default_payment_date(2026, 9, 6) == date(2026, 10, 6)
    assert default_payment_date(2026, 12, 6) == date(2027, 1, 6)


def test_template_rent_amount_reads_the_net_amount(tmp_path):
    assert template_rent_amount(make_template(tmp_path)) == Decimal("200.00")


def test_issue_date_before_payment_date_is_refused(tmp_path):
    with pytest.raises(ValueError, match="émission"):
        generate_rent_receipt(make_template(tmp_path), tmp_path / "x.pdf", 2026, 9, issued_on=date(2026, 10, 5))


def test_payment_outside_the_following_month_is_refused(tmp_path):
    with pytest.raises(ValueError, match="paiement"):
        generate_rent_receipt(make_template(tmp_path), tmp_path / "x.pdf", 2026, 9,
                              issued_on=date(2026, 11, 20), payment_date=date(2026, 11, 6))


def test_existing_output_is_never_overwritten(tmp_path):
    output = tmp_path / "receipt.pdf"
    output.write_bytes(b"%PDF-original")

    with pytest.raises(FileExistsError):
        generate_rent_receipt(make_template(tmp_path), output, 2026, 9, issued_on=date(2026, 10, 23))
    assert output.read_bytes() == b"%PDF-original"


def test_template_without_owner_is_refused(tmp_path):
    with pytest.raises(RentTemplateError, match="propriétaire"):
        generate_rent_receipt(make_template(tmp_path, owner=None), tmp_path / "x.pdf", 2026, 9,
                              issued_on=date(2026, 10, 23))


def test_template_without_signature_is_refused(tmp_path):
    with pytest.raises(RentTemplateError, match="signature"):
        generate_rent_receipt(make_template(tmp_path, signature=False), tmp_path / "x.pdf", 2026, 9,
                              issued_on=date(2026, 10, 23))
