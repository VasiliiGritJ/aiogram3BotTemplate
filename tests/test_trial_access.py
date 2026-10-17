"""Durable, explicit and payment-free launch-trial tests."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import (
    PaymentReceipt,
    SqliteSession,
    SubscriptionPayment,
    User,
    UserAccess,
)
from services.access import (
    AccessEntitlement,
    AccessStatus,
    activate_trial_once,
    get_access_decision,
)
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT


BASE_TIME = datetime(2026, 9, 1, 12, 0, 0)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class TrialAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "trial.db")
        )
        run_migrations(self.database.engine)
        with self.database() as session:
            user = User(
                tg_id=99001,
                fullname="Trial User",
                username="trial_user",
                inviter_id=0,
            )
            session.add(user)
            session.flush()
            self.user_id = user.id
            session.add(UserAccess(user_id=user.id, updated_at=BASE_TIME))
            session.commit()

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    def test_explicit_activation_is_exactly_72_hours_and_durable(self) -> None:
        before = get_access_decision(self.user_id, BASE_TIME, self.database)
        activation = activate_trial_once(self.user_id, BASE_TIME, self.database)
        after = get_access_decision(self.user_id, BASE_TIME, self.database)

        self.assertEqual(AccessStatus.TRIAL_AVAILABLE, before.status)
        self.assertTrue(activation.activated)
        self.assertEqual(BASE_TIME, activation.access.trial_consumed_at)
        self.assertEqual(
            BASE_TIME + LAUNCH_COMMERCIAL_PRODUCT.trial_duration,
            activation.access.trial_ends_at,
        )
        self.assertEqual(AccessStatus.TRIAL, after.status)
        self.assertEqual(AccessEntitlement.ACTIVE_TRIAL, after.entitlement)

    def test_repeated_and_expired_activation_never_extend_or_restart(self) -> None:
        first = activate_trial_once(self.user_id, BASE_TIME, self.database)
        repeated = activate_trial_once(
            self.user_id,
            BASE_TIME + timedelta(hours=1),
            self.database,
        )
        expired = activate_trial_once(
            self.user_id,
            BASE_TIME + timedelta(days=4),
            self.database,
        )

        self.assertTrue(first.activated)
        self.assertFalse(repeated.activated)
        self.assertFalse(expired.activated)
        self.assertEqual(first.access.trial_started_at, expired.access.trial_started_at)
        self.assertEqual(first.access.trial_ends_at, expired.access.trial_ends_at)
        decision = get_access_decision(
            self.user_id,
            BASE_TIME + timedelta(days=4),
            self.database,
        )
        self.assertEqual(AccessEntitlement.EXPIRED_TRIAL, decision.entitlement)

    def test_concurrent_activation_claims_the_single_lifetime_trial_once(self) -> None:
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def activate() -> None:
            try:
                barrier.wait(timeout=2)
                results.append(
                    activate_trial_once(self.user_id, BASE_TIME, self.database)
                )
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [threading.Thread(target=activate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(result.activated for result in results))
        with self.database() as session:
            access_rows = session.scalar(select(func.count(UserAccess.user_id)))
            access = session.get(UserAccess, self.user_id)
        self.assertEqual(1, access_rows)
        self.assertEqual(BASE_TIME, access.trial_started_at)
        self.assertEqual(BASE_TIME + timedelta(hours=72), access.trial_ends_at)

    def test_paid_history_blocks_trial_and_trial_creates_no_financial_rows(self) -> None:
        with self.database() as session:
            access = session.get(UserAccess, self.user_id)
            access.subscription_started_at = BASE_TIME - timedelta(days=31)
            access.subscription_ends_at = BASE_TIME - timedelta(days=1)
            session.commit()

        activation = activate_trial_once(self.user_id, BASE_TIME, self.database)

        self.assertFalse(activation.activated)
        with self.database() as session:
            payment_rows = session.scalar(
                select(func.count(SubscriptionPayment.id))
            )
            receipt_rows = session.scalar(select(func.count(PaymentReceipt.id)))
            access = session.get(UserAccess, self.user_id)
        self.assertEqual(0, payment_rows)
        self.assertEqual(0, receipt_rows)
        self.assertIsNone(access.trial_started_at)
        self.assertIsNone(access.trial_consumed_at)

    def test_trial_activation_does_not_require_profile_or_receipt_email(self) -> None:
        activation = activate_trial_once(self.user_id, BASE_TIME, self.database)

        self.assertTrue(activation.activated)
        with self.database() as session:
            self.assertEqual(
                0,
                session.scalar(select(func.count(SubscriptionPayment.id))),
            )
            self.assertEqual(
                0,
                session.scalar(select(func.count(PaymentReceipt.id))),
            )


if __name__ == "__main__":
    unittest.main()
