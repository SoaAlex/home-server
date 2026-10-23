import shutil
from datetime import date

import pytest
from pypdf import PdfReader

from conftest import write_pdf
from eurl_frais.cli import main
from eurl_frais.config import load_config
from eurl_frais.monthly import run_monthly
from eurl_frais.orange_portal import ManualAuthRequired
from test_rent_pdf import make_template

TODAY = date(2026, 10, 10)


@pytest.fixture
def env(tmp_path):
    loyer, charges, state = tmp_path / "LOYER", tmp_path / "CHARGES", tmp_path / "state"
    for directory in (loyer, charges, state):
        directory.mkdir()
    shutil.copy(make_template(tmp_path), loyer / "QUITTANCE DE LOYER.docx")
    return {
        "EURL_LOYER_DIR": str(loyer), "EURL_CHARGES_DIR": str(charges), "EURL_STATE_DIR": str(state),
        "ORANGE_USERNAME": "user", "ORANGE_PASSWORD": "secret", "ORANGE_SHARE": "0.80",
        "GAS_MONTHLY_EUR": "10.00", "ELECTRICITY_MONTHLY_EUR": "5.00",
        "DISCORD_WEBHOOK_URL": "https://discord.example/webhook",
    }


def orange_pdf(directory, name, *, issued="05/09/2026", total="49,99", ref="OR-TEST-0001", service="Livebox Fibre"):
    return write_pdf(directory / name, [f"Facture n° {ref}", f"Date d'émission : {issued}",
                                        f"Abonnement {service}", f"Total à payer : {total} €"])


def downloader_for(*invoices):
    calls = []

    def download(username, password, session_dir, download_dir, year, month, debug=False):
        calls.append((year, month))
        return [orange_pdf(download_dir, f"dl-{index}.pdf", **kwargs) for index, kwargs in enumerate(invoices)]

    download.calls = calls
    return download


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, webhook, content, files):
        self.calls.append((content, [f.name for f in files]))


TWO_INVOICES = (dict(ref="OR-NET-0001", total="49,99"), dict(ref="OR-MOB-0002", total="15,99", service="forfait mobile"))


def run(env, *, today=TODAY, downloader=None, sender=None, **kwargs):
    return run_monthly(load_config(env), year=2026, month=9, today=today,
                       downloader=downloader or downloader_for(*TWO_INVOICES), sender=sender or Sender(),
                       out=lambda *_: None, **kwargs)


def test_complete_month_archives_everything_and_notifies_once(env):
    sender = Sender()

    assert run(env, sender=sender) == 0

    orange = sorted(p.name for p in (load_config(env).orange_dir).iterdir())
    assert orange == ["2026-09_Orange_internet_OR-NET-0001.pdf", "2026-09_Orange_mobile_OR-MOB-0002.pdf"]
    receipt = load_config(env).loyer_dir / "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf"
    receipt_text = " ".join(PdfReader(receipt).pages[0].extract_text().split())
    assert "Date du paiement : 06/10/2026" in receipt_text and "le 10/10/2026." in receipt_text
    assert not (load_config(env).charges_dir / "RAPPORTS").exists()  # the Discord message is the report
    (content, files), = sender.calls
    assert files == ["QUITTANCE DE LOYER SEPTEMBRE 2026.pdf", "2026-09_Orange_internet_OR-NET-0001.pdf",
                     "2026-09_Orange_mobile_OR-MOB-0002.pdf"]
    assert "COMPLET" in content and "67,78" in content and "200,00" in content and "267,78" in content
    assert "OR-NET-0001" in content and "39,99" in content and "12,79" in content


def test_second_run_does_not_resend(env):
    run(env)
    sender, downloader = Sender(), downloader_for(*TWO_INVOICES)

    assert run(env, sender=sender, downloader=downloader) == 0
    assert sender.calls == [] and downloader.calls == []


