"""Centralized access status calculation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable

from sqlalchemy.orm import Session

from db.models import UserAccess, dbSession


class AccessStatus(StrEnum):
    ACTIVE = "active"
    TRIAL = "trial"
    TRIAL_AVAILABLE = "trial_available"
    EXPIRED = "expired"


@dataclass(frozen=True)
class AccessDecision:
    status: AccessStatus
    ends_at: datetime | None

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
        return AccessDecision(AccessStatus.EXPIRED, None)

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
        )

    if access.trial_started_at is None and access.trial_ends_at is None:
        return AccessDecision(AccessStatus.TRIAL_AVAILABLE, None)

    if (
        access.trial_started_at is not None
        and access.trial_ends_at is not None
        and as_utc_naive(access.trial_started_at) <= current_time
        < as_utc_naive(access.trial_ends_at)
    ):
        return AccessDecision(
            AccessStatus.TRIAL,
            as_utc_naive(access.trial_ends_at),
        )

    return AccessDecision(AccessStatus.EXPIRED, None)


@dataclass(frozen=True)
class TrialActivationResult:
    access: UserAccess
    activated: bool


class TrialActivationError(RuntimeError):
    """Raised when the persisted access record is not safe to activate."""


def activate_trial_once(
    user_id: int,
    now: datetime | None = None,
    session_factory: Callable[[], Session] = dbSession,
) -> TrialActivationResult:
    """Start a three-day trial once; a started or expired trial is never reset."""
    started_at = as_utc_naive(now) if now is not None else utc_now()
    with session_factory() as session:
        with session.begin():
            access = session.get(UserAccess, user_id)
            if access is None:
                raise TrialActivationError("Access record does not exist.")
            if (
                access.subscription_started_at is not None
                and access.subscription_ends_at is not None
                and as_utc_naive(access.subscription_started_at) <= started_at
                < as_utc_naive(access.subscription_ends_at)
            ):
                return TrialActivationResult(access, activated=False)
            if access.trial_started_at is None and access.trial_ends_at is None:
                access.trial_started_at = started_at
                access.trial_ends_at = started_at + timedelta(days=3)
                access.updated_at = started_at
                session.flush()
                return TrialActivationResult(access, activated=True)
            if access.trial_started_at is None or access.trial_ends_at is None:
                raise TrialActivationError("Trial dates are inconsistent.")
            return TrialActivationResult(access, activated=False)


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
