"""Provider port for NPD receipt registration and future reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from services.receipt_domain import ReceiptIntent, ReceiptProviderOutcome


class ReceiptProviderError(RuntimeError):
    """Controlled provider failure with no raw external response."""


class ReceiptProviderRetryableError(ReceiptProviderError):
    """Temporary failure that may be retried with the same idempotency key."""


class ReceiptProviderPermanentError(ReceiptProviderError):
    """Configuration or request failure that must not busy-loop."""


@dataclass(frozen=True)
class ReceiptProviderResult:
    outcome: ReceiptProviderOutcome
    provider_receipt_id: str | None = None
    error_code: str | None = None


@runtime_checkable
class ReceiptProvider(Protocol):
    def register_income_receipt(
        self,
        intent: ReceiptIntent,
        idempotency_key: str,
    ) -> ReceiptProviderResult:
        """Register one NPD income receipt idempotently."""

    def get_receipt(self, provider_receipt_id: str) -> ReceiptProviderResult:
        """Future reconciliation capability; unused by the Stage 8D1 worker."""

    def void_receipt(
        self,
        provider_receipt_id: str,
        idempotency_key: str,
    ) -> ReceiptProviderResult:
        """Future correction capability; refunds are outside Stage 8D1."""
