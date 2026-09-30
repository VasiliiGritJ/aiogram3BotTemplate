import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, inspect, select

from db.migrations import run_migrations
from db.models import PaymentReceipt, SqliteSession, UserAccess
from services.payment_domain import PaymentStatus
from services.payment_provider import ProviderPayment
from services.payment_service import PaymentService, SubscriptionProduct
from services.receipt_email import (
    RECEIPT_EMAIL_ERROR_MESSAGE,
    ReceiptEmailValidationError,
    ReceiptEmailRequired,
    get_receipt_email,
    normalize_receipt_email,
    save_receipt_email,
)
from tests.payment_fakes import FakePaymentProvider


BASE_TIME = datetime(2026, 8, 31, 12, 0)
PRODUCT = SubscriptionProduct("monthly_30d_v1", 49_900, "RUB", 30)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ReceiptEmailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "receipt-email.db")
        )
        self.assertEqual(tuple(range(1, 18)), run_migrations(self.database.engine))
        self.add_user_with_profile(1, email=None)
        self.add_user_with_profile(2, email=None)

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    def add_user_with_profile(self, user_id: int, *, email: str | None) -> None:
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                "VALUES (:id, :tg_id, 'Receipt User', 'receipt_user', 0)",
                {"id": user_id, "tg_id": 8000 + user_id},
            )
            connection.exec_driver_sql(
                """INSERT INTO fitness_profiles(
                    user_id, age, sex, height_cm, weight_kg, goal,
                    experience_level, training_environment, workouts_per_week,
                    session_duration_minutes, limitations, receipt_email, completed_at,
                    updated_at
                ) VALUES (
                    :id, 30, 'male', 180, 80, 'muscle_gain', 'beginner', 'gym',
                    3, 60, NULL, :email, :now, :now
                )""",
                {"id": user_id, "email": email, "now": BASE_TIME},
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access(user_id, updated_at) VALUES (:id, :now)",
                {"id": user_id, "now": BASE_TIME},
            )

    def test_normalizes_valid_addresses_and_rejects_obvious_invalid_input(self) -> None:
        self.assertEqual(
            "name.surname+tag@example.co.uk",
            normalize_receipt_email("  Name.Surname+tag@Example.Co.UK  "),
        )
        for value in ("", "a b@example.com", "nameexample.com", "name@", "a@b", "a@b\n.com"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ReceiptEmailValidationError, RECEIPT_EMAIL_ERROR_MESSAGE):
                    normalize_receipt_email(value)

    def test_nullable_existing_profile_and_update_are_supported_without_uniqueness(self) -> None:
        self.assertIsNone(get_receipt_email(1, self.database))
        self.assertEqual(
            "same@example.com",
            save_receipt_email(1, " SAME@example.com ", self.database),
        )
        self.assertEqual(
            "same@example.com",
            save_receipt_email(2, "same@example.com", self.database),
        )
        self.assertEqual("same@example.com", get_receipt_email(1, self.database))
        self.assertEqual("same@example.com", get_receipt_email(2, self.database))

    def test_new_receipt_snapshots_email_and_profile_change_does_not_mutate_it(self) -> None:
        save_receipt_email(1, "before@example.com", self.database)
        provider = FakePaymentProvider([
            ProviderPayment(
                provider_payment_id="receipt-email-provider-1",
                status=PaymentStatus.SUCCEEDED,
                amount_minor=PRODUCT.amount_minor,
                currency=PRODUCT.currency,
                metadata={"local_payment_id": "1", "product_code": PRODUCT.product_code},
            )
        ])
        service = PaymentService(
            provider,
            PRODUCT,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            idempotency_key_factory=lambda: "receipt-email-payment-key",
        )
        self.assertTrue(service.get_or_create_payment(1).access_applied)
        save_receipt_email(1, "after@example.com", self.database)
        with self.database() as session:
            receipt = session.scalar(select(PaymentReceipt))
            self.assertIsNotNone(receipt)
            self.assertEqual("before@example.com", receipt.buyer_contact)
            self.assertEqual("after@example.com", get_receipt_email(1, self.database))

    def test_payment_service_guard_blocks_provider_create_without_receipt_email(self) -> None:
        provider = FakePaymentProvider([self.provider_payment(PaymentStatus.SUCCEEDED)])
        service = PaymentService(
            provider,
            PRODUCT,
            session_factory=self.database,
            now_factory=lambda: BASE_TIME,
            receipt_email_required=lambda user_id: False,
        )
        with self.assertRaises(ReceiptEmailRequired):
            service.get_or_create_payment(1)
        self.assertEqual([], provider.create_calls)
        with self.database() as session:
            self.assertEqual(0, session.scalar(select(func.count(PaymentReceipt.id))))

    @staticmethod
    def provider_payment(status: PaymentStatus) -> ProviderPayment:
        return ProviderPayment(
            provider_payment_id="receipt-email-provider-1",
            status=status,
            amount_minor=PRODUCT.amount_minor,
            currency=PRODUCT.currency,
            metadata={"local_payment_id": "1", "product_code": PRODUCT.product_code},
        )

    def test_migration_fourteen_preserves_existing_rows_and_is_idempotent(self) -> None:
        migration_db = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "migration-fourteen.db")
        )
        try:
            self.assertEqual(tuple(range(1, 14)), run_migrations(migration_db.engine, 13))
            with migration_db.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                    "VALUES (10, 9010, 'Legacy User', 'legacy_user', 0)"
                )
                connection.exec_driver_sql(
                    """INSERT INTO fitness_profiles(
                        user_id, age, sex, height_cm, weight_kg, goal,
                        experience_level, training_environment, workouts_per_week,
                        session_duration_minutes, limitations, completed_at
                    ) VALUES (
                        10, 30, 'male', 180, 80, 'muscle_gain', 'beginner', 'gym',
                        3, 60, NULL, :now
                    )""",
                    {"now": BASE_TIME},
                )
                connection.exec_driver_sql(
                    "INSERT INTO user_access(user_id, updated_at) VALUES (10, :now)",
                    {"now": BASE_TIME},
                )
            self.assertEqual((14, 15, 16, 17), run_migrations(migration_db.engine))
            self.assertEqual((), run_migrations(migration_db.engine))
            with migration_db.engine.connect() as connection:
                self.assertEqual("ok", connection.exec_driver_sql("PRAGMA quick_check").scalar_one())
                self.assertEqual([], connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
                self.assertEqual(1, connection.exec_driver_sql("SELECT COUNT(*) FROM users").scalar_one())
                self.assertIsNone(connection.exec_driver_sql(
                    "SELECT receipt_email FROM fitness_profiles WHERE user_id = 10"
                ).scalar_one())
                self.assertEqual(17, connection.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations").scalar_one())
            self.assertIn("receipt_email", {
                column["name"]
                for column in inspect(migration_db.engine).get_columns("fitness_profiles")
            })
        finally:
            migration_db.dispose()


if __name__ == "__main__":
    unittest.main()
