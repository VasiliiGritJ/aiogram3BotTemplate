"""Durable, bounded and SQLite-safe NPD receipt outbox processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from db.models import PaymentReceipt, SubscriptionPayment, dbSession
from services.access import as_utc_naive, utc_now
from services.receipt_domain import (
    NPD_RECEIPT_DESCRIPTION,
    ReceiptIntent,
    ReceiptProcessingReason,
    ReceiptProviderOutcome,
    ReceiptRetryPolicy,
    ReceiptStatus,
    sanitize_receipt_error_code,
)
from services.receipt_provider import (
    ReceiptProvider,
    ReceiptProviderPermanentError,
    ReceiptProviderResult,
    ReceiptProviderRetryableError,
)
from services.receipt_email import receipt_email_in_session


@dataclass(frozen=True)
class ReceiptProcessingResult:
    receipt_id: int | None
    status: ReceiptStatus | None
    reason: ReceiptProcessingReason
    attempts: int = 0


@dataclass(frozen=True)
class _ClaimedReceipt:
    receipt_id: int
    claim_token: str
    idempotency_key: str
    attempts: int
    intent: ReceiptIntent


def ensure_receipt_intent(
    session: Session,
    payment: SubscriptionPayment,
    *,
    now: datetime,
) -> PaymentReceipt:
    """Ensure one immutable receipt intent inside the payment transaction."""
    existing = session.scalar(
        select(PaymentReceipt).where(
            PaymentReceipt.subscription_payment_id == payment.id
        )
    )
    if existing is not None:
        return existing
    if payment.provider_payment_id is None:
        raise RuntimeError("Succeeded payment has no provider reference.")
    income_received_at = payment.confirmed_at or now
    idempotency_key = str(
        uuid5(
            NAMESPACE_URL,
            f"npd-income:{payment.id}:{payment.idempotency_key}",
        )
    )
    receipt = PaymentReceipt(
        subscription_payment_id=payment.id,
        idempotency_key=idempotency_key,
        status=ReceiptStatus.PENDING.value,
        attempts=0,
        product_code=payment.product_code,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        service_period_days=payment.period_days,
        item_description=NPD_RECEIPT_DESCRIPTION,
        payment_reference=payment.provider_payment_id,
        buyer_contact=payment.buyer_email
        or receipt_email_in_session(session, payment.user_id),
        income_received_at=income_received_at,
        created_at=now,
        updated_at=now,
    )
    session.add(receipt)
    return receipt


class ReceiptOutboxService:
    """Claim and process at most one due receipt per explicit iteration."""

    def __init__(
        self,
        provider: ReceiptProvider,
        *,
        session_factory: Callable[[], Session] = dbSession,
        now_factory: Callable[[], datetime] = utc_now,
        retry_policy: ReceiptRetryPolicy = ReceiptRetryPolicy(),
        claim_token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._retry_policy = retry_policy
        self._claim_token_factory = claim_token_factory or (lambda: str(uuid4()))

    def process_one(self) -> ReceiptProcessingResult:
        claim = self._claim_next()
        if claim is None:
            return ReceiptProcessingResult(
                None,
                None,
                ReceiptProcessingReason.NOTHING_DUE,
            )
        try:
            provider_result = self._provider.register_income_receipt(
                claim.intent,
                claim.idempotency_key,
            )
        except ReceiptProviderPermanentError:
            provider_result = ReceiptProviderResult(
                ReceiptProviderOutcome.PERMANENT_FAILURE,
                error_code="provider_rejected",
            )
        except ReceiptProviderRetryableError:
            provider_result = ReceiptProviderResult(
                ReceiptProviderOutcome.RETRYABLE_FAILURE,
                error_code="provider_unavailable",
            )
        except Exception:
            provider_result = ReceiptProviderResult(
                ReceiptProviderOutcome.RETRYABLE_FAILURE,
                error_code="provider_unavailable",
            )
        return self._store_provider_result(claim, provider_result)

    def _claim_next(self) -> _ClaimedReceipt | None:
        now = self._now()
        stale_before = now - self._retry_policy.processing_lease
        with self._session_factory() as session:
            with session.begin():
                receipt = session.scalar(
                    select(PaymentReceipt)
                    .where(
                        or_(
                            PaymentReceipt.status == ReceiptStatus.PENDING.value,
                            and_(
                                PaymentReceipt.status
                                == ReceiptStatus.RETRYABLE_FAILED.value,
                                PaymentReceipt.next_attempt_at.is_not(None),
                                PaymentReceipt.next_attempt_at <= now,
                            ),
                            and_(
                                PaymentReceipt.status
                                == ReceiptStatus.PROCESSING.value,
                                PaymentReceipt.processing_started_at.is_not(None),
                                PaymentReceipt.processing_started_at <= stale_before,
                            ),
                        )
                    )
                    .order_by(PaymentReceipt.id)
                    .limit(1)
                )
                if receipt is None:
                    return None
                previous_status = receipt.status
                previous_claim = receipt.claim_token
                attempts = receipt.attempts + 1
                claim_token = self._claim_token_factory()
                predicate = (
                    PaymentReceipt.id == receipt.id,
                    PaymentReceipt.status == previous_status,
                )
                if previous_status == ReceiptStatus.PROCESSING.value:
                    predicate += (PaymentReceipt.claim_token == previous_claim,)
                claimed = session.execute(
                    update(PaymentReceipt)
                    .where(*predicate)
                    .values(
                        status=ReceiptStatus.PROCESSING.value,
                        attempts=attempts,
                        claim_token=claim_token,
                        processing_started_at=now,
                        updated_at=now,
                    )
                )
                if claimed.rowcount != 1:
                    return None
                intent = ReceiptIntent(
                    receipt_id=receipt.id,
                    subscription_payment_id=receipt.subscription_payment_id,
                    amount_minor=receipt.amount_minor,
                    currency=receipt.currency,
                    service_period_days=receipt.service_period_days,
                    item_description=receipt.item_description,
                    payment_reference=receipt.payment_reference,
                    income_received_at=receipt.income_received_at,
                    buyer_contact=receipt.buyer_contact,
                )
                return _ClaimedReceipt(
                    receipt.id,
                    claim_token,
                    receipt.idempotency_key,
                    attempts,
                    intent,
                )

    def _store_provider_result(
        self,
        claim: _ClaimedReceipt,
        provider_result: ReceiptProviderResult,
    ) -> ReceiptProcessingResult:
        now = self._now()
        status: ReceiptStatus
        reason: ReceiptProcessingReason
        next_attempt_at = None
        succeeded_at = None
        provider_receipt_id = None
        error_code = None

        if (
            provider_result.outcome is ReceiptProviderOutcome.SUCCEEDED
            and isinstance(provider_result.provider_receipt_id, str)
            and provider_result.provider_receipt_id.strip()
        ):
            status = ReceiptStatus.SUCCEEDED
            reason = ReceiptProcessingReason.SUCCEEDED
            succeeded_at = now
            provider_receipt_id = provider_result.provider_receipt_id.strip()
        elif provider_result.outcome is ReceiptProviderOutcome.PERMANENT_FAILURE:
            status = ReceiptStatus.PERMANENT_FAILED
            reason = ReceiptProcessingReason.PERMANENT_FAILURE
            error_code = sanitize_receipt_error_code(
                provider_result.error_code,
                fallback="provider_rejected",
            )
        elif provider_result.outcome is ReceiptProviderOutcome.RETRYABLE_FAILURE:
            next_attempt_at = self._retry_policy.next_attempt_at(
                now=now,
                attempts=claim.attempts,
            )
            if next_attempt_at is None:
                status = ReceiptStatus.PERMANENT_FAILED
                reason = ReceiptProcessingReason.RETRIES_EXHAUSTED
                error_code = "retry_exhausted"
            else:
                status = ReceiptStatus.RETRYABLE_FAILED
                reason = ReceiptProcessingReason.RETRY_SCHEDULED
                error_code = sanitize_receipt_error_code(
                    provider_result.error_code,
                    fallback="provider_unavailable",
                )
        else:
            status = ReceiptStatus.PERMANENT_FAILED
            reason = ReceiptProcessingReason.PERMANENT_FAILURE
            error_code = "provider_protocol_error"

        with self._session_factory() as session:
            with session.begin():
                stored = session.execute(
                    update(PaymentReceipt)
                    .where(
                        PaymentReceipt.id == claim.receipt_id,
                        PaymentReceipt.status == ReceiptStatus.PROCESSING.value,
                        PaymentReceipt.claim_token == claim.claim_token,
                    )
                    .values(
                        status=status.value,
                        next_attempt_at=next_attempt_at,
                        processing_started_at=None,
                        claim_token=None,
                        provider_receipt_id=provider_receipt_id,
                        succeeded_at=succeeded_at,
                        last_error_code=error_code,
                        updated_at=now,
                    )
                )
                if stored.rowcount != 1:
                    return ReceiptProcessingResult(
                        claim.receipt_id,
                        None,
                        ReceiptProcessingReason.STALE_CLAIM,
                        claim.attempts,
                    )
        return ReceiptProcessingResult(
            claim.receipt_id,
            status,
            reason,
            claim.attempts,
        )

    def _now(self) -> datetime:
        return as_utc_naive(self._now_factory())
