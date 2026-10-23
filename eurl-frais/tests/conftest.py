import pytest
from reportlab.pdfgen import canvas


def write_pdf(path, lines):
    pdf = canvas.Canvas(str(path))
    text = pdf.beginText(40, 760)
    text.setFont("Helvetica", 10)
    for line in lines:
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()
    return path


@pytest.fixture
def invoice_pdf():
    """Synthetic Orange-like invoice (no real data)."""

    def make(path, *, issued="05/09/2026", total="39,99", ref="OR-TEST-0001", service="Livebox Fibre"):
        lines = [f"Facture n° {ref}"] if ref else []
        lines += [f"Date d'émission : {issued}", f"Abonnement {service}", f"Total à payer : {total} €"]
        return write_pdf(path, lines)

    return make
