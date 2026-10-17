"""Centralized access status calculation."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session

from db.models import UserAccess, dbSession
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT


class AccessStatus(StrEnum):
    ACTIVE = "active"
    TRIAL = "trial"
    TRIAL_AVAILABLE = "trial_available"
    EXPIRED = "expired"


class AccessEntitlement(StrEnum):
    NO_ACCESS = "no_access"
    TRIAL_AVAILABLE = "trial_available"
    ACTIVE_TRIAL = "active_trial"
    ACTIVE_PAID = "active_paid"
    EXPIRED_TRIAL = "expired_trial"
    EXPIRED_PAID = "expired_paid"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True)
class AccessDecision:
    status: AccessStatus
    ends_at: datetime | None
    entitlement: AccessEntitlement | None = None

    @property
    def has_access(self) -> bool:
        return self.status in {AccessStatus.ACTIVE, AccessStatus.TRIAL}


def utc_now() -> datetime:
    """Return current UTC as a naive datetime for SQLite storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc_naive(value: datetime) -> datetime:
    """Normalize aware datetimes to UTC and treat naive values as UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def evaluate_access(
    access: UserAccess | None,
    now: datetime | None = None,
) -> AccessDecision:
    """Calculate access from dates without storing a derived status."""
    current_time = as_utc_naive(now) if now is not None else utc_now()
    if access is None:
        return AccessDecision(
            AccessStatus.EXPIRED,
            None,
            AccessEntitlement.NO_ACCESS,
        )

    subscription_started_at = access.subscription_started_at
    subscription_ends_at = access.subscription_ends_at
    if (
        subscription_started_at is not None
        and subscription_ends_at is not None
        and as_utc_naive(subscription_started_at) <= current_time
        < as_utc_naive(subscription_ends_at)
    ):
        return AccessDecision(
            AccessStatus.ACTIVE,
            as_utc_naive(subscription_ends_at),
            AccessEntitlement.ACTIVE_PAID,
        )

    trial_consumed = (
        access.trial_consumed_at is not None
        or access.trial_started_at is not None
        or access.trial_ends_at is not None
    )
    if not trial_consumed:
        # A paid period may be expired, but its persisted dates prove that this
        # user already consumed the first-access lifecycle.  Do not resurrect
        # a trial after paid history.
        if (
            access.subscription_started_at is not None
            or access.subscription_ends_at is not None
        ):
            return AccessDecision(
                AccessStatus.EXPIRED,
                None,
                AccessEntitlement.EXPIRED_PAID,
            )
        return AccessDecision(
            AccessStatus.TRIAL_AVAILABLE,
            None,
            AccessEntitlement.TRIAL_AVAILABLE,
        )

    if (
        access.trial_started_at is not None
        and access.trial_ends_at is not None
        and as_utc_naive(access.trial_started_at) <= current_time
        < as_utc_naive(access.trial_ends_at)
    ):
        return AccessDecision(
            AccessStatus.TRIAL,
            as_utc_naive(access.trial_ends_at),
            AccessEntitlement.ACTIVE_TRIAL,
        )

    if access.subscription_started_at is not None or access.subscription_ends_at is not None:
        entitlement = AccessEntitlement.EXPIRED_PAID
    elif access.trial_started_at is not None and access.trial_ends_at is not None:
        entitlement = AccessEntitlement.EXPIRED_TRIAL
    else:
        entitlement = AccessEntitlement.INCONSISTENT
    return AccessDecision(AccessStatus.EXPIRED, None, entitlement)


@dataclass(frozen=True)
class TrialActivationResult:
    access: UserAccess
    activated: bool


class TrialActivationError(RuntimeError):
    """Raised when the persisted access record is not safe to activate."""


def activate_trial_once_in_session(
    user_id: int,
    session: Session,
    now: datetime | None = None,
) -> TrialActivationResult:
    """Activate a trial inside an already open transaction.

    This is used by workflows that must make trial activation atomic with a
    second write, such as creating the first workout session.
    """
    started_at = as_utc_naive(now) if now is not None else utc_now()
    trial_ends_at = started_at + LAUNCH_COMMERCIAL_PRODUCT.trial_duration
    claimed = session.execute(
        update(UserAccess)
        .where(
            UserAccess.user_id == user_id,
            UserAccess.trial_consumed_at.is_(None),
            UserAccess.trial_started_at.is_(None),
            UserAccess.trial_ends_at.is_(None),
            UserAccess.subscription_started_at.is_(None),
            UserAccess.subscription_ends_at.is_(None),
        )
        .values(
            trial_consumed_at=started_at,
            trial_started_at=started_at,
            trial_ends_at=trial_ends_at,
            updated_at=started_at,
        )
        .execution_options(synchronize_session=False)
    )
    access = session.get(UserAccess, user_id)
    if access is None:
        raise TrialActivationError("Access record does not exist.")
    session.refresh(access)
    if claimed.rowcount == 1:
        return TrialActivationResult(access, activated=True)
    if (
        access.trial_consumed_at is None
        and (access.trial_started_at is None) != (access.trial_ends_at is None)
    ):
        raise TrialActivationError("Trial dates are inconsistent.")
    return TrialActivationResult(access, activated=False)


def activate_trial_once(
    user_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> TrialActivationResult:
    """Start the launch trial once; a started or expired trial is never reset."""
    with session_factory() as session:
        with session.begin():
            return activate_trial_once_in_session(user_id, session, now)


def get_user_access(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> UserAccess | None:
    with session_factory() as session:
        return session.get(UserAccess, user_id)


def get_access_decision(
    user_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> AccessDecision:
    return evaluate_access(get_user_access(user_id, session_factory), now)


def has_active_access(
    user_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> bool:
    return get_access_decision(user_id, now, session_factory).has_access
