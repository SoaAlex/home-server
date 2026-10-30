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


def test_archive_name_carries_issue_date_service_and_amount(tmp_path):
    content = b"%PDF-1.4\nfirst"

    result = store_orange_pdf(invoice(), content, tmp_path)

    assert result.created is True
    assert result.path.name == "2026-09-05 - Facture Orange internet fibre 39,99€.pdf"
    assert result.path.read_bytes() == content


def test_collect_reads_legacy_canonical_names_without_parsing_the_pdf(tmp_path):
    (tmp_path / "2026-09-07_Orange_internet_1045,99EUR.pdf").write_bytes(b"%PDF-1.5 no text layer")
    (tmp_path / "2026-10-06_Orange_mobile_21,00EUR.pdf").write_bytes(b"%PDF-1.5 other month")

    found, problems = collect_month_invoices(tmp_path, 2026, 9)

    (invoice_found, path), = found
    assert (invoice_found.service, invoice_found.issue_date, invoice_found.total_ttc) == (
        "internet", date(2026, 9, 7), Decimal("1045.99"))
    assert path.name == "2026-09-07_Orange_internet_1045,99EUR.pdf" and problems == []


def test_identical_invoice_is_idempotent(tmp_path):
    first = store_orange_pdf(invoice(), b"%PDF-1.4\nsame", tmp_path)
    second = store_orange_pdf(invoice(), b"%PDF-1.4\nsame", tmp_path)

    assert second.created is False
    assert second.path == first.path
    assert len(list(tmp_path.iterdir())) == 1


def test_redownloaded_invoice_keeps_the_archived_file(tmp_path):
    # Orange regenerates some PDFs on every download (new bytes, same invoice): keep the first archived copy.
    first = store_orange_pdf(invoice(), b"%PDF-1.4\nfirst download", tmp_path)

    again = store_orange_pdf(invoice(), b"%PDF-1.4\nsecond download", tmp_path)

    assert again.created is False and again.path == first.path
    assert first.path.read_bytes() == b"%PDF-1.4\nfirst download"


def test_pdf_not_matching_the_parsed_invoice_is_a_conflict(tmp_path):
    with pytest.raises(InvoiceConflict):
        store_orange_pdf(invoice(sha256="0" * 64), b"%PDF-1.4\nanother file", tmp_path)


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
    assert "AAAA-MM-JJ - Facture Orange " in problems[0]  # tells how to rename an unreadable invoice


def test_collect_on_missing_directory_returns_nothing(tmp_path):
    assert collect_month_invoices(tmp_path / "absent", 2026, 9) == ([], [])


def test_collect_reads_new_names_without_parsing_the_pdf(tmp_path):
    (tmp_path / "2026-09-07 - Facture Orange mobile 26,78€.pdf").write_bytes(b"%PDF-1.5 no text layer")
    (tmp_path / "2026-09-08 - Facture Orange 12,00€.pdf").write_bytes(b"%PDF-1.5 generic")

    found, problems = collect_month_invoices(tmp_path, 2026, 9)

    assert [(i.service, i.issue_date, i.total_ttc) for i, _ in found] == [
        ("mobile", date(2026, 9, 7), Decimal("26.78")), ("orange", date(2026, 9, 8), Decimal("12.00"))]
    assert problems == []


def test_invoice_archived_under_the_legacy_name_is_not_duplicated(tmp_path):
    legacy = tmp_path / "2026-09-05_Orange_internet_39,99EUR.pdf"
    legacy.write_bytes(b"%PDF-1.4\nold archive")

    again = store_orange_pdf(invoice(), b"%PDF-1.4\nnew download", tmp_path)

    assert again.created is False and again.path == legacy
    assert [p.name for p in tmp_path.iterdir()] == [legacy.name]
