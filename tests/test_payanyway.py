import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from sqlalchemy import func, select

from db.migrations import run_migrations
from db.models import PaymentReceipt, SqliteSession, SubscriptionPayment, UserAccess
from services.payanyway import (
    PAYANYWAY_PROVIDER,
    PayAnyWayCallbackReason,
    PayAnyWayConfig,
    PayAnyWayService,
    build_payment_request_signature,
)
from services.payment_domain import PaymentStatus
from services.payment_service import PaymentServiceReason


BASE_TIME = datetime(2026, 9, 17, 12, 0, 0)
SECRET = "test-integrity-code"
MERCHANT_ID = "12345678"
TRANSACTION_ID = "order-stage8g0-1"
OPERATION_ID = "operation-9001"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class PayAnyWayCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "payanyway.db")
        )
        self.assertEqual(tuple(range(1, 19)), run_migrations(self.database.engine))
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO users(id, tg_id, fullname, username, inviter_id)
                VALUES (1, 88001, 'PayAnyWay User', 'payanyway_user', 0)"""
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access(user_id, updated_at) VALUES (1, :now)",
                {"now": BASE_TIME},
            )
            connection.exec_driver_sql(
                """INSERT INTO fitness_profiles(
                    user_id, age, sex, height_cm, weight_kg, goal,
                    experience_level, training_environment, workouts_per_week,
                    session_duration_minutes, limitations, receipt_email,
                    completed_at, updated_at
                ) VALUES (
                    1, 30, 'male', 180, 80, 'muscle_gain', 'beginner', 'gym',
                    3, 60, NULL, 'buyer@example.com', :now, :now
                )""",
                {"now": BASE_TIME},
            )
        self.service = PayAnyWayService(
            PayAnyWayConfig(
                MERCHANT_ID,
                SECRET,
                False,
                "https://www.traineros.ru/payment/success",
                "https://www.traineros.ru/payment/fail",
                "https://www.traineros.ru/payment/return",
            ),
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            transaction_id_factory=lambda: TRANSACTION_ID,
        )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    def signed_callback(self, **overrides: str) -> dict[str, str]:
        values = {
            "MNT_ID": MERCHANT_ID,
            "MNT_TRANSACTION_ID": TRANSACTION_ID,
            "MNT_OPERATION_ID": OPERATION_ID,
            "MNT_AMOUNT": "499.00",
            "MNT_CURRENCY_CODE": "RUB",
            "MNT_SUBSCRIBER_ID": "1",
            "MNT_TEST_MODE": "0",
        }
        values.update(overrides)
        signature_source = "".join(
            values[name]
            for name in (
                "MNT_ID",
                "MNT_TRANSACTION_ID",
                "MNT_OPERATION_ID",
                "MNT_AMOUNT",
                "MNT_CURRENCY_CODE",
                "MNT_SUBSCRIBER_ID",
                "MNT_TEST_MODE",
            )
        ) + SECRET
        values["MNT_SIGNATURE"] = hashlib.md5(
            signature_source.encode(), usedforsecurity=False
        ).hexdigest()
        return values

    def create_order(self):
        return self.service.create_order(1, "buyer@example.com")

    def payment_and_access(self):
        with self.database() as session:
            payment = session.scalar(select(SubscriptionPayment))
            access = session.get(UserAccess, 1)
            assert payment is not None and access is not None
            session.expunge(payment)
            session.expunge(access)
            return payment, access

    def test_order_is_bound_and_signed_for_exact_commercial_contract(self) -> None:
        order = self.create_order()
        query = {key: value[0] for key, value in parse_qs(urlparse(order.payment_url).query).items()}

        self.assertTrue(order.created)
        self.assertEqual(TRANSACTION_ID, order.transaction_id)
        self.assertEqual("499.00", query["MNT_AMOUNT"])
        self.assertEqual("RUB", query["MNT_CURRENCY_CODE"])
        self.assertEqual("1", query["MNT_SUBSCRIBER_ID"])
        self.assertEqual("buyer@example.com", query["MNT_CUSTOM1"])
        self.assertEqual("0", query["MNT_TEST_MODE"])
        expected = hashlib.md5(
            f"{MERCHANT_ID}{TRANSACTION_ID}499.00RUB10{SECRET}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        self.assertEqual(expected, query["MNT_SIGNATURE"])
        request_inventory = json.loads(query["MNT_CUSTOM2"])
        self.assertEqual("buyer@example.com", request_inventory["customer"])
        self.assertEqual("499.00", request_inventory["items"][0]["p"])
        with self.database() as session:
            payment = session.scalar(select(SubscriptionPayment))
            assert payment is not None
            self.assertEqual(PAYANYWAY_PROVIDER, payment.provider)
            self.assertEqual(49_900, payment.amount_minor)
            self.assertEqual(30, payment.period_days)
            self.assertEqual("pending", payment.status)

    def test_official_payment_request_signature_fixture(self) -> None:
        self.assertEqual(
            "c8222aef6362c7f1239ccdc729d1a200",
            build_payment_request_signature(
                "54600817",
                "FF790ABCD",
                "120.25",
                "RUB",
                "",
                "0",
                "QWERTY",
            ),
        )

    def test_signature_distinguishes_mode_and_integrity_secret(self) -> None:
        production = build_payment_request_signature(
            MERCHANT_ID, TRANSACTION_ID, "499.00", "RUB", "1", "0", SECRET
        )
        test_mode = build_payment_request_signature(
            MERCHANT_ID, TRANSACTION_ID, "499.00", "RUB", "1", "1", SECRET
        )
        rotated = build_payment_request_signature(
            MERCHANT_ID,
            TRANSACTION_ID,
            "499.00",
            "RUB",
            "1",
            "0",
            "rotated-integrity-code",
        )
        self.assertNotEqual(production, test_mode)
        self.assertNotEqual(production, rotated)

    def test_url_encoding_happens_after_signature_generation(self) -> None:
        order = self.create_order()
        parsed = urlparse(order.payment_url)
        query = {key: value[0] for key, value in parse_qs(parsed.query).items()}
        expected = build_payment_request_signature(
            MERCHANT_ID,
            TRANSACTION_ID,
            "499.00",
            "RUB",
            "1",
            "0",
            SECRET,
        )
        self.assertEqual(expected, query["MNT_SIGNATURE"])
        self.assertIn("MNT_CUSTOM1=buyer%40example.com", parsed.query)

    def test_reused_pending_order_is_resigned_after_secret_rotation(self) -> None:
        first = self.create_order()
        rotated_service = PayAnyWayService(
            PayAnyWayConfig(
                MERCHANT_ID,
                "rotated-integrity-code",
                False,
                "https://www.traineros.ru/payment/success",
                "https://www.traineros.ru/payment/fail",
                "https://www.traineros.ru/payment/return",
            ),
            session_factory=self.database,
            now_factory=lambda: BASE_TIME + timedelta(minutes=1),
            transaction_id_factory=lambda: "must-not-create-a-second-order",
        )
        reused = rotated_service.create_order(1, "buyer@example.com")

        self.assertFalse(reused.created)
        self.assertEqual(first.payment_id, reused.payment_id)
        self.assertNotEqual(first.payment_url, reused.payment_url)
        query = {
            key: value[0]
            for key, value in parse_qs(urlparse(reused.payment_url).query).items()
        }
        self.assertEqual(
            build_payment_request_signature(
                MERCHANT_ID,
                TRANSACTION_ID,
                "499.00",
                "RUB",
                "1",
                "0",
                "rotated-integrity-code",
            ),
            query["MNT_SIGNATURE"],
        )
        with self.database() as session:
            self.assertEqual(1, session.scalar(select(func.count(SubscriptionPayment.id))))
            payment = session.scalar(select(SubscriptionPayment))
            assert payment is not None
            self.assertEqual(reused.payment_url, payment.confirmation_url)

    def test_reopening_pending_payment_resigns_checkout_without_new_order(self) -> None:
        first = self.create_order()
        rotated_service = PayAnyWayService(
            PayAnyWayConfig(
                MERCHANT_ID,
                "rotated-integrity-code",
                False,
                "https://www.traineros.ru/payment/success",
                "https://www.traineros.ru/payment/fail",
                "https://www.traineros.ru/payment/return",
            ),
            session_factory=self.database,
            now_factory=lambda: BASE_TIME + timedelta(minutes=1),
        )

        reopened = rotated_service.get_latest_payment(1)

        assert reopened is not None
        self.assertEqual(first.payment_id, reopened.payment_id)
        self.assertNotEqual(first.payment_url, reopened.confirmation_url)
        self.assertEqual(
            PaymentServiceReason.ACTIVE_PAYMENT_REUSED,
            reopened.reason,
        )
        with self.database() as session:
            self.assertEqual(1, session.scalar(select(func.count(SubscriptionPayment.id))))

    def test_second_creation_reuses_one_active_order(self) -> None:
        first = self.create_order()
        second = self.create_order()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.payment_id, second.payment_id)
        with self.database() as session:
            count = session.scalar(select(func.count(SubscriptionPayment.id)))
        self.assertEqual(1, count)

    def test_provider_neutral_telegram_adapter_uses_saved_email_and_local_state(self) -> None:
        created = self.service.get_or_create_payment(1)
        reused = self.service.get_or_create_payment(1)

        self.assertEqual(PaymentServiceReason.CREATED, created.reason)
        self.assertEqual(PaymentStatus.PENDING, created.status)
        self.assertTrue(created.created)
        self.assertEqual(PaymentServiceReason.ACTIVE_PAYMENT_REUSED, reused.reason)
        self.assertEqual(created.payment_id, reused.payment_id)
        self.assertEqual(created.confirmation_url, reused.confirmation_url)

        pending = self.service.reconcile_payment(created.payment_id, user_id=1)
        self.assertEqual(PaymentServiceReason.NOT_SUCCEEDED, pending.reason)
        self.assertFalse(pending.access_applied)

        self.service.process_payurl(self.signed_callback())
        confirmed = self.service.reconcile_payment(created.payment_id, user_id=1)
        self.assertEqual(PaymentStatus.SUCCEEDED, confirmed.status)
        self.assertEqual(
            PaymentServiceReason.ACCESS_ALREADY_APPLIED, confirmed.reason
        )
        self.assertTrue(confirmed.access_applied)
        self.assertEqual(confirmed, self.service.get_latest_payment(1))

        with self.assertRaises(PermissionError):
            self.service.reconcile_payment(created.payment_id, user_id=999)

    def test_valid_payurl_confirms_and_issues_exactly_thirty_days_once(self) -> None:
        self.create_order()
        first = self.service.process_payurl(self.signed_callback())
        duplicate = self.service.process_payurl(self.signed_callback())
        payment, access = self.payment_and_access()

        self.assertTrue(first.accepted)
        self.assertTrue(first.access_applied)
        self.assertEqual(PayAnyWayCallbackReason.CONFIRMED, first.reason)
        self.assertTrue(duplicate.accepted)
        self.assertFalse(duplicate.access_applied)
        self.assertEqual(PayAnyWayCallbackReason.ALREADY_CONFIRMED, duplicate.reason)
        self.assertEqual(BASE_TIME, access.subscription_started_at)
        self.assertEqual(BASE_TIME + timedelta(days=30), access.subscription_ends_at)
        self.assertEqual(OPERATION_ID, payment.provider_payment_id)
        self.assertIsNotNone(payment.access_applied_at)
        with self.database() as session:
            self.assertEqual(1, session.scalar(select(func.count(PaymentReceipt.id))))

    def test_invalid_signature_wrong_amount_unknown_and_owner_mismatch_fail_closed(self) -> None:
        self.create_order()
        cases = (
            (
                {**self.signed_callback(), "MNT_SIGNATURE": "0" * 32},
                PayAnyWayCallbackReason.INVALID_SIGNATURE,
            ),
            (
                self.signed_callback(MNT_AMOUNT="498.99"),
                PayAnyWayCallbackReason.AMOUNT_MISMATCH,
            ),
            (
                self.signed_callback(MNT_TRANSACTION_ID="unknown-order"),
                PayAnyWayCallbackReason.PAYMENT_NOT_FOUND,
            ),
            (
                self.signed_callback(MNT_SUBSCRIBER_ID="999"),
                PayAnyWayCallbackReason.OWNERSHIP_MISMATCH,
            ),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                result = self.service.process_payurl(payload)
                self.assertFalse(result.accepted)
                self.assertEqual(reason, result.reason)
        _, access = self.payment_and_access()
        self.assertIsNone(access.subscription_ends_at)

    def test_malformed_payload_and_operation_mismatch_are_controlled(self) -> None:
        self.create_order()
        malformed = self.service.process_payurl({"MNT_ID": MERCHANT_ID})
        self.assertEqual(PayAnyWayCallbackReason.MALFORMED, malformed.reason)
        self.assertIn("<MNT_RESULT_CODE>500</MNT_RESULT_CODE>", malformed.response_xml)

        self.assertTrue(self.service.process_payurl(self.signed_callback()).accepted)
        second_operation = self.service.process_payurl(
            self.signed_callback(MNT_OPERATION_ID="operation-other")
        )
        self.assertFalse(second_operation.accepted)
        self.assertEqual(
            PayAnyWayCallbackReason.OPERATION_MISMATCH, second_operation.reason
        )

    def test_success_response_contains_signed_inventory_customer_and_npd_fields(self) -> None:
        self.create_order()
        result = self.service.process_payurl(self.signed_callback())
        root = ElementTree.fromstring(result.response_xml)
        attributes = {
            attribute.findtext("KEY"): attribute.findtext("VALUE")
            for attribute in root.findall("./MNT_ATTRIBUTES/ATTRIBUTE")
        }
        expected_signature = hashlib.md5(
            f"200{MERCHANT_ID}{TRANSACTION_ID}{SECRET}".encode(),
            usedforsecurity=False,
        ).hexdigest()

        self.assertEqual("200", root.findtext("MNT_RESULT_CODE"))
        self.assertEqual("499.00", root.findtext("MNT_AMOUNT"))
        self.assertEqual(expected_signature, root.findtext("MNT_SIGNATURE"))
        self.assertEqual("buyer@example.com", attributes["CUSTOMER"])
        inventory = json.loads(attributes["INVENTORY"])
        self.assertEqual("499.00", inventory[0]["price"])
        self.assertEqual("1", inventory[0]["quantity"])
        self.assertEqual("1105", inventory[0]["vatTag"])
        self.assertEqual("full_payment", inventory[0]["pm"])
        self.assertEqual("service", inventory[0]["po"])

    def test_browser_success_redirect_never_grants_access(self) -> None:
        self.create_order()
        browser_result = self.service.inspect_browser_success(
            TRANSACTION_ID, user_id=1
        )
        payment, access = self.payment_and_access()
        self.assertEqual("pending", payment.status)
        self.assertFalse(browser_result.access_confirmed)
        self.assertIsNone(access.subscription_ends_at)

    def test_concurrent_duplicate_confirmation_cannot_double_extend(self) -> None:
        self.create_order()
        payload = self.signed_callback()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(self.service.process_payurl, (payload, payload)))
        self.assertTrue(all(result.accepted for result in results))
        self.assertEqual(1, sum(result.access_applied for result in results))
        _, access = self.payment_and_access()
        self.assertEqual(BASE_TIME + timedelta(days=30), access.subscription_ends_at)
        with self.database() as session:
            self.assertEqual(1, session.scalar(select(func.count(PaymentReceipt.id))))


if __name__ == "__main__":
    unittest.main()
