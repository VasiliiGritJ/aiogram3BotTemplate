import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import PaymentReceipt, SqliteSession, SubscriptionPayment, UserAccess
from services.payment_domain import PaymentStatus
from services.payment_provider import ProviderPayment
from services.payment_service import PaymentService, SubscriptionProduct
from services.payment_webhook import YooKassaWebhookProcessor
from services.receipt_domain import (
    NPD_RECEIPT_DESCRIPTION,
    ReceiptProcessingReason,
    ReceiptProviderOutcome,
    ReceiptRetryPolicy,
    ReceiptStatus,
)
from services.receipt_provider import ReceiptProviderResult
from services.receipt_service import ReceiptOutboxService
from tests.payment_fakes import FakePaymentProvider
from tests.receipt_fakes import FakeReceiptProvider


BASE_TIME = datetime(2026, 8, 29, 12, 0, 0)
PRODUCT = SubscriptionProduct("monthly_30d_v1", 49_900, "RUB", 30)
PROVIDER_PAYMENT_ID = "provider-payment-receipt-1"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ReceiptOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "receipt-outbox.db")
        )
        run_migrations(self.database.engine)
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                "VALUES (1, 8801, 'Receipt User', 'receipt_user', 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access(user_id, updated_at) VALUES (1, :now)",
                {"now": BASE_TIME},
            )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    @staticmethod
    def provider_payment(status: PaymentStatus) -> ProviderPayment:
        return ProviderPayment(
            provider_payment_id=PROVIDER_PAYMENT_ID,
            status=status,
            amount_minor=PRODUCT.amount_minor,
            currency=PRODUCT.currency,
            metadata={
                "local_payment_id": "1",
                "product_code": PRODUCT.product_code,
            },
        )

    def payment_service(self, provider: FakePaymentProvider) -> PaymentService:
        return PaymentService(
            provider,
            PRODUCT,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            idempotency_key_factory=lambda: "payment-key-for-receipt",
        )

    def create_succeeded_payment(self) -> tuple[PaymentService, FakePaymentProvider]:
        provider = FakePaymentProvider(
            [self.provider_payment(PaymentStatus.SUCCEEDED)]
        )
        service = self.payment_service(provider)
        result = service.get_or_create_payment(1)
        self.assertTrue(result.access_applied)
        return service, provider

    def receipt(self) -> PaymentReceipt:
        with self.database() as session:
            receipt = session.scalar(select(PaymentReceipt))
            assert receipt is not None
            session.expunge(receipt)
            return receipt

    def test_succeeded_payment_snapshots_one_499_rub_receipt_intent(self) -> None:
        self.create_succeeded_payment()
        receipt = self.receipt()

        self.assertEqual(49_900, receipt.amount_minor)
        self.assertEqual("RUB", receipt.currency)
        self.assertEqual(30, receipt.service_period_days)
        self.assertEqual(NPD_RECEIPT_DESCRIPTION, receipt.item_description)
        self.assertNotIn(PRODUCT.product_code, receipt.item_description)
        self.assertEqual(PROVIDER_PAYMENT_ID, receipt.payment_reference)
        self.assertEqual(ReceiptStatus.PENDING.value, receipt.status)

    def test_duplicate_manual_reconcile_and_webhook_keep_one_receipt_and_grant(self) -> None:
        service, provider = self.create_succeeded_payment()
        with self.database() as session:
            access_before = session.get(UserAccess, 1).subscription_ends_at
        manual = service.reconcile_payment(1, user_id=1)
        webhook = YooKassaWebhookProcessor(service).process(
            {
                "type": "notification",
                "event": "payment.succeeded",
                "object": {"id": PROVIDER_PAYMENT_ID},
            }
        )
        with self.database() as session:
            receipt_count = session.scalar(select(func.count(PaymentReceipt.id)))
            access_after = session.get(UserAccess, 1).subscription_ends_at

        self.assertFalse(manual.access_applied)
        self.assertFalse(webhook.access_applied)
        self.assertEqual(1, receipt_count)
        self.assertEqual(access_before, access_after)
        self.assertEqual(2, len(provider.get_calls))

    def test_successful_worker_stores_provider_id_once(self) -> None:
        self.create_succeeded_payment()
        provider = FakeReceiptProvider(
            [
                ReceiptProviderResult(
                    ReceiptProviderOutcome.SUCCEEDED,
                    provider_receipt_id="receipt-provider-1",
                )
            ]
        )
        service = ReceiptOutboxService(
            provider,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
        )

        first = service.process_one()
        second = service.process_one()
        receipt = self.receipt()

        self.assertEqual(ReceiptProcessingReason.SUCCEEDED, first.reason)
        self.assertEqual(ReceiptProcessingReason.NOTHING_DUE, second.reason)
        self.assertEqual(ReceiptStatus.SUCCEEDED.value, receipt.status)
        self.assertEqual("receipt-provider-1", receipt.provider_receipt_id)
        self.assertEqual(1, len(provider.register_calls))

    def test_retryable_failures_are_bounded_and_keep_same_idempotency_key(self) -> None:
        self.create_succeeded_payment()
        retry = ReceiptProviderResult(
            ReceiptProviderOutcome.RETRYABLE_FAILURE,
            error_code="temporary_unavailable",
        )
        provider = FakeReceiptProvider([retry, retry, retry])
        clock = [BASE_TIME]
        service = ReceiptOutboxService(
            provider,
            session_factory=self.database,
            now_factory=lambda: clock[0],
            retry_policy=ReceiptRetryPolicy(
                delays=(timedelta(seconds=1), timedelta(seconds=2))
            ),
        )

        reasons = []
        for _ in range(3):
            result = service.process_one()
            reasons.append(result.reason)
            receipt = self.receipt()
            if receipt.next_attempt_at is not None:
                clock[0] = receipt.next_attempt_at
        after_exhaustion = service.process_one()

        self.assertEqual(
            [
                ReceiptProcessingReason.RETRY_SCHEDULED,
                ReceiptProcessingReason.RETRY_SCHEDULED,
                ReceiptProcessingReason.RETRIES_EXHAUSTED,
            ],
            reasons,
        )
        self.assertEqual(ReceiptProcessingReason.NOTHING_DUE, after_exhaustion.reason)
        self.assertEqual(ReceiptStatus.PERMANENT_FAILED.value, self.receipt().status)
        self.assertEqual(1, len({key for _, key in provider.register_calls}))

    def test_permanent_failure_does_not_loop_or_revoke_access(self) -> None:
        self.create_succeeded_payment()
        with self.database() as session:
            access_before = session.get(UserAccess, 1).subscription_ends_at
        provider = FakeReceiptProvider(
            [
                ReceiptProviderResult(
                    ReceiptProviderOutcome.PERMANENT_FAILURE,
                    error_code="configuration_invalid",
                )
            ]
        )
        service = ReceiptOutboxService(
            provider,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
        )

        first = service.process_one()
        second = service.process_one()
        with self.database() as session:
            access_after = session.get(UserAccess, 1).subscription_ends_at

        self.assertEqual(ReceiptProcessingReason.PERMANENT_FAILURE, first.reason)
        self.assertEqual(ReceiptProcessingReason.NOTHING_DUE, second.reason)
        self.assertEqual(access_before, access_after)
        self.assertEqual(1, len(provider.register_calls))

    def test_two_workers_cannot_register_same_claim_concurrently(self) -> None:
        self.create_succeeded_payment()
        entered = threading.Event()
        release = threading.Event()

        class BlockingProvider(FakeReceiptProvider):
            def register_income_receipt(self, intent, idempotency_key):
                self.register_calls.append((intent, idempotency_key))
                entered.set()
                release.wait(timeout=3)
                return ReceiptProviderResult(
                    ReceiptProviderOutcome.SUCCEEDED,
                    provider_receipt_id="receipt-concurrent-1",
                )

        provider = BlockingProvider()
        first_service = ReceiptOutboxService(
            provider,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            claim_token_factory=lambda: "claim-one",
        )
        second_service = ReceiptOutboxService(
            provider,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            claim_token_factory=lambda: "claim-two",
        )
        first_result = []
        thread = threading.Thread(target=lambda: first_result.append(first_service.process_one()))
        thread.start()
        self.assertTrue(entered.wait(timeout=2))

        second_result = second_service.process_one()
        release.set()
        thread.join(timeout=3)

        self.assertEqual(ReceiptProcessingReason.NOTHING_DUE, second_result.reason)
        self.assertEqual(ReceiptProcessingReason.SUCCEEDED, first_result[0].reason)
        self.assertEqual(1, len(provider.register_calls))
        self.assertEqual(ReceiptStatus.SUCCEEDED.value, self.receipt().status)


if __name__ == "__main__":
    unittest.main()
