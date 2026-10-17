"""Fail-closed composition for the website-first checkout routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import urlparse

from services.prodamus import ProdamusConfig, ProdamusService
from services.telegram_oidc import TelegramOidcClient, TelegramOidcConfig
from services.website_purchase import WebsitePurchaseService, ControlledCheckoutGate, PublicCheckoutGate
from services.private_offer import PrivateOffer
from storage.runtime_config import SettingsReader


class WebsitePurchaseRuntimeConfigurationError(RuntimeError):
    """Public checkout never starts from partial production configuration."""


@dataclass(frozen=True)
class WebsitePurchaseRuntime:
    service: WebsitePurchaseService
    base_url: str
    telegram_bot_url: str
    offer: PrivateOffer | None = None


def website_purchase_enabled(settings: SettingsReader) -> bool:
    value = settings.optional("WEBSITE_PURCHASE_ENABLED")
    if value is None or value == "false":
        return False
    if value == "true":
        return True
    raise WebsitePurchaseRuntimeConfigurationError("Website purchase configuration is invalid.")


def build_website_purchase_runtime(settings: SettingsReader) -> WebsitePurchaseRuntime:
    """Build only after every OIDC and Prodamus server-side value is present."""
    try:
        if (settings.optional("WEBSITE_PAYMENT_PROVIDER") or "prodamus") != "prodamus":
            raise ValueError("Website payment provider is unavailable.")
        if settings.optional("APP_ENV") == "production" and settings.optional("PRODAMUS_NPD_ENABLED") != "true":
            # Local attestation after merchant-side NPD activation, not a guessed
            # Prodamus API parameter. Never claim delivery from a payment alone.
            raise ValueError("Prodamus NPD activation must be confirmed.")
        base_url = _https_url(settings("WEBSITE_PURCHASE_BASE_URL"), origin_only=True)
        telegram_bot_url = _https_url(settings("TELEGRAM_BOT_URL"))
        offer = PrivateOffer.from_settings(settings)
        checkout_gate = _checkout_gate(settings)
        callback_url = f"{base_url}/auth/telegram/callback"
        config = TelegramOidcConfig(
            client_id=settings("TELEGRAM_OIDC_CLIENT_ID"),
            client_secret=settings("TELEGRAM_OIDC_CLIENT_SECRET"),
            redirect_uri=callback_url,
            proxy_url=settings.optional("TELEGRAM_OIDC_PROXY_URL"),
        )
        prodamus = ProdamusService(ProdamusConfig(
            payform_url=_https_url(settings("PRODAMUS_PAYFORM_URL")),
            secret_key=settings("PRODAMUS_SECRET_KEY"),
            notification_url=f"{base_url}/webhooks/prodamus",
            success_url=f"{base_url}/payment/success",
            fail_url=f"{base_url}/payment/fail",
            return_url=f"{base_url}/buy",
            system_code=settings.optional("PRODAMUS_SYSTEM_CODE") or "",
        ))
    except Exception as error:
        raise WebsitePurchaseRuntimeConfigurationError(
            "Website purchase configuration is incomplete."
        ) from None
    return WebsitePurchaseRuntime(
        WebsitePurchaseService(TelegramOidcClient(config), prodamus, checkout_gate=checkout_gate),
        base_url,
        telegram_bot_url,
        offer,
    )


def _checkout_gate(settings: SettingsReader) -> ControlledCheckoutGate | PublicCheckoutGate:
    mode = settings.optional("WEBSITE_CHECKOUT_MODE") or "off"
    if mode == "off":
        return ControlledCheckoutGate()
    if mode == "public":
        return PublicCheckoutGate()
    if mode != "controlled":
        raise ValueError("Checkout mode is invalid.")
    identity = settings("WEBSITE_CHECKOUT_TELEGRAM_USER_ID")
    expiry = settings("WEBSITE_CHECKOUT_EXPIRES_AT")
    if not identity.isascii() or not identity.isdecimal() or len(identity) > 19 or identity.startswith("0"):
        raise ValueError("Checkout identity is invalid.")
    deadline = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    if deadline.tzinfo is None or deadline.utcoffset().total_seconds() != 0:
        raise ValueError("Checkout deadline must be UTC.")
    return ControlledCheckoutGate(int(identity), deadline)


def _https_url(value: str, *, origin_only: bool = False) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or (origin_only and parsed.path not in {"", "/"})
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError("Public URL is invalid.")
    return value.rstrip("/")
