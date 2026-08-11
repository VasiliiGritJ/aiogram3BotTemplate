import unittest
from datetime import datetime, timedelta, timezone

from db.models import UserAccess
from services.access import AccessStatus, evaluate_access


BASE_TIME = datetime(2026, 8, 9, 12, 0, 0)


def make_access(
    *,
    trial_started_at: datetime = BASE_TIME,
    trial_ends_at: datetime = BASE_TIME + timedelta(days=3),
    subscription_started_at: datetime | None = None,
    subscription_ends_at: datetime | None = None,
) -> UserAccess:
    return UserAccess(
        user_id=1,
        trial_started_at=trial_started_at,
        trial_ends_at=trial_ends_at,
        subscription_started_at=subscription_started_at,
        subscription_ends_at=subscription_ends_at,
        updated_at=BASE_TIME,
    )


class AccessServiceTests(unittest.TestCase):
    def test_trial_is_active_before_end_boundary(self) -> None:
        access = make_access()

        decision = evaluate_access(
            access,
            BASE_TIME + timedelta(days=3) - timedelta(microseconds=1),
        )

        self.assertEqual(AccessStatus.TRIAL, decision.status)
        self.assertTrue(decision.has_access)
        self.assertEqual(access.trial_ends_at, decision.ends_at)

    def test_trial_is_expired_exactly_at_end_boundary(self) -> None:
        decision = evaluate_access(
            make_access(),
            BASE_TIME + timedelta(days=3),
        )

        self.assertEqual(AccessStatus.EXPIRED, decision.status)
        self.assertFalse(decision.has_access)

    def test_trial_is_expired_after_end_boundary(self) -> None:
        decision = evaluate_access(
            make_access(),
            BASE_TIME + timedelta(days=4),
        )

        self.assertEqual(AccessStatus.EXPIRED, decision.status)

    def test_active_subscription_has_priority_over_trial(self) -> None:
        subscription_end = BASE_TIME + timedelta(days=30)
        access = make_access(
            subscription_started_at=BASE_TIME - timedelta(days=1),
            subscription_ends_at=subscription_end,
        )

        decision = evaluate_access(access, BASE_TIME + timedelta(days=1))

        self.assertEqual(AccessStatus.ACTIVE, decision.status)
        self.assertEqual(subscription_end, decision.ends_at)

    def test_expired_subscription_does_not_grant_active_access(self) -> None:
        access = make_access(
            trial_ends_at=BASE_TIME - timedelta(days=1),
            subscription_started_at=BASE_TIME - timedelta(days=31),
            subscription_ends_at=BASE_TIME,
        )

        decision = evaluate_access(access, BASE_TIME)

        self.assertEqual(AccessStatus.EXPIRED, decision.status)
        self.assertFalse(decision.has_access)

    def test_expired_paid_history_does_not_restore_trial_available(self) -> None:
        access = make_access(
            trial_started_at=None,
            trial_ends_at=None,
            subscription_started_at=BASE_TIME - timedelta(days=31),
            subscription_ends_at=BASE_TIME - timedelta(days=1),
        )

        decision = evaluate_access(access, BASE_TIME)

        self.assertEqual(AccessStatus.EXPIRED, decision.status)

    def test_missing_access_is_expired(self) -> None:
        decision = evaluate_access(None, BASE_TIME)

        self.assertEqual(AccessStatus.EXPIRED, decision.status)
        self.assertIsNone(decision.ends_at)

    def test_aware_datetime_is_normalized_to_utc(self) -> None:
        aware_now = datetime(2026, 8, 9, 15, 0, tzinfo=timezone(timedelta(hours=3)))

        decision = evaluate_access(make_access(), aware_now)

        self.assertEqual(AccessStatus.TRIAL, decision.status)


if __name__ == "__main__":
    unittest.main()
