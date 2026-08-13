import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import SqliteSession, SubscriptionPayment, UserAccess
from services.access import AccessStatus, activate_trial_once, evaluate_access
from services.payment_domain import PaymentStatus
from services.payment_provider import (
    PaymentProviderPermanentError,
    PaymentProviderUnavailable,
    ProviderPayment,
)
from services.payment_service import (
    PaymentService,
    PaymentServiceReason,
    SubscriptionProduct,
)
from tests.payment_fakes import FakePaymentProvider


BASE_TIME = datetime(2026, 8, 11, 12, 0, 0)
PRODUCT = SubscriptionProduct("monthly_30d_v1", 99000, "RUB", 30)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class PaymentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temp_directory.name) / "payments.db")
        )
        self._key_number = 0
        self.assertEqual(
            (1, 2, 3, 4, 5, 6, 7, 8),
            run_migrations(self.database.engine),
        )
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 5001, 'Payment User', 'payment_user', 0)
                """
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access (user_id, updated_at) VALUES (1, :now)",
                {"now": BASE_TIME},
            )

    def add_user(self, user_id: int) -> None:
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (:id, :tg_id, 'Payment User', 'payment_user', 0)
                """,
                {"id": user_id, "tg_id": 5000 + user_id},
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access (user_id, updated_at) VALUES (:id, :now)",
                {"id": user_id, "now": BASE_TIME},
            )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    def provider_payment(
        self,
        status: PaymentStatus,
        *,
        provider_id: str = "provider-1",
        amount_minor: int = PRODUCT.amount_minor,
        currency: str = PRODUCT.currency,
        local_payment_id: int = 1,
    ) -> ProviderPayment:
        return ProviderPayment(
            provider_payment_id=provider_id,
            status=status,
            amount_minor=amount_minor,
            currency=currency,
            confirmation_url="https://example.invalid/confirmation",
            metadata={
                "local_payment_id": str(local_payment_id),
                "product_code": PRODUCT.product_code,
            },
        )

    def service(
        self,
        provider: FakePaymentProvider,
        *,
        now: datetime = BASE_TIME,
        before_access_apply=None,
        expected_test_mode: bool | None = None,
    ) -> PaymentService:
        return PaymentService(
            provider,
            PRODUCT,
            session_factory=self.database,
            now_factory=lambda: now,
            idempotency_key_factory=self.next_idempotency_key,
            before_access_apply=before_access_apply,
            expected_test_mode=expected_test_mode,
        )

    def next_idempotency_key(self) -> str:
        self._key_number += 1
        return f"test-key-{self._key_number}"

    def payment_and_access(self) -> tuple[SubscriptionPayment, UserAccess]:
        with self.database() as session:
            payment = session.scalar(select(SubscriptionPayment))
            access = session.get(UserAccess, 1)
            assert payment is not None and access is not None
            return payment, access

    def test_first_click_creates_pending_payment_and_second_click_reuses_it(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.service(provider)

        first = service.get_or_create_payment(1)
        second = service.get_or_create_payment(1)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.payment_id, second.payment_id)
        self.assertEqual(PaymentStatus.PENDING, second.status)
        self.assertEqual(1, len(provider.create_calls))
        self.assertEqual(1, len(provider.get_calls))

    def test_succeeded_payment_applies_exactly_one_30_day_period(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        service = self.service(provider)

        first = service.get_or_create_payment(1)
        repeated = service.reconcile_payment(first.payment_id, user_id=1)
        payment, access = self.payment_and_access()

        self.assertTrue(first.access_applied)
        self.assertFalse(repeated.access_applied)
        self.assertEqual(PaymentServiceReason.ACCESS_ALREADY_APPLIED, repeated.reason)
        self.assertEqual(BASE_TIME, access.subscription_started_at)
        self.assertEqual(BASE_TIME + timedelta(days=30), access.subscription_ends_at)
        self.assertIsNotNone(payment.access_applied_at)
        with self.database() as session:
            self.assertEqual(1, session.scalar(select(func.count(SubscriptionPayment.id))))

    def test_active_trial_is_preserved_before_paid_period(self) -> None:
        activate_trial_once(1, BASE_TIME, self.database)
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])

        result = self.service(provider).get_or_create_payment(1)
        _, access = self.payment_and_access()

        self.assertTrue(result.access_applied)
        self.assertEqual(BASE_TIME + timedelta(days=3), access.subscription_started_at)
        self.assertEqual(BASE_TIME + timedelta(days=33), access.subscription_ends_at)
        self.assertEqual(AccessStatus.TRIAL, evaluate_access(access, BASE_TIME).status)

    def test_active_paid_access_is_extended_from_existing_end(self) -> None:
        first_provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        self.service(first_provider).get_or_create_payment(1)
        second_provider = FakePaymentProvider(
            [self.provider_payment(PaymentStatus.SUCCEEDED, provider_id="provider-2", local_payment_id=2)]
        )

        second = self.service(second_provider).get_or_create_payment(1)
        _, access = self.payment_and_access()

        self.assertTrue(second.access_applied)
        self.assertEqual(BASE_TIME + timedelta(days=60), access.subscription_ends_at)

    def test_trial_available_and_expired_access_start_paid_period_now(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        self.service(provider).get_or_create_payment(1)
        _, access = self.payment_and_access()
        self.assertEqual(BASE_TIME, access.subscription_started_at)

        with self.database() as session:
            with session.begin():
                access = session.get(UserAccess, 1)
                assert access is not None
                access.subscription_started_at = BASE_TIME - timedelta(days=31)
                access.subscription_ends_at = BASE_TIME - timedelta(days=1)
        provider = FakePaymentProvider(
            [self.provider_payment(PaymentStatus.SUCCEEDED, provider_id="provider-2", local_payment_id=2)]
        )
        self.service(provider).get_or_create_payment(1)
        _, expired_access = self.payment_and_access()
        self.assertEqual(BASE_TIME, expired_access.subscription_started_at)

    def test_paid_history_never_restarts_trial(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        self.service(provider).get_or_create_payment(1)
        with self.database() as session:
            with session.begin():
                access = session.get(UserAccess, 1)
                assert access is not None
                access.subscription_started_at = BASE_TIME - timedelta(days=31)
                access.subscription_ends_at = BASE_TIME - timedelta(days=1)

        _, access = self.payment_and_access()
        self.assertEqual(AccessStatus.EXPIRED, evaluate_access(access, BASE_TIME).status)
        activation = activate_trial_once(1, BASE_TIME, self.database)
        self.assertFalse(activation.activated)
        self.assertIsNone(access.trial_started_at)

    def test_all_non_succeeded_statuses_never_apply_access(self) -> None:
        for index, status in enumerate((
            PaymentStatus.CREATING,
            PaymentStatus.PENDING,
            PaymentStatus.WAITING_FOR_CAPTURE,
            PaymentStatus.CANCELED,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        ), start=1):
            with self.subTest(status=status):
                user_id = 10 + index
                self.add_user(user_id)
                provider = FakePaymentProvider(
                    [
                        self.provider_payment(
                            status,
                            provider_id=f"provider-{index}",
                            local_payment_id=index,
                        )
                    ]
                )
                result = self.service(provider).get_or_create_payment(user_id)
                with self.database() as session:
                    access = session.get(UserAccess, user_id)
                    assert access is not None
                self.assertFalse(result.access_applied)
                self.assertEqual(status, result.status)
                self.assertIsNone(access.subscription_ends_at)

    def test_timeout_keeps_creating_and_retry_reuses_the_same_key(self) -> None:
        provider = FakePaymentProvider(
            [
                PaymentProviderUnavailable("timeout"),
                self.provider_payment(PaymentStatus.PENDING),
            ]
        )
        service = self.service(provider)

        timed_out = service.get_or_create_payment(1)
        retried = service.get_or_create_payment(1)
        payment, _ = self.payment_and_access()

        self.assertEqual(PaymentServiceReason.PROVIDER_UNAVAILABLE, timed_out.reason)
        self.assertEqual(PaymentStatus.PENDING, retried.status)
        self.assertEqual("test-key-1", payment.idempotency_key)
        self.assertEqual(2, len(provider.create_calls))

    def test_old_unresolved_creating_payment_is_not_blindly_retried(self) -> None:
        provider = FakePaymentProvider([PaymentProviderUnavailable("timeout")])
        self.service(provider).get_or_create_payment(1)

        later_service = self.service(
            provider,
            now=BASE_TIME + timedelta(hours=24, microseconds=1),
        )
        result = later_service.get_or_create_payment(1)

        self.assertEqual(PaymentServiceReason.RECOVERY_REQUIRED, result.reason)
        self.assertEqual(1, len(provider.create_calls))

    def test_restart_reuses_persisted_provider_payment_without_new_create(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        first_service = self.service(provider)
        first = first_service.get_or_create_payment(1)

        restarted_service = self.service(provider)
        resumed = restarted_service.get_or_create_payment(1)

        self.assertEqual(first.payment_id, resumed.payment_id)
        self.assertEqual(1, len(provider.create_calls))
        self.assertEqual(1, len(provider.get_calls))

    def test_provider_get_failure_and_ownership_mismatch_are_controlled(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.service(provider)
        result = service.get_or_create_payment(1)
        provider.queue_get("provider-1", PaymentProviderUnavailable("unavailable"))

        unavailable = service.reconcile_payment(result.payment_id, user_id=1)
        self.assertEqual(PaymentServiceReason.PROVIDER_UNAVAILABLE, unavailable.reason)
        with self.assertRaises(PermissionError):
            service.reconcile_payment(result.payment_id, user_id=999)

    def test_provider_mismatches_block_access(self) -> None:
        cases = (
            self.provider_payment(PaymentStatus.SUCCEEDED, amount_minor=1),
            self.provider_payment(PaymentStatus.SUCCEEDED, currency="USD"),
            ProviderPayment(
                "provider-1", PaymentStatus.SUCCEEDED, PRODUCT.amount_minor, "RUB",
                metadata={"local_payment_id": "999", "product_code": PRODUCT.product_code},
            ),
        )
        for index, provider_payment in enumerate(cases, start=1):
            with self.subTest(provider_payment=provider_payment):
                user_id = 30 + index
                self.add_user(user_id)
                result = self.service(FakePaymentProvider([provider_payment])).get_or_create_payment(user_id)
                with self.database() as session:
                    access = session.get(UserAccess, user_id)
                    assert access is not None
                self.assertFalse(result.access_applied)
                self.assertIsNone(access.subscription_ends_at)

    def test_expected_payment_mode_rejects_missing_or_mismatched_flag(self) -> None:
        cases = ((True, None), (True, False), (False, None), (False, True))
        for index, (expected_test_mode, test_flag) in enumerate(cases, start=1):
            with self.subTest(
                expected_test_mode=expected_test_mode,
                test_flag=test_flag,
            ):
                user_id = 50 + index
                self.add_user(user_id)
                provider = FakePaymentProvider(
                    [
                        ProviderPayment(
                            provider_payment_id=f"test-mode-{index}",
                            status=PaymentStatus.SUCCEEDED,
                            amount_minor=PRODUCT.amount_minor,
                            currency=PRODUCT.currency,
                            metadata={
                                "local_payment_id": str(index),
                                "product_code": PRODUCT.product_code,
                            },
                            is_test=test_flag,
                        )
                    ]
                )
                result = self.service(
                    provider, expected_test_mode=expected_test_mode
                ).get_or_create_payment(user_id)
                with self.database() as session:
                    access = session.get(UserAccess, user_id)
                    assert access is not None
                self.assertEqual(
                    PaymentServiceReason.PAYMENT_MODE_REJECTED, result.reason
                )
                self.assertFalse(result.access_applied)
                self.assertIsNone(access.subscription_ends_at)

    def test_test_mode_accepts_true_flag_and_preserves_duplicate_idempotency(self) -> None:
        provider = FakePaymentProvider(
            [
                ProviderPayment(
                    provider_payment_id="test-mode-true",
                    status=PaymentStatus.SUCCEEDED,
                    amount_minor=PRODUCT.amount_minor,
                    currency=PRODUCT.currency,
                    metadata={
                        "local_payment_id": "1",
                        "product_code": PRODUCT.product_code,
                    },
                    is_test=True,
                )
            ]
        )
        service = self.service(provider, expected_test_mode=True)

        first = service.get_or_create_payment(1)
        repeated = service.reconcile_payment(first.payment_id, user_id=1)
        _, access = self.payment_and_access()

        self.assertTrue(first.access_applied)
        self.assertFalse(repeated.access_applied)
        self.assertIsNotNone(access.subscription_ends_at)

    def test_creation_guard_blocks_before_a_local_payment_is_created(self) -> None:
        class BlockedTestProvider(FakePaymentProvider):
            def ensure_payment_creation_allowed(self) -> None:
                raise PaymentProviderPermanentError("test shop verification failed")

        provider = BlockedTestProvider()
        with self.assertRaises(PaymentProviderPermanentError):
            self.service(provider).get_or_create_payment(1)

        with self.database() as session:
            payment_count = session.scalar(select(func.count(SubscriptionPayment.id)))
        self.assertEqual(0, payment_count)
        self.assertEqual([], provider.create_calls)

    def test_reused_provider_id_for_another_local_payment_is_rejected(self) -> None:
        first = self.service(
            FakePaymentProvider([self.provider_payment(PaymentStatus.CANCELED)])
        ).get_or_create_payment(1)
        self.assertEqual(PaymentStatus.CANCELED, first.status)
        provider = FakePaymentProvider(
            [
                self.provider_payment(
                    PaymentStatus.SUCCEEDED,
                    local_payment_id=2,
                )
            ]
        )

        result = self.service(provider).get_or_create_payment(1)
        _, access = self.payment_and_access()

        self.assertEqual(PaymentServiceReason.PROVIDER_ID_MISMATCH, result.reason)
        self.assertFalse(result.access_applied)
        self.assertIsNone(access.subscription_ends_at)

    def test_terminal_status_is_not_downgraded_by_late_provider_response(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        service = self.service(provider)
        result = service.get_or_create_payment(1)
        provider.queue_get("provider-1", self.provider_payment(PaymentStatus.PENDING))

        late = service.reconcile_payment(result.payment_id, user_id=1)
        payment, _ = self.payment_and_access()
        self.assertEqual(PaymentStatus.SUCCEEDED, payment.status)
        self.assertEqual(PaymentServiceReason.ACCESS_ALREADY_APPLIED, late.reason)

    def test_transaction_rollback_leaves_payment_unapplied_for_safe_retry(self) -> None:
        failures = [True]

        def fail_once() -> None:
            if failures and failures.pop():
                raise RuntimeError("test rollback")

        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        service = self.service(provider, before_access_apply=fail_once)
        with self.assertRaises(RuntimeError):
            service.get_or_create_payment(1)
        payment, access = self.payment_and_access()
        self.assertEqual(PaymentStatus.CREATING, payment.status)
        self.assertIsNone(payment.access_applied_at)
        self.assertIsNone(access.subscription_ends_at)

        retried = service.get_or_create_payment(1)
        payment, access = self.payment_and_access()
        self.assertTrue(retried.access_applied)
        self.assertEqual(PaymentStatus.SUCCEEDED, payment.status)
        self.assertIsNotNone(access.subscription_ends_at)


if __name__ == "__main__":
    unittest.main()
