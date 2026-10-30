"""Settings from the environment (.env). Personal amounts and secrets live only there."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """A setting is missing or invalid (the message names it, never its value)."""


def _decimal(env: Mapping[str, str], key: str) -> Decimal | None:
    raw = env.get(key, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        raise ConfigError(f"{key} doit être un nombre") from None
    if not value.is_finite() or value < 0:
        raise ConfigError(f"{key} doit être un nombre positif")
    return value


def _integer(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = env.get(key, "").strip() or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} doit être un entier") from None
    if not low <= value <= high:
        raise ConfigError(f"{key} doit être compris entre {low} et {high}")
    return value


# Current names first (CLOUD_NAS reorganised on 2026-10-30), then the legacy ones still honoured if present.
TEMPLATE_NAMES = ("Modèle quittance loyer bureau EURL.docx", "QUITTANCE DE LOYER.docx")
ORANGE_DIR_NAMES = ("Orange", "ORANGE")


def _first_existing(root: Path, names: tuple[str, ...]) -> Path:
    return next((root / name for name in names if (root / name).exists()), root / names[0])


@dataclass(frozen=True)
class Config:
    loyer_dir: Path
    charges_dir: Path
    state_dir: Path
    orange_expected_invoices: int
    orange_share: Decimal | None
    gas_monthly: Decimal | None
    electricity_monthly: Decimal | None
    rent_payment_day: int
    orange_username: str = field(default="", repr=False)
    orange_password: str = field(default="", repr=False)
    discord_webhook_url: str = field(default="", repr=False)

    @property
    def template_path(self) -> Path:
        return _first_existing(self.loyer_dir, TEMPLATE_NAMES)

    @property
    def orange_dir(self) -> Path:
        return _first_existing(self.charges_dir, ORANGE_DIR_NAMES)

    @property
    def has_orange_credentials(self) -> bool:
        return bool(self.orange_username and self.orange_password)

    @property
    def fixed_charges(self) -> tuple[tuple[str, Decimal], ...]:
        return (("Gaz", self.gas_monthly or Decimal("0")), ("Électricité", self.electricity_monthly or Decimal("0")))

    def require_amounts(self) -> None:
        missing = [
            key for key, value in (
                ("ORANGE_SHARE", self.orange_share),
                ("GAS_MONTHLY_EUR", self.gas_monthly),
                ("ELECTRICITY_MONTHLY_EUR", self.electricity_monthly),
            ) if value is None
        ]
        if missing:
            raise ConfigError(f"Variables manquantes dans .env : {', '.join(missing)}")


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    share = _decimal(env, "ORANGE_SHARE")
    if share is not None and not Decimal("0") < share <= Decimal("1"):
        raise ConfigError("ORANGE_SHARE doit être une fraction entre 0 et 1 (ex. 0.80)")
    return Config(
        loyer_dir=Path(env.get("EURL_LOYER_DIR", "/eurl/LOYER")),
        charges_dir=Path(env.get("EURL_CHARGES_DIR", "/eurl/CHARGES")),
        state_dir=Path(env.get("EURL_STATE_DIR", "/state")),
        orange_expected_invoices=_integer(env, "ORANGE_EXPECTED_INVOICES", 2, 1, 10),
        orange_share=share,
        gas_monthly=_decimal(env, "GAS_MONTHLY_EUR"),
        electricity_monthly=_decimal(env, "ELECTRICITY_MONTHLY_EUR"),
        rent_payment_day=_integer(env, "RENT_PAYMENT_DAY", 6, 1, 28),
        orange_username=env.get("ORANGE_USERNAME", "").strip(),
        orange_password=env.get("ORANGE_PASSWORD", ""),
        discord_webhook_url=env.get("DISCORD_WEBHOOK_URL", "").strip(),
    )