def test_incomplete_month_warns_then_completes_with_a_manual_pdf(env):
    sender = Sender()

    assert run(env, sender=sender, downloader=downloader_for(TWO_INVOICES[0])) == 3
    assert "INCOMPLET" in sender.calls[0][0]
    assert "provisoire" in sender.calls[0][0]

    orange_pdf(load_config(env).orange_dir, "facture mobile.pdf", ref="OR-MOB-0002", total="15,99", service="forfait mobile")
    assert run(env, sender=sender, use_orange=False) == 0
    assert "COMPLET" in sender.calls[1][0] and "INCOMPLET" not in sender.calls[1][0]
    assert "provisoire" not in sender.calls[1][0]


def test_dry_run_writes_nothing_and_sends_nothing(env):
    sender, downloader = Sender(), downloader_for(*TWO_INVOICES)

    run(env, sender=sender, downloader=downloader, dry_run=True)

    config = load_config(env)
    assert sender.calls == [] and downloader.calls == []
    assert sorted(p.name for p in config.loyer_dir.iterdir()) == ["QUITTANCE DE LOYER.docx"]
    assert list(config.charges_dir.iterdir()) == []


def test_without_credentials_the_orange_folder_is_used(env):
    env = {**env, "ORANGE_PASSWORD": ""}
    config = load_config(env)
    config.orange_dir.mkdir()
    orange_pdf(config.orange_dir, "a.pdf", ref="OR-NET-0001")
    orange_pdf(config.orange_dir, "b.pdf", ref="OR-MOB-0002", total="15,99", service="forfait mobile")
    downloader = downloader_for(*TWO_INVOICES)

    assert run(env, downloader=downloader) == 0
    assert downloader.calls == []


def test_orange_blocking_is_reported_and_not_fatal(env):
    def blocked(*_args, **_kwargs):
        raise ManualAuthRequired("code SMS demandé")

    sender = Sender()

    assert run(env, sender=sender, downloader=blocked) == 3
    assert "code SMS" in sender.calls[0][0]
    assert load_config(env).orange_dir.is_dir()  # ready for a manual drop
    assert (load_config(env).loyer_dir / "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf").is_file()


def test_receipt_is_not_issued_before_the_payment_date(env):
    sender = Sender()

    assert run(env, sender=sender, today=date(2026, 10, 3)) == 3
    assert not (load_config(env).loyer_dir / "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf").exists()
    assert "06/10/2026" in sender.calls[0][0]


def test_existing_receipt_is_kept(env):
    receipt = load_config(env).loyer_dir / "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf"
    receipt.write_bytes(b"%PDF-manual")

    assert run(env) == 0
    assert receipt.read_bytes() == b"%PDF-manual"


def test_default_month_is_the_previous_month(env):
    downloader = downloader_for(*TWO_INVOICES)

    assert main(["monthly", "--no-notify"], env=env, today=date(2026, 10, 23), downloader=downloader) == 0
    assert downloader.calls == [(2026, 9)]


def test_monthly_crash_posts_an_alert_on_discord(env):
    (load_config(env).loyer_dir / "QUITTANCE DE LOYER.docx").unlink()
    sender = Sender()

    assert main(["monthly", "--month", "2026-09"], env=env, today=TODAY,
                downloader=downloader_for(*TWO_INVOICES), sender=sender) == 1
    (content, files), = sender.calls
    assert content.startswith("❌") and "2026-09" in content and files == []


def test_quittance_command_writes_a_corrected_copy_and_never_overwrites(env):
    args = ["quittance", "--month", "2026-02", "--payment-date", "2026-03-06", "--issued-on", "2026-03-06",
            "--output", "LOYER/QUITTANCE DE LOYER FEVRIER - CORRIGEE.pdf"]

    assert main(args, env=env, today=TODAY) == 0
    copy = load_config(env).loyer_dir / "QUITTANCE DE LOYER FEVRIER - CORRIGEE.pdf"
    text = " ".join(PdfReader(copy).pages[0].extract_text().split())
    assert "[01/02/2026] au : [28/02/2026]" in text and "le 06/03/2026." in text
    assert main(args, env=env, today=TODAY) == 1


def test_quittance_command_refuses_paths_outside_loyer_and_charges(env):
    args = ["quittance", "--month", "2026-02", "--payment-date", "2026-03-06", "--output", "../evil.pdf"]

    assert main(args, env=env, today=TODAY) == 2
