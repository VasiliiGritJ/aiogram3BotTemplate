"""Server-owned commercial facts for the current launch offer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommercialProduct:
    service_name: str
    product_code: str
    receipt_description: str
    amount_minor: int
    currency: str
    period_days: int
    auto_renew: bool

    def __post_init__(self) -> None:
        if not self.service_name.strip() or not self.product_code.strip():
            raise ValueError("Commercial product identity is invalid.")
        if not self.receipt_description.strip() or self.amount_minor <= 0:
            raise ValueError("Commercial product facts are invalid.")
        if self.currency != "RUB" or self.period_days != 30:
            raise ValueError("Commercial product launch contract is invalid.")
        if self.auto_renew:
            raise ValueError("The launch offer must not auto-renew.")

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
)
