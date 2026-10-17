import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from hashlib import sha256
from hmac import new as hmac_new
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from db.migrations import run_migrations
from db.models import PaymentReceipt, SqliteSession, SubscriptionPayment, UserAccess
from services.prodamus import (
    ProdamusCallbackReason,
    ProdamusConfig,
    ProdamusService,
    build_prodamus_signature,
)
from services.payment_domain import PaymentStatus


NOW = datetime(2026, 9, 30, 12, 0)
SECRET = "test-prodamus-secret-key-not-production"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ProdamusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteSession(sqlite_url(Path(self.temp.name) / "prodamus.db"))
        self.assertEqual(tuple(range(1, 19)), run_migrations(self.db.engine))
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                "VALUES (1, 1001, 'Buyer', 'buyer', 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access(user_id, updated_at) VALUES (1, :now)",
                {"now": NOW},
            )
        self.service = ProdamusService(
            ProdamusConfig(
                "https://trainer.payform.ru/",
                SECRET,
                "https://payments.traineros.ru/webhooks/prodamus",
                "https://payments.traineros.ru/payment/success",
                "https://payments.traineros.ru/payment/fail",
                "https://payments.traineros.ru/buy",
            ),
            session_factory=self.db,
            now_factory=lambda: NOW,
            transaction_id_factory=lambda: "prodamus-test-1",
        )

    def tearDown(self) -> None:
        self.db.dispose()
        self.temp.cleanup()

    def order(self):
        return self.service.create_order(1, "buyer@example.com")

    def payload(self, **changes):
        order = self.order()
        fields = {
            "order_id": "provider-operation-1",
            "order_num": order.transaction_id,
            "sum": "499.00",
            "currency": "rub",
            "domain": "trainer.payform.ru",
            "customer_email": "buyer@example.com",
            "payment_status": "success",
        }
        fields.update(changes)
        return fields

    def signed(self, **changes):
        payload = self.payload(**changes)
        return payload, build_prodamus_signature(payload, SECRET)

    def payment_and_access(self):
        with self.db() as session:
            payment = session.scalar(select(SubscriptionPayment))
            access = session.get(UserAccess, 1)
            assert payment is not None and access is not None
            return payment, access

    def test_order_is_signed_and_bound_to_exact_499_rub_email_and_inventory(self):
        order = self.order()
        query = parse_qs(urlparse(order.checkout_url).query, keep_blank_values=True)
        signed_fields = {
            "do": query["do"][0],
            "order_id": query["order_id"][0],
            "order_num": query["order_num"][0],
            "customer_email": query["customer_email"][0],
            "customer_extra": query["customer_extra"][0],
            "currency": query["currency"][0],
            "sys": query["sys"][0],
            "urlNotification": query["urlNotification"][0],
            "urlSuccess": query["urlSuccess"][0],
            "urlFail": query["urlFail"][0],
            "urlReturn": query["urlReturn"][0],
            "products": [{
                "name": query["products[0][name]"][0],
                "price": query["products[0][price]"][0],
                "quantity": int(query["products[0][quantity]"][0]),
                "sum": query["products[0][sum]"][0],
            }],
        }
        self.assertEqual("499.00", signed_fields["products"][0]["price"])
        self.assertEqual("rub", signed_fields["currency"])
        self.assertEqual("buyer@example.com", signed_fields["customer_email"])
        self.assertEqual(build_prodamus_signature(signed_fields, SECRET), query["signature"][0])

    def test_valid_callback_issues_one_thirty_day_entitlement_and_receipt_snapshot(self):
        payload, signature = self.signed()
        result = self.service.process_notification(payload, signature)
        payment, access = self.payment_and_access()
        self.assertTrue(result.accepted)
        self.assertTrue(result.access_applied)
        self.assertEqual(ProdamusCallbackReason.CONFIRMED, result.reason)
        self.assertEqual(NOW + timedelta(days=30), access.subscription_ends_at)
        self.assertEqual("buyer@example.com", payment.buyer_email)
        with self.db() as session:
            receipt = session.scalar(select(PaymentReceipt))
        self.assertEqual("buyer@example.com", receipt.buyer_contact)
        self.assertEqual(49900, receipt.amount_minor)
        self.assertEqual("RUB", receipt.currency)
        self.assertEqual(30, receipt.service_period_days)
        self.assertEqual("pending", receipt.status)  # Payment is not receipt-delivery proof.
        self.assertIsNone(receipt.provider_receipt_id)

    def test_invalid_signature_unknown_amount_and_customer_mismatch_fail_closed(self):
        cases = (
            (self.payload(), "0" * 64, ProdamusCallbackReason.INVALID_SIGNATURE),
            (*self.signed(order_num="unknown"), ProdamusCallbackReason.PAYMENT_NOT_FOUND),
            (*self.signed(sum="498.00"), ProdamusCallbackReason.AMOUNT_MISMATCH),
            (*self.signed(customer_email="other@example.com"), ProdamusCallbackReason.CUSTOMER_MISMATCH),
        )
        for payload, signature, reason in cases:
            with self.subTest(reason=reason):
                result = self.service.process_notification(payload, signature)
                self.assertFalse(result.accepted)
                self.assertEqual(reason, result.reason)
        _, access = self.payment_and_access()
        self.assertIsNone(access.subscription_ends_at)

    def test_duplicate_and_concurrent_callback_never_double_extend(self):
        payload, signature = self.signed()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: self.service.process_notification(payload, signature), range(2)))
        self.assertTrue(all(result.accepted for result in results))
        self.assertEqual(1, sum(result.access_applied for result in results))
        _, access = self.payment_and_access()
        self.assertEqual(NOW + timedelta(days=30), access.subscription_ends_at)
        with self.db() as session:
            self.assertEqual(1, len(session.scalars(select(PaymentReceipt)).all()))

    def test_signed_wrong_currency_or_channel_cannot_claim_payment_or_access(self):
        for changes, reason in (
            ({"currency": "usd"}, ProdamusCallbackReason.CURRENCY_MISMATCH),
            ({"currency": "eur"}, ProdamusCallbackReason.CURRENCY_MISMATCH),
            ({"domain": "other.payform.ru"}, ProdamusCallbackReason.DOMAIN_MISMATCH),
            ({"domain": "trainer.payform.ru.attacker.invalid"}, ProdamusCallbackReason.DOMAIN_MISMATCH),
            ({"domain": "https://trainer.payform.ru"}, ProdamusCallbackReason.DOMAIN_MISMATCH),
        ):
            with self.subTest(changes=changes):
                payload, signature = self.signed(**changes)
                result = self.service.process_notification(payload, signature)
                self.assertFalse(result.accepted)
                self.assertEqual(reason, result.reason)
                payment, access = self.payment_and_access()
                self.assertIsNone(payment.provider_payment_id)
                self.assertIsNone(access.subscription_ends_at)
                with self.db() as session:
                    self.assertIsNone(session.scalar(select(PaymentReceipt)))

    def test_currency_and_channel_are_required_signed_fields(self):
        for name in ("currency", "domain"):
            with self.subTest(name=name):
                payload = self.payload()
                del payload[name]
                signature = build_prodamus_signature(payload, SECRET)
                result = self.service.process_notification(payload, signature)
                self.assertFalse(result.accepted)
                self.assertEqual(ProdamusCallbackReason.MALFORMED, result.reason)
                self.assertIsNone(self.payment_and_access()[1].subscription_ends_at)

    def test_browser_return_cannot_grant_access(self):
        order = self.order()
        self.assertFalse(self.service.inspect_browser_success(order.transaction_id, user_id=1))
        _, access = self.payment_and_access()
        self.assertIsNone(access.subscription_ends_at)

    def test_signature_normalizes_form_scalars_and_has_independent_vector(self):
        payload = {"z": "https://example.invalid/", "a": [{"quantity": 1, "price": "499.00"}]}
        expected_bytes = b'{"a":[{"price":"499.00","quantity":"1"}],"z":"https:\\/\\/example.invalid\\/"}'
        expected = hmac_new(SECRET.encode(), expected_bytes, sha256).hexdigest()
        self.assertEqual(expected, build_prodamus_signature(payload, SECRET))
        payload["a"][0]["quantity"] = "1"
        self.assertEqual(expected, build_prodamus_signature(payload, SECRET))

    def test_active_subscription_and_trial_extend_from_existing_business_base(self):
        for field in ("subscription_ends_at", "trial_ends_at"):
            with self.subTest(field=field):
                with self.db() as session, session.begin():
                    access = session.get(UserAccess, 1)
                    access.subscription_ends_at = None
                    access.trial_ends_at = None
                    setattr(access, field, NOW + timedelta(days=4))
                    payment = session.scalar(select(SubscriptionPayment))
                    if payment is not None:
                        # Separate successful purchases; never replay an existing effect.
                        self.service._transaction_id_factory = lambda: "prodamus-test-2"
                payload, signature = self.signed(order_id=f"operation-{field}")
                result = self.service.process_notification(payload, signature)
                self.assertTrue(result.access_applied)
                self.assertEqual(NOW + timedelta(days=34), self.payment_and_access()[1].subscription_ends_at)

    def test_pending_and_negative_statuses_never_grant_or_cancel_from_guesswork(self):
        for status in ("pending", "failed", "cancelled", "canceled", "unknown"):
            payload, signature = self.signed(payment_status=status)
            result = self.service.process_notification(payload, signature)
            self.assertEqual(ProdamusCallbackReason.STATUS_REJECTED, result.reason)
            payment, access = self.payment_and_access()
            self.assertEqual("pending", payment.status)
            self.assertIsNone(payment.provider_payment_id)
            self.assertIsNone(access.subscription_ends_at)
        # A rejected/abandoned checkout remains retryable under the same order.
        self.assertFalse(self.order().created)
        payload, signature = self.signed()
        self.assertTrue(self.service.process_notification(payload, signature).accepted)

    def test_terminal_failed_cancelled_expired_orders_allow_new_order_without_access(self):
        for index, status in enumerate((PaymentStatus.FAILED, PaymentStatus.CANCELED, PaymentStatus.EXPIRED)):
            order = self.order()
            with self.db() as session, session.begin():
                session.get(SubscriptionPayment, order.payment_id).status = status.value
            self.service._transaction_id_factory = lambda: f"retry-{index}"
            next_order = self.order()
            self.assertTrue(next_order.created)
            self.assertNotEqual(order.payment_id, next_order.payment_id)
            self.assertIsNone(self.payment_and_access()[1].subscription_ends_at)

    def test_invalid_amounts_and_non_ascii_signature_are_controlled_rejections(self):
        for amount in ("NaN", "Infinity", "-499", "499.001", "4.99E2"):
            payload, signature = self.signed(sum=amount)
            self.assertEqual(ProdamusCallbackReason.MALFORMED, self.service.process_notification(payload, signature).reason)
        self.assertEqual(ProdamusCallbackReason.INVALID_SIGNATURE,
                         self.service.process_notification(self.payload(), "я" * 64).reason)

    def test_success_status_is_owned_and_only_available_after_webhook(self):
        order = self.order()
        self.assertEqual(PaymentStatus.PENDING, self.service.inspect_order_status(order.payment_id, user_id=1))
        self.assertIsNone(self.service.inspect_order_status(order.payment_id, user_id=2))
        payload, signature = self.signed()
        self.service.process_notification(payload, signature)
        self.assertEqual(PaymentStatus.SUCCEEDED, self.service.inspect_order_status(order.payment_id, user_id=1))

    def test_observability_contains_only_event_and_local_payment_id(self):
        with self.assertLogs("services.prodamus", level="INFO") as logs:
            payload, signature = self.signed()
            checkout_url = self.order().checkout_url
            self.service.process_notification(payload, "0" * 64)
            self.service.process_notification(payload, signature)
            self.service.process_notification(payload, signature)
        output = "\n".join(logs.output)
        for event in ("order_created", "signature_invalid", "signature_valid", "payment_matched",
                      "subscription_activated", "duplicate_ignored"):
            self.assertIn(f"event={event}", output)
        for private in (SECRET, "buyer@example.com", signature, str(payload), checkout_url):
            self.assertNotIn(private, output)


if __name__ == "__main__":
    unittest.main()
