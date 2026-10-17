"""Offline, explicit-session maintenance. No production DB or scheduler defaults.

Operator verifies identity, purpose/retention and backup disposition separately.
Functions do not authorize a real operation and never log personal data.
"""

from datetime import datetime

from sqlalchemy import delete, select, func, update

from db.models import (User, FitnessProfile, UserAccess, UserWorkoutPlan,
                       WorkoutSession, WebsitePurchaseSession, SubscriptionPayment,
                       Payments)


class PrivacyReviewRequired(ValueError):
    pass


def purge_expired_purchase_sessions(session, *, now: datetime) -> int:
    """Run in operator's transaction; removes only expired authentication rows."""
    _guard(session, True)
    result = session.execute(delete(WebsitePurchaseSession).where(
        WebsitePurchaseSession.expires_at <= now))
    return result.rowcount


def erase_training_data(session, *, user_id: int, identity_verified: bool) -> dict:
    """Approved training-purpose withdrawal; financial/access ledger untouched."""
    _guard(session, identity_verified)
    if session.get(User, user_id) is None:
        raise PrivacyReviewRequired("Account not found.")
    counts = {}
    # SQLite FK CASCADE removes sets/blocks/plan children; FK check is mandatory.
    for model in (WorkoutSession, UserWorkoutPlan, FitnessProfile):
        counts[model.__tablename__] = session.execute(
            delete(model).where(model.user_id == user_id)).rowcount
    return counts


def erase_account(session, *, user_id: int, identity_verified: bool,
                  retention_review_complete: bool, now: datetime) -> dict:
    """Erase only when no financial rows/active obligations require review.

    Trial ledger deletion requires a documented ended purpose; no blanket lifetime
    exception. Financial accounts use training erasure first and a separate lawful
    retention decision, never blind cascading tax-record deletion.
    """
    _guard(session, identity_verified and retention_review_complete)
    user = session.get(User, user_id)
    if user is None:
        raise PrivacyReviewRequired("Account not found.")
    for model, column in ((SubscriptionPayment, SubscriptionPayment.user_id),
                          (Payments, Payments.user)):
        if session.scalar(select(func.count()).select_from(model).where(column == user_id)):
            raise PrivacyReviewRequired("Financial retention review required.")
    access = session.get(UserAccess, user_id)
    if access and any(value is not None and value > now for value in
                      (access.trial_ends_at, access.subscription_ends_at)):
        raise PrivacyReviewRequired("Active access obligation review required.")
    counts = erase_training_data(session, user_id=user_id, identity_verified=True)
    session.execute(delete(WebsitePurchaseSession).where(
        WebsitePurchaseSession.telegram_user_id == user.tg_id))
    session.execute(delete(UserAccess).where(UserAccess.user_id == user_id))
    # inviter_id is Telegram ID, not FK; remove residual referral links too.
    session.execute(update(User).where(User.inviter_id == user.tg_id).values(inviter_id=0))
    session.execute(delete(User).where(User.id == user_id))
    counts["users"] = 1
    return counts


def export_non_special_account_data(session, *, user_id: int, identity_verified: bool) -> dict:
    """Return only to operator's secure delivery channel, never stdout/logs.

    No historical health text, auth material, signed checkout URLs or raw provider
    payload. Workout details are provided via the existing history interface.
    """
    if not identity_verified:
        raise PrivacyReviewRequired("Verified request required.")
    result = {}
    for model, key in ((User, User.id), (FitnessProfile, FitnessProfile.user_id),
                       (UserAccess, UserAccess.user_id),
                       (SubscriptionPayment, SubscriptionPayment.user_id)):
        columns = [c for c in model.__table__.columns
                   if c.name not in {"limitations", "confirmation_url", "idempotency_key"}]
        result[model.__tablename__] = [dict(row) for row in session.execute(
            select(*columns).where(key == user_id)).mappings()]
    return result


def _guard(session, approved: bool):
    if not approved or not session.in_transaction():
        raise PrivacyReviewRequired("Verified request and explicit transaction required.")
    if session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
        raise PrivacyReviewRequired("Foreign key enforcement required.")
