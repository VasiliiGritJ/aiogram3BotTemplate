"""Canonical receipt-email validation and profile persistence.

The address is collected only when the user enters the paid flow.  Keeping
this boundary independent from a payment provider prevents a Telegram handler
or a future fiscal adapter from inventing its own validation rules.
"""

from __future__ import annotations

import re
from typing import Callable

from sqlalchemy.orm import Session

from db.models import FitnessProfile, User, dbSession
from services.access import utc_now


MAX_RECEIPT_EMAIL_LENGTH = 254
RECEIPT_EMAIL_ERROR_MESSAGE = "Проверьте email и попробуйте ещё раз."
_EMAIL_PATTERN = re.compile(
    r"^[^@\s\x00-\x1f\x7f]{1,64}@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$",
    re.IGNORECASE,
)


class ReceiptEmailValidationError(ValueError):
    """Raised only for an address that is unsuitable for receipt delivery."""


class ReceiptEmailProfileUnavailable(RuntimeError):
    """The user has no completed profile in which the receipt contact is kept."""


class ReceiptEmailRequired(RuntimeError):
    """A new payment cannot start until its receipt-delivery contact exists."""


def normalize_receipt_email(value: str) -> str:
    """Return the canonical storage form without attempting full RFC parsing."""
    if not isinstance(value, str):
        raise ReceiptEmailValidationError(RECEIPT_EMAIL_ERROR_MESSAGE)
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > MAX_RECEIPT_EMAIL_LENGTH
        or not _EMAIL_PATTERN.fullmatch(normalized)
    ):
        raise ReceiptEmailValidationError(RECEIPT_EMAIL_ERROR_MESSAGE)
    return normalized


def receipt_email_in_session(session: Session, user_id: int) -> str | None:
    """Return only a valid canonical contact; legacy/malformed values are absent."""
    profile = session.get(FitnessProfile, user_id)
    if profile is None or profile.receipt_email is None:
        return None
    try:
        return normalize_receipt_email(profile.receipt_email)
    except ReceiptEmailValidationError:
        return None


def get_receipt_email(
    user_id: int,
    session_factory: Callable[[], Session] = dbSession,
) -> str | None:
    with session_factory() as session:
        return receipt_email_in_session(session, user_id)


def save_receipt_email(
    user_id: int,
    value: str,
    session_factory: Callable[[], Session] = dbSession,
) -> str:
    """Validate then persist the address atomically on the existing profile."""
    normalized = normalize_receipt_email(value)
    with session_factory() as session:
        with session.begin():
            if session.get(User, user_id) is None:
                raise LookupError("User does not exist.")
            profile = session.get(FitnessProfile, user_id)
            if profile is None:
                raise ReceiptEmailProfileUnavailable(
                    "Receipt email requires a completed profile."
                )
            profile.receipt_email = normalized
            profile.updated_at = utc_now()
    return normalized
