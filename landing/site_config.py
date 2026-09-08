"""Safe, explicit public configuration required before landing publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from services.receipt_email import normalize_receipt_email


class LandingConfigurationError(RuntimeError):
    """Public landing configuration is incomplete without echoing its values."""


@dataclass(frozen=True)
class LandingPublicConfig:
    """Public facts injected only by a future approved hosting runtime."""

    public_site_url: str | None = None
    telegram_bot_url: str | None = None
    telegram_channel_url: str | None = None
    terms_url: str | None = None
    privacy_url: str | None = None
    refund_url: str | None = None
    seller_full_name: str | None = None
    seller_inn: str | None = None
    support_email: str | None = None

    @property
    def production_ready(self) -> bool:
        return all(
            (
                self.public_site_url,
                self.telegram_bot_url,
                self.telegram_channel_url,
                self.terms_url,
                self.privacy_url,
                self.refund_url,
                self.seller_full_name,
                self.seller_inn,
                self.support_email,
            )
        )


def build_landing_public_config(
    read_optional: Callable[[str], str | None],
    *,
    production: bool,
) -> LandingPublicConfig:
    """Build public-only values; production rejects every missing disclosure."""
    values = {
        name: _read_value(read_optional, name, production)
        for name in (
            "PUBLIC_SITE_URL",
            "TELEGRAM_BOT_URL",
            "TELEGRAM_CHANNEL_URL",
            "TERMS_URL",
            "PRIVACY_URL",
            "REFUND_URL",
            "SELLER_FULL_NAME",
            "SELLER_INN",
            "SUPPORT_EMAIL",
        )
    }
    public_site_url = _validate_https_url(
        values["PUBLIC_SITE_URL"],
        "PUBLIC_SITE_URL",
        production,
    )
    telegram_bot_url = _validate_telegram_url(
        values["TELEGRAM_BOT_URL"],
        "TELEGRAM_BOT_URL",
        production,
    )
    telegram_channel_url = _validate_telegram_url(
        values["TELEGRAM_CHANNEL_URL"],
        "TELEGRAM_CHANNEL_URL",
        production,
    )
    terms_url = _validate_https_url(values["TERMS_URL"], "TERMS_URL", production)
    privacy_url = _validate_https_url(
        values["PRIVACY_URL"], "PRIVACY_URL", production
    )
    refund_url = _validate_https_url(
        values["REFUND_URL"], "REFUND_URL", production
    )
    seller_full_name = _validate_seller_name(
        values["SELLER_FULL_NAME"],
        production,
    )
    seller_inn = _validate_inn(values["SELLER_INN"], production)
    support_email = _validate_support_email(values["SUPPORT_EMAIL"], production)
    return LandingPublicConfig(
        public_site_url=public_site_url,
        telegram_bot_url=telegram_bot_url,
        telegram_channel_url=telegram_channel_url,
        terms_url=terms_url,
        privacy_url=privacy_url,
        refund_url=refund_url,
        seller_full_name=seller_full_name,
        seller_inn=seller_inn,
        support_email=support_email,
    )


def _read_value(
    read_optional: Callable[[str], str | None],
    name: str,
    production: bool,
) -> str | None:
    try:
        value = read_optional(name)
    except Exception:
        value = None
    if not isinstance(value, str) or not value.strip():
        if production:
            raise LandingConfigurationError(
                f"Landing configuration is missing: {name}."
            )
        return None
    return value.strip()


def _validate_https_url(
    value: str | None,
    name: str,
    production: bool,
) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise LandingConfigurationError(
            f"Landing configuration is invalid: {name}."
        )
    return value


def _validate_telegram_url(
    value: str | None,
    name: str,
    production: bool,
) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me"}
        or not parsed.path.strip("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise LandingConfigurationError(
            f"Landing configuration is invalid: {name}."
        )
    return value


def _validate_seller_name(value: str | None, production: bool) -> str | None:
    if value is None:
        return None
    if len(value) < 5 or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise LandingConfigurationError(
            "Landing configuration is invalid: SELLER_FULL_NAME."
        )
    return value


def _validate_inn(value: str | None, production: bool) -> str | None:
    if value is None:
        return None
    if not value.isdecimal() or len(value) not in {10, 12}:
        raise LandingConfigurationError("Landing configuration is invalid: SELLER_INN.")
    return value


def _validate_support_email(value: str | None, production: bool) -> str | None:
    if value is None:
        return None
    try:
        return normalize_receipt_email(value)
    except ValueError:
        raise LandingConfigurationError(
            "Landing configuration is invalid: SUPPORT_EMAIL."
        ) from None
