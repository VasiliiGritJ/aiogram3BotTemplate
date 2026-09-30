import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from db.migrations import run_migrations
from db.models import SqliteSession


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ReceiptOutboxSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = SqliteSession(
            sqlite_url(Path(self.temporary.name) / "receipt-schema.db")
        )

    def tearDown(self) -> None:
        self.database.dispose()
        self.temporary.cleanup()

    def test_clean_migration_creates_receipt_constraints_and_indexes(self) -> None:
        applied = run_migrations(self.database.engine)
        self.assertEqual(tuple(range(1, 18)), applied)
        inspector = inspect(self.database.engine)
        self.assertIn("payment_receipts", inspector.get_table_names())
        columns = {column["name"] for column in inspector.get_columns("payment_receipts")}
        self.assertTrue(
            {
                "subscription_payment_id",
                "idempotency_key",
                "status",
                "attempts",
                "next_attempt_at",
                "provider_receipt_id",
                "amount_minor",
                "currency",
                "item_description",
                "income_received_at",
                "claim_token",
            }.issubset(columns)
        )
        index_names = {index["name"] for index in inspector.get_indexes("payment_receipts")}
        self.assertIn("ix_payment_receipts_delivery", index_names)
        self.assertIn("uq_payment_receipts_provider_receipt_id", index_names)

    def test_historic_succeeded_payment_is_not_backfilled(self) -> None:
        run_migrations(self.database.engine, target_version=12)
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                "VALUES (1, 9901, 'Historic User', 'historic', 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO user_access(user_id, subscription_ends_at) "
                "VALUES (1, '2026-10-01 00:00:00')"
            )
            connection.exec_driver_sql(
                """INSERT INTO subscription_payments(
                    id, user_id, provider, provider_payment_id, idempotency_key,
                    product_code, amount_minor, currency, period_days, status,
                    confirmed_at, access_applied_at, grant_started_at, grant_ends_at
                ) VALUES (
                    1, 1, 'yookassa', 'historic-provider-payment', 'historic-key',
                    'monthly_30d_v1', 49900, 'RUB', 30, 'succeeded',
                    '2026-08-29 12:00:00', '2026-08-29 12:00:00',
                    '2026-08-29 12:00:00', '2026-09-28 12:00:00'
                )"""
            )

        self.assertEqual((13, 14, 15, 16, 17), run_migrations(self.database.engine))
        self.assertEqual((), run_migrations(self.database.engine))
        with self.database.engine.connect() as connection:
            receipt_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM payment_receipts"
            ).scalar_one()
            access_end = connection.exec_driver_sql(
                "SELECT subscription_ends_at FROM user_access WHERE user_id = 1"
            ).scalar_one()
            quick = connection.exec_driver_sql("PRAGMA quick_check").scalar_one()
            fk_issues = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(0, receipt_count)
        self.assertEqual("2026-10-01 00:00:00", access_end)
        self.assertEqual("ok", quick)
        self.assertEqual([], fk_issues)

    def test_one_receipt_per_payment_and_valid_status_are_enforced(self) -> None:
        run_migrations(self.database.engine)
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users(id, tg_id, fullname, username, inviter_id) "
                "VALUES (1, 9902, 'Schema User', 'schema', 0)"
            )
            connection.exec_driver_sql(
                """INSERT INTO subscription_payments(
                    id, user_id, provider, provider_payment_id, idempotency_key,
                    product_code, amount_minor, currency, period_days, status
                ) VALUES (
                    1, 1, 'yookassa', 'provider-schema', 'payment-schema-key',
                    'monthly_30d_v1', 49900, 'RUB', 30, 'succeeded'
                )"""
            )
            connection.exec_driver_sql(
                """INSERT INTO payment_receipts(
                    subscription_payment_id, idempotency_key, status, attempts,
                    product_code, amount_minor, currency, service_period_days,
                    item_description, payment_reference, income_received_at
                ) VALUES (
                    1, 'receipt-key', 'pending', 0, 'monthly_30d_v1', 49900,
                    'RUB', 30, 'Service', 'provider-schema', '2026-08-29'
                )"""
            )
        with self.assertRaises(IntegrityError):
            with self.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    """INSERT INTO payment_receipts(
                        subscription_payment_id, idempotency_key, status, attempts,
                        product_code, amount_minor, currency, service_period_days,
                        item_description, payment_reference, income_received_at
                    ) VALUES (
                        1, 'other-receipt-key', 'pending', 0, 'monthly_30d_v1',
                        49900, 'RUB', 30, 'Service', 'provider-schema', '2026-08-29'
                    )"""
                )


if __name__ == "__main__":
    unittest.main()
