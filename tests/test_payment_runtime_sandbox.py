"""Tests for fail-closed, test-only payment runtime composition."""

import unittest
from unittest.mock import patch

from storage.payment_runtime import (
    PaymentMode,
    PaymentProviderName,
    PaymentRuntimeConfigurationError,
    build_payment_runtime,
)


VALID_SETTINGS = {
    "PAYMENTS_TEST_MODE": "true",
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

        self.assertEqual(49_900, runtime.product.amount_minor)
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
                if expected_mode is PaymentMode.PRODUCTION:
                    settings.update({
                        "PAYMENT_PROVIDER": "yookassa",
                        "PUBLIC_SITE_URL": "https://example.invalid",
                        "TERMS_URL": "https://example.invalid/terms",
                        "PRIVACY_URL": "https://example.invalid/privacy",
                        "REFUND_URL": "https://example.invalid/refund",
                        "SUPPORT_EMAIL": "support@example.invalid",
                    })
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

    def test_return_url_is_rejected_without_fallback(self) -> None:
        invalid_settings = (
            {"YOOKASSA_RETURN_URL": "not-a-url"},
            {"YOOKASSA_RETURN_URL": "ftp://example.invalid/return"},
        )
        for override in invalid_settings:
            with self.subTest(override=override):
                settings = {**VALID_SETTINGS, **override}
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payment_runtime(self.reader(settings))

    def test_server_owned_launch_product_cannot_be_repriced_by_environment(self) -> None:
        settings = {
            **VALID_SETTINGS,
            "SUBSCRIPTION_AMOUNT_MINOR": "1",
            "SUBSCRIPTION_CURRENCY": "USD",
        }
        with patch("storage.payment_runtime.YooKassaProvider"):
            runtime = build_payment_runtime(self.reader(settings))
        self.assertEqual(49_900, runtime.product.amount_minor)
        self.assertEqual("RUB", runtime.product.currency)
        self.assertEqual(30, runtime.product.period_days)

    def test_test_mode_tolerates_missing_public_commercial_values(self) -> None:
        with patch("storage.payment_runtime.YooKassaProvider"):
            runtime = build_payment_runtime(self.reader(VALID_SETTINGS))
        self.assertIsNone(runtime.commercial_public.public_site_url)
        self.assertIsNone(runtime.commercial_public.terms_url)
        self.assertIsNone(runtime.commercial_public.privacy_url)
        self.assertIsNone(runtime.commercial_public.refund_url)
        self.assertIsNone(runtime.commercial_public.support_email)

    def test_production_missing_public_commercial_values_fail_before_provider(self) -> None:
        valid_public = {
            "PUBLIC_SITE_URL": "https://example.invalid",
            "TERMS_URL": "https://example.invalid/terms",
            "PRIVACY_URL": "https://example.invalid/privacy",
            "REFUND_URL": "https://example.invalid/refund",
            "SUPPORT_EMAIL": "support@example.invalid",
        }
        production = {
            **VALID_SETTINGS,
            **valid_public,
            "PAYMENTS_MODE": "production",
            "PAYMENTS_TEST_MODE": "false",
            "PAYMENT_PROVIDER": "yookassa",
        }
        required_public_names = (
            "PUBLIC_SITE_URL",
            "TERMS_URL",
            "PRIVACY_URL",
            "REFUND_URL",
            "SUPPORT_EMAIL",
        )
        for name in required_public_names:
            with self.subTest(name=name), patch(
                "storage.payment_runtime.YooKassaProvider"
            ) as provider_class:
                settings = dict(production)
                settings.pop(name)
                with self.assertRaises(PaymentRuntimeConfigurationError) as error:
                    build_payment_runtime(self.reader(settings))
                self.assertIn(name, str(error.exception))
                self.assertNotIn(VALID_SETTINGS["YOOKASSA_SECRET_TOKEN"], str(error.exception))
                provider_class.assert_not_called()

    def test_production_rejects_malformed_or_non_https_public_values(self) -> None:
        valid_public = {
            "PUBLIC_SITE_URL": "https://example.invalid",
            "TERMS_URL": "https://example.invalid/terms",
            "PRIVACY_URL": "https://example.invalid/privacy",
            "REFUND_URL": "https://example.invalid/refund",
            "SUPPORT_EMAIL": "support@example.invalid",
        }
        invalid_values = (
            ("PUBLIC_SITE_URL", "http://example.invalid"),
            ("TERMS_URL", "ftp://example.invalid/terms"),
            ("PRIVACY_URL", "https://user:password@example.invalid/privacy"),
            ("REFUND_URL", "not a url"),
            ("TERMS_URL", "https://example.invalid/terms\nunsafe"),
            ("SUPPORT_EMAIL", "not an email"),
        )
        for name, value in invalid_values:
            with self.subTest(name=name), patch("storage.payment_runtime.YooKassaProvider") as provider_class:
                settings = {
                    **VALID_SETTINGS,
                    **valid_public,
                    "PAYMENTS_MODE": "production",
                    "PAYMENTS_TEST_MODE": "false",
                    "PAYMENT_PROVIDER": "yookassa",
                    name: value,
                }
                with self.assertRaises(PaymentRuntimeConfigurationError) as error:
                    build_payment_runtime(self.reader(settings))
                self.assertIn(name, str(error.exception))
                self.assertNotIn(VALID_SETTINGS["YOOKASSA_SECRET_TOKEN"], str(error.exception))
                provider_class.assert_not_called()

    def test_valid_production_commercial_values_are_wired_without_secret_exposure(self) -> None:
        settings = {
            **VALID_SETTINGS,
            "PAYMENTS_MODE": "production",
            "PAYMENTS_TEST_MODE": "false",
            "PAYMENT_PROVIDER": "yookassa",
            "PUBLIC_SITE_URL": "https://example.invalid",
            "TERMS_URL": "https://example.invalid/terms",
            "PRIVACY_URL": "https://example.invalid/privacy",
            "REFUND_URL": "https://example.invalid/refund",
            "SUPPORT_EMAIL": "Support@Example.Invalid",
        }
        with patch("storage.payment_runtime.YooKassaProvider") as provider_class:
            runtime = build_payment_runtime(self.reader(settings))
        self.assertFalse(provider_class.call_args.kwargs["expected_test_mode"])
        self.assertEqual("https://example.invalid", runtime.commercial_public.public_site_url)
        self.assertEqual("https://example.invalid/terms", runtime.commercial_public.terms_url)
        self.assertEqual("support@example.invalid", runtime.commercial_public.support_email)

    def test_production_provider_is_explicit_and_payanyway_uses_same_ui_runtime(self) -> None:
        commercial = {
            "PUBLIC_SITE_URL": "https://www.traineros.ru",
            "TERMS_URL": "https://www.traineros.ru/#terms",
            "PRIVACY_URL": "https://www.traineros.ru/#privacy",
            "REFUND_URL": "https://www.traineros.ru/#refund",
            "SUPPORT_EMAIL": "support@example.invalid",
        }
        values = {
            **commercial,
            "PAYMENTS_MODE": "production",
            "PAYMENTS_TEST_MODE": "false",
            "PAYMENT_PROVIDER": "payanyway",
            "PAYANYWAY_MNT_ID": "12345678",
            "PAYANYWAY_INTEGRITY_CODE": "fixture-secret",
            "PAYANYWAY_SUCCESS_URL": "https://www.traineros.ru/?payment=success#payment",
            "PAYANYWAY_FAIL_URL": "https://www.traineros.ru/?payment=fail#payment",
            "PAYANYWAY_RETURN_URL": "https://www.traineros.ru/#payment",
        }
        with patch("storage.payment_runtime.PayAnyWayService") as service_class:
            runtime = build_payment_runtime(self.reader(values))

        self.assertIs(PaymentProviderName.PAYANYWAY, runtime.provider)
        self.assertIs(service_class.return_value, runtime.payment_service)
        self.assertFalse(service_class.call_args.args[0].test_mode)

        values.pop("PAYMENT_PROVIDER")
        with self.assertRaises(PaymentRuntimeConfigurationError):
            build_payment_runtime(self.reader(values))

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
