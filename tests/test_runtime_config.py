import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from storage.runtime_config import (
    ApplicationEnvironment,
    RuntimeConfigurationError,
    SettingsReader,
    build_bot_role_config,
    build_maintenance_role_config,
    build_webhook_role_config,
    process_environment_settings,
)
from storage.payment_runtime import (
    PaymentMode,
    PaymentProviderName,
    PaymentRuntime,
)
from storage.receipt_runtime import ReceiptMode, build_receipt_runtime_config
from storage.runtime_bootstrap import validate_payment_environment


SECRET = "secret-value-that-must-not-leak"


def settings(values: dict[str, str]) -> SettingsReader:
    return SettingsReader(values.__getitem__, values.get)


class RuntimeConfigTests(unittest.TestCase):
    def test_bot_module_import_does_not_read_dotenv_or_create_bot(self) -> None:
        previous_bot = sys.modules.pop("bot", None)
        previous_config = sys.modules.pop("storage.config", None)
        try:
            with (
                patch("environs.Env.read_env") as read_env,
                patch("storage.telegram_session.create_telegram_bot") as create_bot,
            ):
                module = importlib.import_module("bot")
            read_env.assert_not_called()
            create_bot.assert_not_called()
            self.assertTrue(callable(module.run))
        finally:
            sys.modules.pop("bot", None)
            sys.modules.pop("storage.config", None)
            if previous_bot is not None:
                sys.modules["bot"] = previous_bot
            if previous_config is not None:
                sys.modules["storage.config"] = previous_config

    def test_process_environment_loader_never_reads_dotenv(self) -> None:
        with patch("environs.Env.read_env") as read_env:
            reader = process_environment_settings()
        read_env.assert_not_called()
        self.assertIsInstance(reader, SettingsReader)

    def test_legacy_payment_import_no_longer_reads_credentials(self) -> None:
        sys.modules.pop("payments.yookassa_manager", None)
        with patch("environs.Env.read_env") as read_env:
            module = importlib.import_module("payments.yookassa_manager")
        read_env.assert_not_called()
        self.assertTrue(hasattr(module, "YOOKassa"))

    def test_development_bot_role_has_only_bot_and_safe_local_defaults(self) -> None:
        config = build_bot_role_config(
            settings({"BOT_TOKEN": "123456:test-token"}),
            expected_environment=ApplicationEnvironment.DEVELOPMENT,
        )
        self.assertEqual(Path("db.db").resolve(), config.database_path)
        self.assertIsNone(config.telegram_proxy_url)
        self.assertEqual((), config.admin_ids)
        self.assertNotIn("test-token", repr(config))

    def test_production_bot_role_requires_explicit_environment_and_absolute_db(self) -> None:
        for values in (
            {"BOT_TOKEN": "token"},
            {"APP_ENV": "production", "BOT_TOKEN": "token"},
            {
                "APP_ENV": "development",
                "BOT_TOKEN": "token",
                "DATABASE_PATH": str(Path("runtime.db").resolve()),
            },
        ):
            with self.subTest(values=values), self.assertRaises(RuntimeConfigurationError):
                build_bot_role_config(
                    settings(values),
                    expected_environment=ApplicationEnvironment.PRODUCTION,
                )

    def test_webhook_role_has_separate_required_production_config(self) -> None:
        database = str(Path("runtime.db").resolve())
        config = build_webhook_role_config(
            settings(
                {
                    "APP_ENV": "production",
                    "DATABASE_PATH": database,
                    "PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1",
                    "PAYMENT_WEBHOOK_BIND_PORT": "8081",
                    "PAYMENT_WEBHOOK_PATH": "/internal/payment",
                    "PAYMENT_WEBHOOK_PUBLIC_URL": "https://payments.traineros.ru/internal/payment",
                }
            ),
            expected_environment=ApplicationEnvironment.PRODUCTION,
        )
        self.assertEqual(Path(database), config.database_path)
        self.assertEqual(8081, config.bind_port)
        self.assertEqual(
            "https://payments.traineros.ru/internal/payment",
            config.public_url,
        )

        with self.assertRaises(RuntimeConfigurationError):
            build_webhook_role_config(
                settings({"APP_ENV": "production", "DATABASE_PATH": database}),
                expected_environment=ApplicationEnvironment.PRODUCTION,
            )

        for public_url in (
            "http://payments.traineros.ru/internal/payment",
            "https://payments.traineros.ru/other",
            "https://user:secret@payments.traineros.ru/internal/payment",
        ):
            with self.subTest(public_url=public_url), self.assertRaises(
                RuntimeConfigurationError
            ):
                build_webhook_role_config(
                    settings(
                        {
                            "APP_ENV": "production",
                            "DATABASE_PATH": database,
                            "PAYMENT_WEBHOOK_BIND_HOST": "127.0.0.1",
                            "PAYMENT_WEBHOOK_BIND_PORT": "8081",
                            "PAYMENT_WEBHOOK_PATH": "/internal/payment",
                            "PAYMENT_WEBHOOK_PUBLIC_URL": public_url,
                        }
                    ),
                    expected_environment=ApplicationEnvironment.PRODUCTION,
                )

    def test_maintenance_role_needs_only_database_path(self) -> None:
        config = build_maintenance_role_config(
            settings({"DATABASE_PATH": str(Path("maintenance.db").resolve())})
        )
        self.assertEqual(Path("maintenance.db").resolve(), config.database_path)

    def test_application_and_payment_modes_must_match(self) -> None:
        validate_payment_environment(ApplicationEnvironment.DEVELOPMENT, PaymentMode.TEST)
        validate_payment_environment(
            ApplicationEnvironment.PRODUCTION,
            PaymentMode.PRODUCTION,
        )
        for environment, mode in (
            (ApplicationEnvironment.DEVELOPMENT, PaymentMode.PRODUCTION),
            (ApplicationEnvironment.PRODUCTION, PaymentMode.TEST),
        ):
            with self.subTest(environment=environment, mode=mode):
                with self.assertRaises(RuntimeConfigurationError):
                    validate_payment_environment(environment, mode)

    def test_mode_mismatch_blocks_provider_composition_before_side_effects(self) -> None:
        import bot as bot_entrypoint

        values = {
            "APP_ENV": "development",
            "BOT_TOKEN": "123456:test-token",
            "PAYMENTS_MODE": "production",
            "PAYMENTS_TEST_MODE": "false",
        }
        with patch.object(bot_entrypoint, "build_payment_runtime") as builder:
            with self.assertRaises(RuntimeConfigurationError):
                bot_entrypoint.build_bot_application(
                    settings(values),
                    expected_environment=ApplicationEnvironment.DEVELOPMENT,
                )
        builder.assert_not_called()

    def test_production_bot_accepts_payanyway_as_the_receipt_delivery_adapter(self) -> None:
        import bot as bot_entrypoint

        values = {
            "APP_ENV": "production",
            "DATABASE_PATH": str(Path("runtime.db").resolve()),
            "BOT_TOKEN": "123456:test-token",
            "PAYMENTS_MODE": "production",
            "RECEIPTS_MODE": "production",
            "RECEIPT_PROVIDER": "payanyway",
        }
        payment_runtime = PaymentRuntime(
            object(),
            object(),
            PaymentMode.PRODUCTION,
            provider=PaymentProviderName.PAYANYWAY,
        )
        with (
            patch.object(
                bot_entrypoint,
                "build_payment_runtime",
                return_value=payment_runtime,
            ),
            patch.object(bot_entrypoint, "initialize_database_runtime") as database,
            patch.object(bot_entrypoint, "configure_payment_runtime") as configure,
            patch.object(bot_entrypoint.bot_registry, "configure_bot_role") as bot_role,
            patch.object(bot_entrypoint.importlib, "import_module"),
        ):
            database.return_value.validation.migration_versions = tuple(range(1, 17))
            runtime = bot_entrypoint.build_bot_application(
                settings(values),
                expected_environment=ApplicationEnvironment.PRODUCTION,
            )

        self.assertIs(ApplicationEnvironment.PRODUCTION, runtime.environment)
        configure.assert_called_once_with(payment_runtime)
        bot_role.assert_called_once()

        values["RECEIPT_PROVIDER"] = "other"
        with (
            patch.object(
                bot_entrypoint,
                "build_payment_runtime",
                return_value=payment_runtime,
            ),
            self.assertRaises(RuntimeConfigurationError),
        ):
            bot_entrypoint.build_bot_application(
                settings(values),
                expected_environment=ApplicationEnvironment.PRODUCTION,
            )

    def test_configuration_errors_never_echo_reader_secret(self) -> None:
        reader = SettingsReader(
            lambda name: (_ for _ in ()).throw(RuntimeError(SECRET)),
            lambda name: None,
        )
        with self.assertRaises(RuntimeConfigurationError) as error:
            build_bot_role_config(
                reader,
                expected_environment=ApplicationEnvironment.DEVELOPMENT,
            )
        self.assertNotIn(SECRET, str(error.exception))

    def test_receipts_default_to_disabled_only_in_development(self) -> None:
        config = build_receipt_runtime_config(
            settings({}),
            environment=ApplicationEnvironment.DEVELOPMENT,
        )
        self.assertIs(ReceiptMode.DISABLED, config.mode)
        with self.assertRaises(RuntimeConfigurationError):
            build_receipt_runtime_config(
                settings({}),
                environment=ApplicationEnvironment.PRODUCTION,
            )

    def test_receipt_test_mode_is_explicit_and_provider_neutral(self) -> None:
        config = build_receipt_runtime_config(
            settings({"RECEIPTS_MODE": "test", "RECEIPT_PROVIDER": "fake"}),
            environment=ApplicationEnvironment.DEVELOPMENT,
        )
        self.assertIs(ReceiptMode.TEST, config.mode)
        self.assertEqual("fake", config.provider_code)

    def test_production_receipts_fail_closed_until_adapter_is_available(self) -> None:
        values = {
            "RECEIPTS_MODE": "production",
            "RECEIPT_PROVIDER": "future_authorized_operator",
        }
        with self.assertRaises(RuntimeConfigurationError):
            build_receipt_runtime_config(
                settings(values),
                environment=ApplicationEnvironment.PRODUCTION,
            )
        config = build_receipt_runtime_config(
            settings(values),
            environment=ApplicationEnvironment.PRODUCTION,
            provider_adapter_available=True,
        )
        self.assertIs(ReceiptMode.PRODUCTION, config.mode)


if __name__ == "__main__":
    unittest.main()
