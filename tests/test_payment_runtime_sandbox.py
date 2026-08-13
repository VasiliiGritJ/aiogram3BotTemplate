"""Tests for fail-closed, test-only payment runtime composition."""

import unittest
from unittest.mock import patch

from storage.payment_runtime import (
    PaymentMode,
    PaymentRuntimeConfigurationError,
    build_payment_runtime,
)


VALID_SETTINGS = {
    "PAYMENTS_TEST_MODE": "true",
    "SUBSCRIPTION_AMOUNT_MINOR": "250",
    "SUBSCRIPTION_CURRENCY": "RUB",
    "YOOKASSA_RETURN_URL": "https://example.invalid/payment-return",
    "YOOKASSA_SHOP_ID": "test-shop-id",
    "YOOKASSA_SECRET_TOKEN": "test-secret-must-not-leak",
}


class PaymentRuntimeSandboxTests(unittest.TestCase):
    @staticmethod
    def reader(values):
        def read_required(name: str) -> str:
            return values[name]

        return read_required

    def test_explicit_true_test_mode_builds_test_only_runtime(self) -> None:
        with patch("storage.payment_runtime.YooKassaProvider") as provider_class:
            runtime = build_payment_runtime(self.reader(VALID_SETTINGS))

        self.assertEqual(250, runtime.product.amount_minor)
        self.assertEqual("RUB", runtime.product.currency)
        self.assertEqual(30, runtime.product.period_days)
        self.assertIs(PaymentMode.TEST, runtime.mode)
        self.assertTrue(provider_class.call_args.kwargs["expected_test_mode"])

    def test_missing_or_non_strict_legacy_test_mode_is_rejected(self) -> None:
        for value in (None, "", "false", "TRUE", "yes", "1"):
            with self.subTest(value=value):
                settings = dict(VALID_SETTINGS)
                if value is None:
                    settings.pop("PAYMENTS_TEST_MODE")
                else:
                    settings["PAYMENTS_TEST_MODE"] = value
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payment_runtime(self.reader(settings))

    def test_explicit_test_and_production_modes_are_wired_symmetrically(self) -> None:
        cases = (
            ("test", "true", PaymentMode.TEST, True),
            ("production", "false", PaymentMode.PRODUCTION, False),
        )
        for mode_value, legacy_value, expected_mode, expected_test in cases:
            with self.subTest(mode=mode_value):
                settings = {
                    **VALID_SETTINGS,
                    "PAYMENTS_MODE": mode_value,
                    "PAYMENTS_TEST_MODE": legacy_value,
                }
                with (
                    patch("storage.payment_runtime.YooKassaProvider") as provider_class,
                    patch("storage.payment_runtime.PaymentService") as service_class,
                ):
                    runtime = build_payment_runtime(self.reader(settings))
                self.assertIs(expected_mode, runtime.mode)
                self.assertIs(
                    expected_test,
                    provider_class.call_args.kwargs["expected_test_mode"],
                )
                self.assertIs(
                    expected_test,
                    service_class.call_args.kwargs["expected_test_mode"],
                )

    def test_invalid_or_conflicting_explicit_mode_fails_closed(self) -> None:
        cases = (
            {"PAYMENTS_MODE": "", "PAYMENTS_TEST_MODE": "true"},
            {"PAYMENTS_MODE": "   ", "PAYMENTS_TEST_MODE": "true"},
            {"PAYMENTS_MODE": "PRODUCTION", "PAYMENTS_TEST_MODE": "false"},
            {"PAYMENTS_MODE": "live", "PAYMENTS_TEST_MODE": "false"},
            {"PAYMENTS_MODE": "production", "PAYMENTS_TEST_MODE": "true"},
            {"PAYMENTS_MODE": "test", "PAYMENTS_TEST_MODE": "false"},
        )
        for override in cases:
            with self.subTest(override=override):
                settings = {**VALID_SETTINGS, **override}
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payment_runtime(self.reader(settings))

    def test_production_mode_cannot_be_enabled_by_credentials_or_legacy_false(self) -> None:
        settings = dict(VALID_SETTINGS)
        settings["PAYMENTS_TEST_MODE"] = "false"
        with self.assertRaises(PaymentRuntimeConfigurationError):
            build_payment_runtime(self.reader(settings))

    def test_all_required_payment_values_fail_closed_without_secret_leakage(self) -> None:
        required_names = (
            "SUBSCRIPTION_AMOUNT_MINOR",
            "SUBSCRIPTION_CURRENCY",
            "YOOKASSA_RETURN_URL",
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_TOKEN",
        )
        for name in required_names:
            with self.subTest(name=name):
                settings = dict(VALID_SETTINGS)
                settings.pop(name)
                with self.assertRaises(PaymentRuntimeConfigurationError) as error:
                    build_payment_runtime(self.reader(settings))
                self.assertNotIn(VALID_SETTINGS["YOOKASSA_SECRET_TOKEN"], str(error.exception))

    def test_invalid_amount_currency_and_return_url_are_rejected_without_fallback(self) -> None:
        invalid_settings = (
            {"SUBSCRIPTION_AMOUNT_MINOR": "0"},
            {"SUBSCRIPTION_AMOUNT_MINOR": "not-a-number"},
            {"SUBSCRIPTION_CURRENCY": "rub"},
            {"SUBSCRIPTION_CURRENCY": "RU"},
            {"YOOKASSA_RETURN_URL": "not-a-url"},
            {"YOOKASSA_RETURN_URL": "ftp://example.invalid/return"},
        )
        for override in invalid_settings:
            with self.subTest(override=override):
                settings = {**VALID_SETTINGS, **override}
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payment_runtime(self.reader(settings))

    def test_reader_exceptions_are_normalized_without_secret_text(self) -> None:
        def failing_reader(name: str) -> str:
            if name == "YOOKASSA_SECRET_TOKEN":
                raise RuntimeError("test-secret-must-not-leak")
            return VALID_SETTINGS[name]

        with self.assertRaises(PaymentRuntimeConfigurationError) as error:
            build_payment_runtime(failing_reader)
        self.assertNotIn("test-secret-must-not-leak", str(error.exception))


if __name__ == "__main__":
    unittest.main()
