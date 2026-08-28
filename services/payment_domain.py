"""Pure, deterministic payment domain rules for Stage 5."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class PaymentStatus(StrEnum):
    CREATING = "creating"
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    EXPIRED = "expired"
    FAILED = "failed"


class PaymentReasonCode(StrEnum):
    VALID = "valid"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CURRENCY = "invalid_currency"
    INVALID_PRODUCT = "invalid_product"
    INVALID_PERIOD = "invalid_period"
    STATUS_CHANGED = "status_changed"
    STATUS_ALREADY_CURRENT = "status_already_current"
    TERMINAL_STATUS_IMMUTABLE = "terminal_status_immutable"
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    NOT_SUCCEEDED = "not_succeeded"
    ACCESS_ALREADY_APPLIED = "access_already_applied"
    RETRY_SAME_KEY = "retry_same_key"
    UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION = (
        "unknown_outcome_requires_reconciliation"
    )
    NOT_CREATING = "not_creating"


class GrantBase(StrEnum):
    NOW = "now"
    ACTIVE_TRIAL = "active_trial"
    ACTIVE_SUBSCRIPTION = "active_subscription"


@dataclass(frozen=True)
class PaymentSpec:
    product_code: str
    amount_minor: int
    currency: str
    period_days: int


@dataclass(frozen=True)
class PaymentState:
    status: PaymentStatus
    spec: PaymentSpec
    created_at: datetime
    access_applied_at: datetime | None = None


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    reason: PaymentReasonCode


@dataclass(frozen=True)
class StatusTransition:
    accepted: bool
    status: PaymentStatus
    reason: PaymentReasonCode


@dataclass(frozen=True)
class AccessEligibility:
    can_apply: bool
    reason: PaymentReasonCode


@dataclass(frozen=True)
class CreatingRecovery:
    retry_same_key: bool
    reason: PaymentReasonCode


@dataclass(frozen=True)
class SubscriptionGrant:
    started_at: datetime
    ends_at: datetime
    base: GrantBase


_TERMINAL_STATUSES = frozenset(
    {
        PaymentStatus.SUCCEEDED,
        PaymentStatus.CANCELED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    }
)
_ALLOWED_TRANSITIONS = {
    PaymentStatus.CREATING: frozenset(
        {
            PaymentStatus.PENDING,
            PaymentStatus.WAITING_FOR_CAPTURE,
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CANCELED,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }
    ),
    PaymentStatus.PENDING: frozenset(
        {
            PaymentStatus.WAITING_FOR_CAPTURE,
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CANCELED,
            PaymentStatus.EXPIRED,
        }
    ),
    PaymentStatus.WAITING_FOR_CAPTURE: frozenset(
        {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CANCELED,
            PaymentStatus.EXPIRED,
        }
    ),
}


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_payment_spec(spec: PaymentSpec) -> ValidationResult:
    if (
        isinstance(spec.amount_minor, bool)
        or not isinstance(spec.amount_minor, int)
        or spec.amount_minor <= 0
    ):
        return ValidationResult(False, PaymentReasonCode.INVALID_AMOUNT)
    if (
        not isinstance(spec.currency, str)
        or len(spec.currency) != 3
        or not spec.currency.isalpha()
        or spec.currency != spec.currency.upper()
    ):
        return ValidationResult(False, PaymentReasonCode.INVALID_CURRENCY)
    if not isinstance(spec.product_code, str) or not spec.product_code.strip():
        return ValidationResult(False, PaymentReasonCode.INVALID_PRODUCT)
    if (
        isinstance(spec.period_days, bool)
        or not isinstance(spec.period_days, int)
        or spec.period_days <= 0
    ):
        return ValidationResult(False, PaymentReasonCode.INVALID_PERIOD)
    return ValidationResult(True, PaymentReasonCode.VALID)


def transition_payment_status(
    current: PaymentStatus,
    requested: PaymentStatus,
) -> StatusTransition:
    if current is requested:
        return StatusTransition(True, current, PaymentReasonCode.STATUS_ALREADY_CURRENT)
    if current in _TERMINAL_STATUSES:
        return StatusTransition(
            False, current, PaymentReasonCode.TERMINAL_STATUS_IMMUTABLE
        )
    if requested in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        return StatusTransition(True, requested, PaymentReasonCode.STATUS_CHANGED)
    return StatusTransition(
        False, current, PaymentReasonCode.INVALID_STATUS_TRANSITION
    )


def get_access_eligibility(payment: PaymentState) -> AccessEligibility:
    validation = validate_payment_spec(payment.spec)
    if not validation.is_valid:
        return AccessEligibility(False, validation.reason)
    if payment.status is not PaymentStatus.SUCCEEDED:
        return AccessEligibility(False, PaymentReasonCode.NOT_SUCCEEDED)
    if payment.access_applied_at is not None:
        return AccessEligibility(False, PaymentReasonCode.ACCESS_ALREADY_APPLIED)
    return AccessEligibility(True, PaymentReasonCode.VALID)


def get_creating_recovery(
    payment: PaymentState,
    now: datetime,
    provider_idempotency_window: timedelta,
) -> CreatingRecovery:
    """Classify a create retry without assuming a provider's retention window."""
    if payment.status is not PaymentStatus.CREATING:
        return CreatingRecovery(False, PaymentReasonCode.NOT_CREATING)
    if provider_idempotency_window < timedelta(0):
        raise ValueError("provider_idempotency_window must not be negative")

    age = _as_utc_naive(now) - _as_utc_naive(payment.created_at)
    if age <= provider_idempotency_window:
        return CreatingRecovery(True, PaymentReasonCode.RETRY_SAME_KEY)
    return CreatingRecovery(
        False, PaymentReasonCode.UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION
    )


def calculate_subscription_grant(
    *,
    now: datetime,
    period_days: int,
    trial_ends_at: datetime | None = None,
    subscription_ends_at: datetime | None = None,
) -> SubscriptionGrant:
    """Return the immutable 30-day-style paid period without mutating access."""
    if (
        isinstance(period_days, bool)
        or not isinstance(period_days, int)
        or period_days <= 0
    ):
        raise ValueError("period_days must be a positive integer")

    current_time = _as_utc_naive(now)
    active_subscription_end = (
        _as_utc_naive(subscription_ends_at)
        if subscription_ends_at is not None
        and _as_utc_naive(subscription_ends_at) > current_time
        else None
    )
    active_trial_end = (
        _as_utc_naive(trial_ends_at)
        if trial_ends_at is not None and _as_utc_naive(trial_ends_at) > current_time
        else None
    )

    if active_subscription_end is not None:
        started_at = active_subscription_end
        base = GrantBase.ACTIVE_SUBSCRIPTION
    elif active_trial_end is not None:
        started_at = active_trial_end
        base = GrantBase.ACTIVE_TRIAL
    else:
        started_at = current_time
        base = GrantBase.NOW

    return SubscriptionGrant(
        started_at=started_at,
        ends_at=started_at + timedelta(days=period_days),
        base=base,
    )
