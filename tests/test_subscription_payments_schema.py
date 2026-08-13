import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from db.migrations import run_migrations
from db.models import SqliteSession


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class SubscriptionPaymentsSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_directory.name) / "payments.db"
        self.session = SqliteSession(sqlite_url(self.database))
        self.assertEqual(
            (1, 2, 3, 4, 5, 6, 7),
            run_migrations(self.session.engine),
        )
        with self.session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO users (id, tg_id, fullname, username, inviter_id)
                VALUES (1, 1001, 'Payment User', 'payment_user', 0)
                """
            )

    def tearDown(self) -> None:
        self.session.dispose()
        self.temp_directory.cleanup()

    def insert_payment(self, **overrides: object) -> None:
        values = {
            "user_id": 1,
            "provider": "yookassa",
            "provider_payment_id": None,
            "idempotency_key": "key-1",
            "product_code": "monthly_30d_v1",
            "amount_minor": 99000,
            "currency": "RUB",
            "period_days": 30,
            "status": "creating",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.session.engine.begin() as connection:
            connection.exec_driver_sql(
                f"INSERT INTO subscription_payments ({columns}) VALUES ({placeholders})",
                values,
            )

    def test_migration_is_idempotent_and_has_required_columns(self) -> None:
        self.assertEqual((), run_migrations(self.session.engine))
        columns = {
            column["name"]
            for column in inspect(self.session.engine).get_columns("subscription_payments")
        }

        self.assertTrue(
            {
                "id", "user_id", "provider", "provider_payment_id",
                "idempotency_key", "product_code", "amount_minor", "currency",
                "period_days", "status", "confirmation_url", "provider_expires_at",
                "cancellation_code", "failure_code", "created_at", "updated_at",
                "confirmed_at", "last_checked_at", "access_applied_at",
                "grant_started_at", "grant_ends_at",
            }.issubset(columns)
        )

    def test_amount_period_and_status_constraints_are_enforced(self) -> None:
        for overrides in (
            {"amount_minor": 0},
            {"period_days": 0},
            {"status": "unknown"},
            {"currency": "rub"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError):
                    self.insert_payment(**overrides)

    def test_idempotency_key_and_provider_payment_id_are_unique(self) -> None:
        self.insert_payment(provider_payment_id="provider-1")

        with self.assertRaises(IntegrityError):
            self.insert_payment(idempotency_key="key-1", provider_payment_id="provider-2")
        with self.assertRaises(IntegrityError):
            self.insert_payment(idempotency_key="key-2", provider_payment_id="provider-1")

    def test_multiple_null_provider_payment_ids_are_allowed(self) -> None:
        self.insert_payment(idempotency_key="key-1", status="canceled")
        self.insert_payment(idempotency_key="key-2", status="failed")

    def test_only_one_active_payment_exists_per_user_and_product(self) -> None:
        self.insert_payment(idempotency_key="key-1", status="creating")
        with self.assertRaises(IntegrityError):
            self.insert_payment(idempotency_key="key-2", status="pending")

        self.insert_payment(
            idempotency_key="key-3",
            status="pending",
            product_code="other_product",
        )

    def test_terminal_payment_does_not_block_new_payment(self) -> None:
        self.insert_payment(idempotency_key="key-1", status="canceled")
        self.insert_payment(idempotency_key="key-2", status="creating")

    def test_user_delete_is_restricted_by_payment_history(self) -> None:
        self.insert_payment(idempotency_key="key-1", status="succeeded")

        with self.assertRaises(IntegrityError):
            with self.session.engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM users WHERE id = 1")

    def test_legacy_payments_table_and_rows_are_preserved(self) -> None:
        with self.session.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO payments (user, yoo_id, link, status)
                VALUES (1, 'legacy-payment', 'https://example.invalid/legacy', 'pending')
                """
            )

        self.assertEqual((), run_migrations(self.session.engine))
        with self.session.engine.connect() as connection:
            legacy_row = connection.exec_driver_sql(
                "SELECT user, yoo_id, link, status FROM payments WHERE yoo_id = 'legacy-payment'"
            ).one()

        self.assertEqual(
            (1, "legacy-payment", "https://example.invalid/legacy", "pending"),
            legacy_row,
        )


if __name__ == "__main__":
    unittest.main()
