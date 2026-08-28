"""Framework-neutral processing of YooKassa payment notifications.

The notification only selects an existing local payment.  Its status, amount,
currency and metadata are never trusted: ``PaymentService`` always loads the
authoritative provider state before any local transition or access grant.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from services.payment_service import (
    PaymentService,
    PaymentServiceReason,
    PaymentServiceResult,
)


YOOKASSA_PROVIDER = "yookassa"
SUPPORTED_PAYMENT_EVENTS = frozenset(
    {
        "payment.waiting_for_capture",
        "payment.succeeded",
        "payment.canceled",
    }
)


class PaymentWebhookReason(StrEnum):
    RECONCILED = "reconciled"
    MALFORMED_PAYLOAD = "malformed_payload"
    UNSUPPORTED_EVENT = "unsupported_event"
    PAYMENT_NOT_FOUND = "payment_not_found"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RECONCILE_REJECTED = "reconcile_rejected"


@dataclass(frozen=True)
class PaymentWebhookResult:
    reason: PaymentWebhookReason
    service_result: PaymentServiceResult | None = None

    @property
    def access_applied(self) -> bool:
        return bool(self.service_result and self.service_result.access_applied)


_REJECTED_RECONCILE_REASONS = frozenset(
    {
        PaymentServiceReason.PROVIDER_ID_MISMATCH,
        PaymentServiceReason.AMOUNT_MISMATCH,
        PaymentServiceReason.CURRENCY_MISMATCH,
        PaymentServiceReason.METADATA_MISMATCH,
        PaymentServiceReason.PAYMENT_MODE_REJECTED,
        PaymentServiceReason.STATUS_REJECTED,
    }
)


class YooKassaWebhookProcessor:
    """Validate notification shape and reconcile an existing payment only."""

    def __init__(self, payment_service: PaymentService) -> None:
        self._payment_service = payment_service

    def process(self, payload: object) -> PaymentWebhookResult:
        if not isinstance(payload, Mapping):
            return PaymentWebhookResult(PaymentWebhookReason.MALFORMED_PAYLOAD)
        if payload.get("type") != "notification":
            return PaymentWebhookResult(PaymentWebhookReason.MALFORMED_PAYLOAD)
        event = payload.get("event")
        if not isinstance(event, str):
            return PaymentWebhookResult(PaymentWebhookReason.MALFORMED_PAYLOAD)
        if event not in SUPPORTED_PAYMENT_EVENTS:
            return PaymentWebhookResult(PaymentWebhookReason.UNSUPPORTED_EVENT)
        payment_object = payload.get("object")
        if not isinstance(payment_object, Mapping):
            return PaymentWebhookResult(PaymentWebhookReason.MALFORMED_PAYLOAD)
        provider_payment_id = payment_object.get("id")
        if not isinstance(provider_payment_id, str) or not provider_payment_id:
            return PaymentWebhookResult(PaymentWebhookReason.MALFORMED_PAYLOAD)

        try:
            service_result = self._payment_service.reconcile_provider_payment(
                YOOKASSA_PROVIDER,
                provider_payment_id,
            )
        except LookupError:
            return PaymentWebhookResult(PaymentWebhookReason.PAYMENT_NOT_FOUND)

        if service_result.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE:
            reason = PaymentWebhookReason.PROVIDER_UNAVAILABLE
        elif service_result.reason in _REJECTED_RECONCILE_REASONS:
            reason = PaymentWebhookReason.RECONCILE_REJECTED
        else:
            reason = PaymentWebhookReason.RECONCILED
        return PaymentWebhookResult(reason, service_result)
