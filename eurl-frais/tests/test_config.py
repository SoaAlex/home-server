from decimal import Decimal
from pathlib import Path

import pytest

from eurl_frais.config import ConfigError, load_config

FULL = {
    "EURL_LOYER_DIR": "/eurl/LOYER",
    "EURL_CHARGES_DIR": "/eurl/CHARGES",
    "EURL_STATE_DIR": "/state",
    "ORANGE_USERNAME": "user@example.test",
    "ORANGE_PASSWORD": "pa55-secret",
    "ORANGE_SHARE": "0,80",
    "GAS_MONTHLY_EUR": "10.00",
    "ELECTRICITY_MONTHLY_EUR": "5",
    "DISCORD_WEBHOOK_URL": "https://discord.example/api/webhooks/1/hook-secret",
}


def test_loads_paths_amounts_and_defaults():
    config = load_config(FULL)

    assert config.template_path == Path("/eurl/LOYER/QUITTANCE DE LOYER.docx")
    assert config.orange_dir == Path("/eurl/CHARGES/ORANGE")
    assert config.reports_dir == Path("/eurl/CHARGES/RAPPORTS")
    assert config.orange_share == Decimal("0.80")
    assert config.fixed_charges == (("Gaz", Decimal("10.00")), ("Électricité", Decimal("5.00")))
    assert config.orange_expected_invoices == 2
    assert config.rent_payment_day == 6
    assert config.has_orange_credentials is True


def test_repr_never_contains_secrets():
    text = repr(load_config(FULL))

    assert "pa55-secret" not in text and "hook-secret" not in text and "user@example.test" not in text


def test_missing_amounts_are_named_without_values():
    config = load_config({k: v for k, v in FULL.items() if k not in {"ORANGE_SHARE", "GAS_MONTHLY_EUR"}})

    with pytest.raises(ConfigError, match="ORANGE_SHARE.*GAS_MONTHLY_EUR"):
        config.require_amounts()


@pytest.mark.parametrize("key,value", [("ORANGE_SHARE", "1.5"), ("RENT_PAYMENT_DAY", "29"),
                                       ("ORANGE_EXPECTED_INVOICES", "0"), ("GAS_MONTHLY_EUR", "abc")])
def test_invalid_values_are_refused(key, value):
    with pytest.raises(ConfigError, match=key):
        load_config({**FULL, key: value})


def test_missing_credentials_are_reported_as_absent():
    config = load_config({**FULL, "ORANGE_PASSWORD": ""})

    assert config.has_orange_credentials is False
