import unittest
from datetime import datetime, timedelta, timezone

from services.payment_domain import (
    GrantBase,
    PaymentReasonCode,
    PaymentSpec,
    PaymentState,
    PaymentStatus,
    calculate_subscription_grant,
    get_access_eligibility,
    get_creating_recovery,
    transition_payment_status,
    validate_payment_spec,
)


BASE_TIME = datetime(2026, 8, 11, 12, 0, 0)
VALID_SPEC = PaymentSpec(
    product_code="monthly_30d_v1",
    amount_minor=99000,
    currency="RUB",
    period_days=30,
)


class PaymentDomainTests(unittest.TestCase):
    def make_payment(
        self,
        status: PaymentStatus = PaymentStatus.CREATING,
        *,
        spec: PaymentSpec = VALID_SPEC,
        access_applied_at: datetime | None = None,
    ) -> PaymentState:
        return PaymentState(
            status=status,
            spec=spec,
            created_at=BASE_TIME,
            access_applied_at=access_applied_at,
        )

    def test_valid_spec_is_accepted(self) -> None:
        result = validate_payment_spec(VALID_SPEC)

        self.assertTrue(result.is_valid)
        self.assertEqual(PaymentReasonCode.VALID, result.reason)

    def test_invalid_amount_is_rejected(self) -> None:
        for amount in (0, -1, True, "99000"):
            with self.subTest(amount=amount):
                result = validate_payment_spec(
                    PaymentSpec("monthly_30d_v1", amount, "RUB", 30)
                )
                self.assertFalse(result.is_valid)
                self.assertEqual(PaymentReasonCode.INVALID_AMOUNT, result.reason)

    def test_invalid_currency_product_and_period_are_rejected(self) -> None:
        cases = (
            (PaymentSpec("monthly_30d_v1", 1, "rub", 30), PaymentReasonCode.INVALID_CURRENCY),
            (PaymentSpec("", 1, "RUB", 30), PaymentReasonCode.INVALID_PRODUCT),
            (PaymentSpec("monthly_30d_v1", 1, "RUB", 0), PaymentReasonCode.INVALID_PERIOD),
        )

        for spec, reason in cases:
            with self.subTest(reason=reason):
                result = validate_payment_spec(spec)
                self.assertFalse(result.is_valid)
                self.assertEqual(reason, result.reason)

    def test_non_terminal_transitions_follow_state_machine(self) -> None:
        transition = transition_payment_status(
            PaymentStatus.CREATING, PaymentStatus.PENDING
        )

        self.assertTrue(transition.accepted)
        self.assertEqual(PaymentStatus.PENDING, transition.status)
        self.assertEqual(PaymentReasonCode.STATUS_CHANGED, transition.reason)

        invalid = transition_payment_status(
            PaymentStatus.PENDING, PaymentStatus.CREATING
        )
        self.assertFalse(invalid.accepted)
        self.assertEqual(PaymentReasonCode.INVALID_STATUS_TRANSITION, invalid.reason)

    def test_terminal_statuses_are_monotonic(self) -> None:
        for status in (
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CANCELED,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        ):
            with self.subTest(status=status):
                repeated = transition_payment_status(status, status)
                downgrade = transition_payment_status(status, PaymentStatus.PENDING)
                self.assertTrue(repeated.accepted)
                self.assertEqual(PaymentReasonCode.STATUS_ALREADY_CURRENT, repeated.reason)
                self.assertFalse(downgrade.accepted)
                self.assertEqual(
                    PaymentReasonCode.TERMINAL_STATUS_IMMUTABLE,
                    downgrade.reason,
                )

    def test_only_unapplied_succeeded_payment_is_eligible(self) -> None:
        eligible = get_access_eligibility(
            self.make_payment(PaymentStatus.SUCCEEDED)
        )
        pending = get_access_eligibility(self.make_payment(PaymentStatus.PENDING))
        repeated = get_access_eligibility(
            self.make_payment(
                PaymentStatus.SUCCEEDED,
                access_applied_at=BASE_TIME,
            )
        )

        self.assertTrue(eligible.can_apply)
        self.assertEqual(PaymentReasonCode.VALID, eligible.reason)
        self.assertFalse(pending.can_apply)
        self.assertEqual(PaymentReasonCode.NOT_SUCCEEDED, pending.reason)
        self.assertFalse(repeated.can_apply)
        self.assertEqual(PaymentReasonCode.ACCESS_ALREADY_APPLIED, repeated.reason)

    def test_invalid_spec_blocks_access_even_when_succeeded(self) -> None:
        result = get_access_eligibility(
            self.make_payment(
                PaymentStatus.SUCCEEDED,
                spec=PaymentSpec("monthly_30d_v1", 0, "RUB", 30),
            )
        )

        self.assertFalse(result.can_apply)
        self.assertEqual(PaymentReasonCode.INVALID_AMOUNT, result.reason)

    def test_creating_recovery_only_retries_within_supplied_window(self) -> None:
        payment = self.make_payment()
        window = timedelta(hours=24)

        retry = get_creating_recovery(payment, BASE_TIME + window, window)
        unknown = get_creating_recovery(
            payment,
            BASE_TIME + window + timedelta(microseconds=1),
            window,
        )

        self.assertTrue(retry.retry_same_key)
        self.assertEqual(PaymentReasonCode.RETRY_SAME_KEY, retry.reason)
        self.assertFalse(unknown.retry_same_key)
        self.assertEqual(
            PaymentReasonCode.UNKNOWN_OUTCOME_REQUIRES_RECONCILIATION,
            unknown.reason,
        )

    def test_recovery_does_not_retry_non_creating_payment(self) -> None:
        result = get_creating_recovery(
            self.make_payment(PaymentStatus.PENDING),
            BASE_TIME,
            timedelta(hours=1),
        )

        self.assertFalse(result.retry_same_key)
        self.assertEqual(PaymentReasonCode.NOT_CREATING, result.reason)

    def test_grant_uses_now_without_active_access(self) -> None:
        grant = calculate_subscription_grant(now=BASE_TIME, period_days=30)

        self.assertEqual(GrantBase.NOW, grant.base)
        self.assertEqual(BASE_TIME, grant.started_at)
        self.assertEqual(BASE_TIME + timedelta(days=30), grant.ends_at)

    def test_grant_preserves_active_trial_before_paid_period(self) -> None:
        trial_end = BASE_TIME + timedelta(days=2)
        grant = calculate_subscription_grant(
            now=BASE_TIME,
            period_days=30,
            trial_ends_at=trial_end,
        )

        self.assertEqual(GrantBase.ACTIVE_TRIAL, grant.base)
        self.assertEqual(trial_end, grant.started_at)
        self.assertEqual(trial_end + timedelta(days=30), grant.ends_at)

    def test_active_subscription_has_priority_over_trial(self) -> None:
        trial_end = BASE_TIME + timedelta(days=2)
        subscription_end = BASE_TIME + timedelta(days=12)
        grant = calculate_subscription_grant(
            now=BASE_TIME,
            period_days=30,
            trial_ends_at=trial_end,
            subscription_ends_at=subscription_end,
        )

        self.assertEqual(GrantBase.ACTIVE_SUBSCRIPTION, grant.base)
        self.assertEqual(subscription_end, grant.started_at)
        self.assertEqual(subscription_end + timedelta(days=30), grant.ends_at)

    def test_expired_dates_do_not_extend_paid_period(self) -> None:
        grant = calculate_subscription_grant(
            now=BASE_TIME,
            period_days=30,
            trial_ends_at=BASE_TIME,
            subscription_ends_at=BASE_TIME - timedelta(microseconds=1),
        )

        self.assertEqual(GrantBase.NOW, grant.base)
        self.assertEqual(BASE_TIME, grant.started_at)

    def test_aware_datetime_boundaries_are_deterministic_utc(self) -> None:
        now = datetime(2026, 8, 11, 15, 0, tzinfo=timezone(timedelta(hours=3)))
        trial_end = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)

        grant = calculate_subscription_grant(
            now=now,
            period_days=30,
            trial_ends_at=trial_end,
        )

        self.assertEqual(GrantBase.ACTIVE_TRIAL, grant.base)
        self.assertEqual(datetime(2026, 8, 11, 13, 0), grant.started_at)


if __name__ == "__main__":
    unittest.main()
