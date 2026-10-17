"""Trusted operator reconciliation of one *completed* full Prodamus refund.

No public route, browser return, checkout-cancellation event or guessed webhook
status invokes this service. An authorized operator must first independently
verify Prodamus status «Возвращён», the matching refund document and identifiers.
This records that evidence, not a refund request, tax filing or provider API call.
Production execution/migration requires separate owner authorization.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
import re
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.models import PaymentRefund, SubscriptionPayment, UserAccess, dbSession
from services.access import as_utc_naive, utc_now


class RefundStatus(StrEnum):
    CONFIRMED = "refunded"


class RefundRejected(ValueError):
    """Contains only a fixed reason code; never external evidence or identity."""


@dataclass(frozen=True, repr=False)
class VerifiedFullRefund:
    payment_id: int
    user_id: int
    transaction_id: str
    provider_payment_id: str
    provider_refund_id: str
    evidence_sha256: str
    amount_minor: int
    currency: str
    confirmed_at: datetime
    # Only a completed provider refund is admissible, never «Возвращается».
    provider_status: str = ""


@dataclass(frozen=True)
class RefundResult:
    applied: bool
    access_end_after: datetime = field(repr=False)


class FullRefundService:
    def __init__(self, *, session_factory: Callable[[], Session] = dbSession,
                 now_factory: Callable[[], datetime] = utc_now):
        self._sessions = session_factory
        self._now = now_factory

    def reconcile_verified(self, proof: VerifiedFullRefund) -> RefundResult:
        """Internal trusted boundary, like confirm_authoritative_payment.

        Caller authentication/evidence verification happens OUTSIDE this method;
        accepting a dataclass is not independent proof of a provider refund.
        There is intentionally no user-facing caller or automatic tax correction.
        """
        now = as_utc_naive(self._now())
        if (proof.provider_status != "returned"
                or type(proof.amount_minor) is not int or proof.amount_minor != 49900
                or proof.currency != "RUB"
                or not re.fullmatch(r"[0-9a-f]{64}", proof.evidence_sha256)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", proof.provider_refund_id)):
            raise RefundRejected("invalid_completed_refund_evidence")
        confirmed = as_utc_naive(proof.confirmed_at)
        if confirmed > now:
            raise RefundRejected("refund_time_invalid")
        with self._sessions() as session, session.begin():
            # Serialize read/modify/write across workers; duplicates see committed
            # history. Payment confirmations flush their write before access read.
            session.execute(text("BEGIN IMMEDIATE"))
            payment = session.get(SubscriptionPayment, proof.payment_id)
            if (payment is None or payment.provider != "prodamus"
                    or payment.user_id != proof.user_id
                    or payment.merchant_transaction_id != proof.transaction_id
                    or payment.provider_payment_id != proof.provider_payment_id
                    or payment.status != "succeeded"
                    or payment.amount_minor != proof.amount_minor
                    or payment.currency != proof.currency or payment.period_days != 30
                    or payment.access_applied_at is None or payment.confirmed_at is None
                    or payment.confirmed_at > confirmed):
                raise RefundRejected("payment_binding_invalid")
            previous = session.get(PaymentRefund, payment.id)
            if previous is not None:
                if (previous.provider_refund_id != proof.provider_refund_id
                        or previous.evidence_sha256 != proof.evidence_sha256
                        or previous.confirmed_at != confirmed):
                    raise RefundRejected("conflicting_refund_evidence")
                return RefundResult(False, previous.access_end_after)
            reused = session.scalar(select(PaymentRefund).where(
                PaymentRefund.provider_refund_id == proof.provider_refund_id))
            if reused is not None:
                raise RefundRejected("refund_reference_reused")
            start, end = payment.grant_started_at, payment.grant_ends_at
            access = session.get(UserAccess, payment.user_id)
            if (start is None or end is None or end - start != timedelta(days=30)
                    or access is None or access.subscription_ends_at != end):
                # No guessing/subtracting from a later purchase or manual grant.
                raise RefundRejected("access_requires_manual_reconciliation")
            # Preserve an earlier paid tail / trial. Expired access is not
            # resurrected; truncate only this grant's remaining interval.
            after = min(end, max(start, confirmed))
            access.subscription_ends_at = after
            access.updated_at = now
            session.add(PaymentRefund(
                payment_id=payment.id, provider_refund_id=proof.provider_refund_id,
                evidence_sha256=proof.evidence_sha256, amount_minor=proof.amount_minor,
                currency=proof.currency, confirmed_at=confirmed, reconciled_at=now,
                access_end_before=end, access_end_after=after,
            ))
            # Keep payment/grant/access_applied_at and receipt history untouched.
            # A late success callback therefore cannot issue this grant again.
            return RefundResult(True, after)
