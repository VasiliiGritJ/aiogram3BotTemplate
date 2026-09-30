import unittest

from storage.runtime_config import SettingsReader
from storage.website_purchase_runtime import (
    WebsitePurchaseRuntimeConfigurationError,
    build_website_purchase_runtime,
    website_purchase_enabled,
)


def settings(values: dict[str, str]) -> SettingsReader:
    return SettingsReader(values.__getitem__, values.get)


class WebsitePurchaseRuntimeTests(unittest.TestCase):
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
