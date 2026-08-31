"""Explicit application composition for the paid-subscription flow.

No environment file is read while importing this module.  The production
factory is called by a validated runtime bootstrap and requires every
commercial value explicitly, so a price is never silently invented.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from urllib.parse import urlparse

from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from services.payment_service import PaymentService, SubscriptionProduct
from services.receipt_email import get_receipt_email, normalize_receipt_email
from services.yookassa_provider import YooKassaCredentials, YooKassaProvider


PRODUCT_CODE = LAUNCH_COMMERCIAL_PRODUCT.product_code
PERIOD_DAYS = LAUNCH_COMMERCIAL_PRODUCT.period_days


class PaymentRuntimeConfigurationError(RuntimeError):
    """Raised when required server-side payment configuration is missing."""


class PaymentMode(StrEnum):
    """Explicit application mode; credentials alone never select production."""

    TEST = "test"
    PRODUCTION = "production"

    @property
    def expects_test_shop(self) -> bool:
        return self is PaymentMode.TEST


@dataclass(frozen=True)
class CommercialPublicConfig:
    """Public, non-secret links exposed by commercial screens only."""

    public_site_url: str | None = None
    terms_url: str | None = None
    privacy_url: str | None = None
    refund_url: str | None = None
    support_email: str | None = None


@dataclass(frozen=True)
class PaymentRuntime:
    product: SubscriptionProduct
    payment_service: PaymentService
    mode: PaymentMode
    commercial_public: CommercialPublicConfig = CommercialPublicConfig()


_configured_runtime: PaymentRuntime | None = None


def build_payment_runtime(read_required: Callable[[str], str]) -> PaymentRuntime:
    """Build the live composition only from injected server-side settings."""
    mode = read_payment_mode(read_required)
    commercial_public = build_commercial_public_config(
        read_required,
        production=mode is PaymentMode.PRODUCTION,
    )
    return_url = _read_required(read_required, "YOOKASSA_RETURN_URL")
    parsed_return_url = urlparse(return_url)
    if parsed_return_url.scheme not in {"http", "https"} or not parsed_return_url.netloc:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        )
    try:
        product = LAUNCH_COMMERCIAL_PRODUCT.payment_product
        credentials = YooKassaCredentials(
            shop_id=_read_required(read_required, "YOOKASSA_SHOP_ID"),
            secret_key=_read_required(read_required, "YOOKASSA_SECRET_TOKEN"),
        )
        provider = YooKassaProvider(
            credentials,
            return_url,
            expected_test_mode=mode.expects_test_shop,
        )
        payment_service = PaymentService(
            provider,
            product,
            expected_test_mode=mode.expects_test_shop,
            receipt_email_required=lambda user_id: get_receipt_email(user_id) is not None,
        )
    except ValueError as error:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        ) from error
    return PaymentRuntime(product, payment_service, mode, commercial_public)


def build_commercial_public_config(
    read_required: Callable[[str], str],
    *,
    production: bool,
) -> CommercialPublicConfig:
    """Read only non-secret launch disclosures and fail closed in production."""
    url_fields = (
        ("PUBLIC_SITE_URL", "public_site_url"),
        ("TERMS_URL", "terms_url"),
        ("PRIVACY_URL", "privacy_url"),
        ("REFUND_URL", "refund_url"),
    )
    values: dict[str, str | None] = {}
    for setting_name, attribute_name in url_fields:
        raw_value = _read_commercial_value(read_required, setting_name, production)
        values[attribute_name] = (
            _validate_public_url(raw_value, setting_name, production)
            if raw_value is not None
            else None
        )
    raw_support_email = _read_commercial_value(
        read_required,
        "SUPPORT_EMAIL",
        production,
    )
    if raw_support_email is None:
        support_email = None
    else:
        try:
            support_email = normalize_receipt_email(raw_support_email)
        except ValueError:
            raise PaymentRuntimeConfigurationError(
                "Commercial configuration is invalid: SUPPORT_EMAIL."
            ) from None
    return CommercialPublicConfig(**values, support_email=support_email)


def _read_commercial_value(
    read_required: Callable[[str], str],
    name: str,
    production: bool,
) -> str | None:
    try:
        value = read_required(name)
    except Exception:
        if production:
            raise PaymentRuntimeConfigurationError(
                f"Commercial configuration is missing: {name}."
            ) from None
        return None
    if not isinstance(value, str) or not value.strip():
        if production:
            raise PaymentRuntimeConfigurationError(
                f"Commercial configuration is missing: {name}."
            )
        return None
    return value.strip()


def _validate_public_url(value: str, name: str, production: bool) -> str:
    parsed = urlparse(value)
    allowed_schemes = {"https"} if production else {"http", "https"}
    if (
        any(character.isspace() or ord(character) < 32 for character in value)
        or
        parsed.scheme not in allowed_schemes
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PaymentRuntimeConfigurationError(
            f"Commercial configuration is invalid: {name}."
        )
    return value


def _read_required(read_required: Callable[[str], str], name: str) -> str:
    try:
        value = read_required(name)
    except Exception:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is incomplete."
        ) from None
    if not isinstance(value, str) or not value.strip():
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is incomplete."
        )
    return value.strip()


def read_payment_mode(read_required: Callable[[str], str]) -> PaymentMode:
    """Read an explicit mode, retaining only safe legacy sandbox compatibility.

    Existing local sandbox configuration with ``PAYMENTS_TEST_MODE=true`` stays
    valid.  Production can only be selected by the explicit
    ``PAYMENTS_MODE=production`` value; credentials or a legacy false flag are
    never enough to enable it.
    """
    explicit_value = _read_optional(read_required, "PAYMENTS_MODE")
    legacy_value = _read_optional(read_required, "PAYMENTS_TEST_MODE")
    if explicit_value is None:
        if legacy_value == "true":
            return PaymentMode.TEST
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        )
    try:
        mode = PaymentMode(explicit_value)
    except ValueError:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        ) from None
    if legacy_value is not None:
        expected_legacy_value = "true" if mode is PaymentMode.TEST else "false"
        if legacy_value != expected_legacy_value:
            raise PaymentRuntimeConfigurationError(
                "Subscription configuration is invalid."
            )
    return mode


def _read_optional(read_required: Callable[[str], str], name: str) -> str | None:
    try:
        value = read_required(name)
    except Exception:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        )
    return value.strip()


def configure_payment_runtime(runtime: PaymentRuntime) -> None:
    global _configured_runtime
    if _configured_runtime is not None:
        raise PaymentRuntimeConfigurationError("Payment runtime is already configured.")
    _configured_runtime = runtime


def get_payment_runtime() -> PaymentRuntime:
    """Return dependencies validated during the role bootstrap."""
    if _configured_runtime is None:
        raise PaymentRuntimeConfigurationError("Payment runtime is not configured.")
    return _configured_runtime
