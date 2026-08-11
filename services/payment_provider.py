"""Provider-neutral contracts for the Stage 5 payment workflow.

This module deliberately contains no provider SDK code.  A concrete YooKassa
adapter can implement the protocol in a later phase without leaking its types
into the payment domain or service.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

from services.payment_domain import PaymentStatus, PaymentSpec


class PaymentProviderError(RuntimeError):
    """A controlled provider-side failure that must not grant access."""


class PaymentProviderUnavailable(PaymentProviderError):
    """The provider outcome is unknown and requires a later reconciliation."""


@dataclass(frozen=True)
class PaymentCreateRequest:
    """The server-owned data sent to a payment provider."""

    local_payment_id: int
    spec: PaymentSpec
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPayment:
    """Normalized provider response, independent from a provider SDK."""

    provider_payment_id: str
    status: PaymentStatus
    amount_minor: int
    currency: str
    confirmation_url: str | None = None
    expires_at: datetime | None = None
    cancellation_code: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """Minimal synchronous contract used by the local MVP service."""

    def create_payment(
        self,
        request: PaymentCreateRequest,
        idempotency_key: str,
    ) -> ProviderPayment:
        """Create or recover one provider payment for the supplied key."""

    def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        """Return the provider's latest authoritative payment state."""
