import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
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


NOW = datetime(2026, 9, 30, 12, 0)
SECRET = "test-prodamus-secret-key-not-production"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ProdamusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteSession(sqlite_url(Path(self.temp.name) / "prodamus.db"))
        self.assertEqual(tuple(range(1, 18)), run_migrations(self.db.engine))
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

    def test_browser_return_cannot_grant_access(self):
        order = self.order()
        self.assertFalse(self.service.inspect_browser_success(order.transaction_id, user_id=1))
        _, access = self.payment_and_access()
        self.assertIsNone(access.subscription_ends_at)


if __name__ == "__main__":
    unittest.main()
