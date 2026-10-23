from datetime import date
from decimal import Decimal

import pytest

from eurl_frais.invoice_parser import OrangeInvoice
from eurl_frais.storage import InvoiceConflict, collect_month_invoices, store_orange_pdf


def invoice(invoice_id="OR-2026-09/01", sha256=""):
    return OrangeInvoice(
        service="internet",
        invoice_id=invoice_id,
        issue_date=date(2026, 9, 5),
        period_start=None,
        period_end=None,
        total_ttc=Decimal("39.99"),
        source_filename="source.pdf",
        sha256=sha256,
    )


def test_first_import_uses_deterministic_safe_filename(tmp_path):
    content = b"%PDF-1.4\nfirst"

    result = store_orange_pdf(invoice(), content, tmp_path)

    assert result.created is True
    assert result.path.name == "2026-09_Orange_internet_OR-2026-09-01.pdf"
    assert result.path.read_bytes() == content


def test_invoice_without_reference_is_named_with_its_hash(tmp_path):
    result = store_orange_pdf(invoice(invoice_id=None), b"%PDF-1.4\nno-ref", tmp_path)

    assert result.path.name.startswith("2026-09_Orange_internet_")
    assert len(result.path.stem.rsplit("_", 1)[1]) == 10


def test_identical_invoice_is_idempotent(tmp_path):
    first = store_orange_pdf(invoice(), b"%PDF-1.4\nsame", tmp_path)
    second = store_orange_pdf(invoice(), b"%PDF-1.4\nsame", tmp_path)

    assert second.created is False
    assert second.path == first.path
    assert len(list(tmp_path.iterdir())) == 1


def test_same_reference_with_different_content_is_a_conflict(tmp_path):
    store_orange_pdf(invoice(), b"%PDF-1.4\nfirst", tmp_path)

    with pytest.raises(InvoiceConflict):
        store_orange_pdf(invoice(), b"%PDF-1.4\ndifferent", tmp_path)


def test_non_pdf_content_is_refused(tmp_path):
    with pytest.raises(ValueError):
        store_orange_pdf(invoice(), b"<html>login</html>", tmp_path)


def test_failed_atomic_replace_leaves_no_final_or_temp_file(tmp_path, monkeypatch):
    def fail_replace(_source, _destination):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("eurl_frais.storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated crash"):
        store_orange_pdf(invoice(), b"%PDF-1.4\npartial", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_collect_keeps_only_invoices_issued_in_month_and_dedupes(tmp_path, invoice_pdf):
    internet = invoice_pdf(tmp_path / "2026-09_Orange_internet_A.pdf", ref="OR-A-0001")
    invoice_pdf(tmp_path / "2026-09_Orange_mobile_B.pdf", ref="OR-B-0002", total="15,99", service="forfait mobile")
    (tmp_path / "copie manuelle.pdf").write_bytes(internet.read_bytes())
    invoice_pdf(tmp_path / "2026-08_Orange_internet_C.pdf", issued="05/08/2026", ref="OR-C-0003")
    (tmp_path / "illisible.pdf").write_bytes(b"%PDF-1.4 broken")

    found, problems = collect_month_invoices(tmp_path, 2026, 9)

    assert sorted(inv.invoice_id for inv, _path in found) == ["OR-A-0001", "OR-B-0002"]
    assert [path.name for _inv, path in found if path.name.startswith("copie")] == []
    assert len(problems) == 1 and "illisible.pdf" in problems[0]


def test_collect_on_missing_directory_returns_nothing(tmp_path):
    assert collect_month_invoices(tmp_path / "absent", 2026, 9) == ([], [])
