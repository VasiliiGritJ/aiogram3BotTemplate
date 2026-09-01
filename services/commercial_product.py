"""Server-owned commercial facts for the current launch offer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


TRIAL_DAYS = 3
TRIAL_DURATION = timedelta(hours=72)
TRIAL_PRICE_MINOR = 0
TRIAL_REQUIRES_PAYMENT_METHOD = False
TRIAL_AUTO_RENEW = False
TRIAL_LIFETIME_LIMIT = 1


@dataclass(frozen=True)
class CommercialProduct:
    service_name: str
    product_code: str
    receipt_description: str
    amount_minor: int
    currency: str
    period_days: int
    auto_renew: bool
    trial_duration: timedelta
    trial_price_minor: int
    trial_requires_payment_method: bool
    trial_auto_renew: bool
    trial_lifetime_limit: int

    def __post_init__(self) -> None:
        if not self.service_name.strip() or not self.product_code.strip():
            raise ValueError("Commercial product identity is invalid.")
        if not self.receipt_description.strip() or self.amount_minor <= 0:
            raise ValueError("Commercial product facts are invalid.")
        if self.currency != "RUB" or self.period_days != 30:
            raise ValueError("Commercial product launch contract is invalid.")
        if self.auto_renew:
            raise ValueError("The launch offer must not auto-renew.")
        if (
            self.trial_duration != TRIAL_DURATION
            or self.trial_price_minor != TRIAL_PRICE_MINOR
            or self.trial_requires_payment_method
            or self.trial_auto_renew
            or self.trial_lifetime_limit != TRIAL_LIFETIME_LIMIT
        ):
            raise ValueError("The launch trial contract is invalid.")

    @property
    def trial_days(self) -> int:
        return self.trial_duration.days

    @property
    def payment_product(self):
        from services.payment_service import SubscriptionProduct

        return SubscriptionProduct(
            self.product_code,
            self.amount_minor,
            self.currency,
            self.period_days,
        )


LAUNCH_COMMERCIAL_PRODUCT = CommercialProduct(
    service_name="Мой AI Тренер",
    product_code="monthly_30d_v1",
    receipt_description="Доступ к сервису «Мой AI Тренер» на 30 дней",
    amount_minor=49_900,
    currency="RUB",
    period_days=30,
    auto_renew=False,
    trial_duration=TRIAL_DURATION,
    trial_price_minor=TRIAL_PRICE_MINOR,
    trial_requires_payment_method=TRIAL_REQUIRES_PAYMENT_METHOD,
    trial_auto_renew=TRIAL_AUTO_RENEW,
    trial_lifetime_limit=TRIAL_LIFETIME_LIMIT,
)
