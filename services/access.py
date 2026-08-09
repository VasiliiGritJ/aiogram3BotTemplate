"""Centralized access status calculation."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable

from sqlalchemy.orm import Session

from db.models import UserAccess, dbSession


class AccessStatus(StrEnum):
    ACTIVE = "active"
    TRIAL = "trial"
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

    if (
        as_utc_naive(access.trial_started_at) <= current_time
        < as_utc_naive(access.trial_ends_at)
    ):
        return AccessDecision(
            AccessStatus.TRIAL,
            as_utc_naive(access.trial_ends_at),
        )

    return AccessDecision(AccessStatus.EXPIRED, None)


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
