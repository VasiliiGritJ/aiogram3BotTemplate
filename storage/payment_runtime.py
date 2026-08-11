"""Lazy application composition for the paid-subscription flow.

No environment file is read while importing this module.  The production
factory is called only by the subscription UI and requires every commercial
value explicitly, so a price is never silently invented.
"""

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from services.payment_service import PaymentService, SubscriptionProduct
from services.yookassa_provider import YooKassaCredentials, YooKassaProvider


PRODUCT_CODE = "monthly_30d_v1"
PERIOD_DAYS = 30


class PaymentRuntimeConfigurationError(RuntimeError):
    """Raised when required server-side payment configuration is missing."""


@dataclass(frozen=True)
class PaymentRuntime:
    product: SubscriptionProduct
    payment_service: PaymentService


def build_payment_runtime(read_required: Callable[[str], str]) -> PaymentRuntime:
    """Build the live composition only from injected server-side settings."""
    test_mode = _read_test_mode(read_required)
    amount_value = _read_required(read_required, "SUBSCRIPTION_AMOUNT_MINOR")
    try:
        amount_minor = int(amount_value)
    except ValueError:
        raise PaymentRuntimeConfigurationError(
            "Subscription amount is not configured."
        ) from None
    currency = _read_required(read_required, "SUBSCRIPTION_CURRENCY")
    return_url = _read_required(read_required, "YOOKASSA_RETURN_URL")
    parsed_return_url = urlparse(return_url)
    if parsed_return_url.scheme not in {"http", "https"} or not parsed_return_url.netloc:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        )
    try:
        product = SubscriptionProduct(
            product_code=PRODUCT_CODE,
            amount_minor=amount_minor,
            currency=currency,
            period_days=PERIOD_DAYS,
        )
        credentials = YooKassaCredentials(
            shop_id=_read_required(read_required, "YOOKASSA_SHOP_ID"),
            secret_key=_read_required(read_required, "YOOKASSA_SECRET_TOKEN"),
        )
        provider = YooKassaProvider(
            credentials,
            return_url,
            require_test_mode=test_mode,
        )
        payment_service = PaymentService(
            provider,
            product,
            require_test_mode=test_mode,
        )
    except ValueError as error:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        ) from error
    return PaymentRuntime(product, payment_service)


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


def _read_test_mode(read_required: Callable[[str], str]) -> bool:
    value = _read_required(read_required, "PAYMENTS_TEST_MODE")
    if value != "true":
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        )
    return True


def get_payment_runtime() -> PaymentRuntime:
    """Lazily compose production payment dependencies outside Telegram handlers."""
    from storage.config import get_value_from_env

    return build_payment_runtime(get_value_from_env)
