import unittest
from types import SimpleNamespace
from unittest.mock import patch

from storage.runtime_config import SettingsReader
from storage.website_purchase_runtime import (
    WebsitePurchaseRuntimeConfigurationError,
    build_website_purchase_runtime,
    website_purchase_enabled,
)


def settings(values: dict[str, str]) -> SettingsReader:
    return SettingsReader(values.__getitem__, values.get)


COMPLETE_CONFIG = {
    "APP_ENV": "production",
    "WEBSITE_PURCHASE_BASE_URL": "https://payments.traineros.ru",
    "TELEGRAM_BOT_URL": "https://t.me/GritJBot",
    "TELEGRAM_OIDC_CLIENT_ID": "test-client-id",
    "TELEGRAM_OIDC_CLIENT_SECRET": "test-secret",
    "PRODAMUS_PAYFORM_URL": "https://trainer.payform.ru",
    "PRODAMUS_SECRET_KEY": "test-provider-secret",
    "PRODAMUS_NPD_ENABLED": "true",
}


class WebsitePurchaseRuntimeTests(unittest.TestCase):
    def test_invalid_enabled_config_is_rejected_before_opening_database(self):
        from storage.payment_runtime import PaymentMode, PaymentProviderName
        from storage.runtime_config import ApplicationEnvironment
        from storage.payment_webhook_http import (
            build_deployed_payment_webhook_runtime, PaymentWebhookHttpConfigurationError,
        )
        role = SimpleNamespace(environment=ApplicationEnvironment.PRODUCTION, database_path="unused")
        payment = SimpleNamespace(provider=PaymentProviderName.PAYANYWAY, mode=PaymentMode.PRODUCTION)
        with (
            patch("storage.payment_webhook_http.build_webhook_role_config", return_value=role),
            patch("storage.payment_webhook_http.read_payment_mode", return_value=PaymentMode.PRODUCTION),
            patch("storage.payment_webhook_http.build_payment_runtime", return_value=payment),
            patch("storage.payment_webhook_http.build_receipt_runtime_config", return_value=SimpleNamespace(provider_code="payanyway")),
            patch("storage.payment_webhook_http.initialize_database_runtime") as database,
        ):
            with self.assertRaises(PaymentWebhookHttpConfigurationError):
                build_deployed_payment_webhook_runtime(
                    settings({"WEBSITE_PURCHASE_ENABLED": "true"}),
                    expected_environment=ApplicationEnvironment.PRODUCTION,
                )
        database.assert_not_called()

    def test_checkout_is_disabled_until_explicitly_enabled(self) -> None:
        self.assertFalse(website_purchase_enabled(settings({})))
        self.assertFalse(
            website_purchase_enabled(settings({"WEBSITE_PURCHASE_ENABLED": "false"}))
        )

    def test_invalid_enabled_value_is_rejected(self) -> None:
        with self.assertRaises(WebsitePurchaseRuntimeConfigurationError):
            website_purchase_enabled(settings({"WEBSITE_PURCHASE_ENABLED": "yes"}))

    def test_enabled_checkout_fails_closed_without_all_server_values(self) -> None:
        with self.assertRaises(WebsitePurchaseRuntimeConfigurationError):
            build_website_purchase_runtime(
                settings(
                    {
                        "WEBSITE_PURCHASE_BASE_URL": "https://payments.traineros.ru",
                        "TELEGRAM_BOT_URL": "https://t.me/GritJBot",
                    }
                )
            )

    def test_enabled_checkout_accepts_complete_https_server_config(self) -> None:
        runtime = build_website_purchase_runtime(
            settings(
                {
                    "WEBSITE_PURCHASE_BASE_URL": "https://payments.traineros.ru",
                    "TELEGRAM_BOT_URL": "https://t.me/GritJBot",
                    "TELEGRAM_OIDC_CLIENT_ID": "test-client-id",
                    "TELEGRAM_OIDC_CLIENT_SECRET": "test-secret",
                    "PRODAMUS_PAYFORM_URL": "https://payform.prodamus.ru/test",
                    "PRODAMUS_SECRET_KEY": "test-provider-secret",
                }
            )
        )
        self.assertEqual("https://payments.traineros.ru", runtime.base_url)
        self.assertEqual("https://t.me/GritJBot", runtime.telegram_bot_url)

    def test_every_required_production_value_fails_closed_when_missing(self):
        for name in COMPLETE_CONFIG:
            if name == "APP_ENV":
                continue
            values = dict(COMPLETE_CONFIG)
            del values[name]
            with self.subTest(name=name), self.assertRaises(WebsitePurchaseRuntimeConfigurationError):
                build_website_purchase_runtime(settings(values))

    def test_missing_configuration_does_not_echo_secret_or_provider_response(self):
        values = dict(COMPLETE_CONFIG, PRODAMUS_PAYFORM_URL="not-a-url-secret-marker")
        try:
            build_website_purchase_runtime(settings(values))
        except WebsitePurchaseRuntimeConfigurationError as error:
            self.assertNotIn("secret-marker", str(error))
            self.assertTrue(error.__suppress_context__)

    def test_unknown_provider_bad_origins_and_unconfirmed_npd_rejected(self):
        for name, value in (
            ("WEBSITE_PAYMENT_PROVIDER", "unknown"),
            ("WEBSITE_PURCHASE_BASE_URL", "http://payments.traineros.ru"),
            ("WEBSITE_PURCHASE_BASE_URL", "https://payments.traineros.ru/nested"),
            ("WEBSITE_PURCHASE_BASE_URL", "https://payments.traineros.ru?secret=example"),
            ("PRODAMUS_PAYFORM_URL", "https://trainer.payform.ru#bad"),
            ("PRODAMUS_NPD_ENABLED", "false"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(WebsitePurchaseRuntimeConfigurationError):
                build_website_purchase_runtime(settings(dict(COMPLETE_CONFIG, **{name: value})))

    def test_secret_dataclass_representations_are_redacted(self):
        from services.prodamus import ProdamusConfig
        from services.telegram_oidc import TelegramOidcConfig
        secret = "fixture-secret-not-production"
        url = "https://example.invalid"
        self.assertNotIn(secret, repr(ProdamusConfig(url, secret, url, url, url, url)))
        self.assertNotIn(secret, repr(TelegramOidcConfig("client", secret, url)))
