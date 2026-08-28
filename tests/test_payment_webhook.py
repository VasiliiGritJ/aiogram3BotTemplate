import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import SqliteSession, SubscriptionPayment, UserAccess
from services.payment_domain import PaymentStatus
from services.payment_provider import PaymentProviderUnavailable, ProviderPayment
from services.payment_service import PaymentService, SubscriptionProduct
from services.payment_webhook import (
    PaymentWebhookReason,
    YooKassaWebhookProcessor,
)
from tests.payment_fakes import FakePaymentProvider


BASE_TIME = datetime(2026, 8, 13, 12, 0, 0)
PRODUCT = SubscriptionProduct("monthly_30d_v1", 99000, "RUB", 30)
PROVIDER_ID = "provider-payment-1"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def notification(event: str = "payment.succeeded", **object_overrides):
    payment_object = {
        "id": PROVIDER_ID,
        # Deliberately untrusted fields: the processor must ignore all of them.
        "status": "succeeded",
        "amount": {"value": "0.01", "currency": "USD"},
    }
    payment_object.update(object_overrides)
    return {
        "type": "notification",
        "event": event,
        "object": payment_object,
    }


class PaymentWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temp_directory.name) / "webhooks.db")
        )
        run_migrations(self.database.engine)
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 7001, 'Webhook User', 'webhook_user', 0)
                """
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access (user_id, updated_at) VALUES (1, :now)",
                {"now": BASE_TIME},
            )
        self._key_number = 0

    def tearDown(self) -> None:
        self.database.dispose()
        self.temp_directory.cleanup()

    def provider_payment(self, status: PaymentStatus, **overrides) -> ProviderPayment:
        values = {
            "provider_payment_id": PROVIDER_ID,
            "status": status,
            "amount_minor": PRODUCT.amount_minor,
            "currency": PRODUCT.currency,
            "metadata": {
                "local_payment_id": "1",
                "product_code": PRODUCT.product_code,
            },
        }
        values.update(overrides)
        return ProviderPayment(**values)

    def service(self, provider: FakePaymentProvider) -> PaymentService:
        return PaymentService(
            provider,
            PRODUCT,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            idempotency_key_factory=self.next_key,
        )

    def next_key(self) -> str:
        self._key_number += 1
        return f"webhook-key-{self._key_number}"

    def create_pending(self, provider: FakePaymentProvider) -> PaymentService:
        service = self.service(provider)
        created = service.get_or_create_payment(1)
        self.assertEqual(PaymentStatus.PENDING, created.status)
        return service

    def access_and_payment(self):
        with self.database() as session:
            access = session.get(UserAccess, 1)
            payment = session.scalar(select(SubscriptionPayment))
            assert access is not None and payment is not None
            return access, payment

    def test_succeeded_notification_rechecks_provider_and_applies_once(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.create_pending(provider)
        provider.queue_get(PROVIDER_ID, self.provider_payment(PaymentStatus.SUCCEEDED))

        result = YooKassaWebhookProcessor(service).process(notification())
        access, payment = self.access_and_payment()

        self.assertEqual(PaymentWebhookReason.RECONCILED, result.reason)
        self.assertTrue(result.access_applied)
        self.assertEqual(BASE_TIME + timedelta(days=30), access.subscription_ends_at)
        self.assertIsNotNone(payment.access_applied_at)
        self.assertEqual([PROVIDER_ID], provider.get_calls)

    def test_duplicate_notification_never_extends_access_twice(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.create_pending(provider)
        succeeded = self.provider_payment(PaymentStatus.SUCCEEDED)
        provider.queue_get(PROVIDER_ID, succeeded, succeeded)
        processor = YooKassaWebhookProcessor(service)

        first = processor.process(notification())
        access_after_first, payment_after_first = self.access_and_payment()
        end_after_first = access_after_first.subscription_ends_at
        applied_after_first = payment_after_first.access_applied_at
        second = processor.process(notification())
        access_after_second, payment_after_second = self.access_and_payment()

        self.assertTrue(first.access_applied)
        self.assertFalse(second.access_applied)
        self.assertEqual(end_after_first, access_after_second.subscription_ends_at)
        self.assertEqual(applied_after_first, payment_after_second.access_applied_at)
        with self.database() as session:
            self.assertEqual(
                1, session.scalar(select(func.count(SubscriptionPayment.id)))
            )

    def test_payload_succeeded_cannot_override_pending_or_canceled_provider(self) -> None:
        for status in (PaymentStatus.PENDING, PaymentStatus.CANCELED):
            with self.subTest(authoritative_status=status):
                self.tearDown()
                self.setUp()
                provider = FakePaymentProvider(
                    [self.provider_payment(PaymentStatus.PENDING)]
                )
                service = self.create_pending(provider)
                provider.queue_get(PROVIDER_ID, self.provider_payment(status))

                result = YooKassaWebhookProcessor(service).process(notification())
                access, payment = self.access_and_payment()

                self.assertEqual(PaymentWebhookReason.RECONCILED, result.reason)
                self.assertFalse(result.access_applied)
                self.assertEqual(status.value, payment.status)
                self.assertIsNone(access.subscription_ends_at)

    def test_unknown_or_wrong_provider_payment_never_creates_or_grants(self) -> None:
        provider = FakePaymentProvider()
        processor = YooKassaWebhookProcessor(self.service(provider))

        result = processor.process(notification(id="unknown-provider-payment"))

        self.assertEqual(PaymentWebhookReason.PAYMENT_NOT_FOUND, result.reason)
        self.assertEqual([], provider.get_calls)
        with self.database() as session:
            self.assertEqual(
                0, session.scalar(select(func.count(SubscriptionPayment.id)))
            )
            access = session.get(UserAccess, 1)
            assert access is not None
            self.assertIsNone(access.subscription_ends_at)

    def test_malformed_and_unsupported_events_are_controlled_without_mutation(self) -> None:
        provider = FakePaymentProvider()
        processor = YooKassaWebhookProcessor(self.service(provider))
        malformed = (
            None,
            {},
            {"type": "notification", "event": "payment.succeeded"},
            notification(id=""),
            {**notification(), "type": "other"},
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                self.assertEqual(
                    PaymentWebhookReason.MALFORMED_PAYLOAD,
                    processor.process(payload).reason,
                )
        self.assertEqual(
            PaymentWebhookReason.UNSUPPORTED_EVENT,
            processor.process(notification("refund.succeeded")).reason,
        )
        self.assertEqual([], provider.get_calls)

    def test_all_supported_payment_events_reach_existing_payment_lookup(self) -> None:
        processor = YooKassaWebhookProcessor(self.service(FakePaymentProvider()))
        for event in (
            "payment.waiting_for_capture",
            "payment.succeeded",
            "payment.canceled",
        ):
            with self.subTest(event=event):
                self.assertEqual(
                    PaymentWebhookReason.PAYMENT_NOT_FOUND,
                    processor.process(notification(event)).reason,
                )

    def test_authoritative_provider_id_mismatch_is_rejected_without_access(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.create_pending(provider)
        provider.queue_get(
            PROVIDER_ID,
            self.provider_payment(
                PaymentStatus.SUCCEEDED,
                provider_payment_id="different-provider-payment",
            ),
        )

        result = YooKassaWebhookProcessor(service).process(notification())
        access, payment = self.access_and_payment()

        self.assertEqual(PaymentWebhookReason.RECONCILE_REJECTED, result.reason)
        self.assertFalse(result.access_applied)
        self.assertEqual(PaymentStatus.PENDING.value, payment.status)
        self.assertIsNone(access.subscription_ends_at)

    def test_provider_failure_fails_closed_and_manual_reconcile_still_works(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.PENDING)])
        service = self.create_pending(provider)
        provider.queue_get(PROVIDER_ID, PaymentProviderUnavailable("safe failure"))

        failed = YooKassaWebhookProcessor(service).process(notification())
        access, _ = self.access_and_payment()
        self.assertEqual(PaymentWebhookReason.PROVIDER_UNAVAILABLE, failed.reason)
        self.assertFalse(failed.access_applied)
        self.assertIsNone(access.subscription_ends_at)

        provider.queue_get(PROVIDER_ID, self.provider_payment(PaymentStatus.SUCCEEDED))
        manual = service.reconcile_payment(1, user_id=1)
        access, _ = self.access_and_payment()
        self.assertTrue(manual.access_applied)
        self.assertEqual(BASE_TIME + timedelta(days=30), access.subscription_ends_at)


if __name__ == "__main__":
    unittest.main()
