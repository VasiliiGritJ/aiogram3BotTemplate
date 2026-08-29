"""Pure provider-neutral domain for durable NPD income receipt delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re


NPD_RECEIPT_DESCRIPTION = "Доступ к сервису «Мой AI Тренер» на 30 дней"
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class ReceiptStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"
    VOIDED = "voided"


class ReceiptProviderOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


class ReceiptProcessingReason(StrEnum):
    NOTHING_DUE = "nothing_due"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRIES_EXHAUSTED = "retries_exhausted"
    PERMANENT_FAILURE = "permanent_failure"
    STALE_CLAIM = "stale_claim"


@dataclass(frozen=True)
class ReceiptIntent:
    receipt_id: int
    subscription_payment_id: int
    amount_minor: int
    currency: str
    service_period_days: int
    item_description: str
    payment_reference: str
    income_received_at: datetime
    buyer_contact: str | None = None

    def __post_init__(self) -> None:
        if self.receipt_id <= 0 or self.subscription_payment_id <= 0:
            raise ValueError("Receipt identity is invalid.")
        if self.amount_minor <= 0:
            raise ValueError("Receipt amount is invalid.")
        if len(self.currency) != 3 or self.currency != self.currency.upper():
            raise ValueError("Receipt currency is invalid.")
        if self.service_period_days <= 0 or not self.item_description.strip():
            raise ValueError("Receipt item is invalid.")
        if not self.payment_reference.strip():
            raise ValueError("Receipt payment reference is invalid.")


@dataclass(frozen=True)
class ReceiptRetryPolicy:
    delays: tuple[timedelta, ...] = (
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=12),
    )
    processing_lease: timedelta = timedelta(minutes=10)

    @property
    def max_attempts(self) -> int:
        return len(self.delays) + 1

    def next_attempt_at(self, *, now: datetime, attempts: int) -> datetime | None:
        if attempts <= 0:
            raise ValueError("Receipt attempts must be positive after a claim.")
        if attempts >= self.max_attempts:
            return None
        return now + self.delays[attempts - 1]


def sanitize_receipt_error_code(value: str | None, *, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    if not _SAFE_ERROR_CODE.fullmatch(fallback):
        raise ValueError("Fallback receipt error code is invalid.")
    return fallback
