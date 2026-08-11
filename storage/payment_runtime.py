"""Lazy application composition for the paid-subscription flow.

No environment file is read while importing this module.  The production
factory is called only by the subscription UI and requires every commercial
value explicitly, so a price is never silently invented.
"""

from dataclasses import dataclass
from typing import Callable

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
    try:
        amount_minor = int(read_required("SUBSCRIPTION_AMOUNT_MINOR"))
    except (TypeError, ValueError):
        raise PaymentRuntimeConfigurationError(
            "Subscription amount is not configured."
        ) from None
    currency = read_required("SUBSCRIPTION_CURRENCY").strip()
    return_url = read_required("YOOKASSA_RETURN_URL").strip()
    try:
        product = SubscriptionProduct(
            product_code=PRODUCT_CODE,
            amount_minor=amount_minor,
            currency=currency,
            period_days=PERIOD_DAYS,
        )
        credentials = YooKassaCredentials(
            shop_id=read_required("YOOKASSA_SHOP_ID").strip(),
            secret_key=read_required("YOOKASSA_SECRET_TOKEN"),
        )
        provider = YooKassaProvider(credentials, return_url)
        payment_service = PaymentService(provider, product)
    except ValueError as error:
        raise PaymentRuntimeConfigurationError(
            "Subscription configuration is invalid."
        ) from error
    return PaymentRuntime(product, payment_service)


def get_payment_runtime() -> PaymentRuntime:
    """Lazily compose production payment dependencies outside Telegram handlers."""
    from storage.config import get_value_from_env

    return build_payment_runtime(get_value_from_env)
