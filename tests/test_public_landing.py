"""Static landing and production publication-gate tests without a web server."""

from pathlib import Path
import unittest

from landing.render import render_production_landing
from landing.site_config import LandingConfigurationError, build_landing_public_config


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing" / "index.html"
STYLES = ROOT / "landing" / "styles.css"
SCRIPT = ROOT / "landing" / "app.js"


class PublicLandingTests(unittest.TestCase):
    @staticmethod
    def values(mapping: dict[str, str]):
        return mapping.get

    def test_page_contains_frozen_offer_cta_and_mobile_metadata(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        self.assertIn('<meta name="viewport"', page)
        self.assertIn("Мой AI Тренер", page)
        self.assertIn("499 ₽", page)
        self.assertIn("30 дней", page)
        self.assertIn("Автопродления нет", page)
        self.assertIn("Попробовать в Telegram", page)
        self.assertIn('href="styles.css"', page)
        self.assertIn('src="app.js"', page)
        self.assertTrue(STYLES.is_file())
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue((ROOT / "landing" / "validate.py").is_file())

    def test_trainer_os_y2k_redesign_keeps_offer_and_accessible_interaction(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")

        for phrase in (
            "TRAINER.OS / ONLINE",
            "Тренируйся",
            "Не просто список упражнений",
            "499 ₽ не покупают мотивацию",
            "одна сессия может стоить больше",
            "Один обычный поход в кофейню может стоить сопоставимо",
            "≈ 16,6 ₽ / DAY",
            "не является медицинской помощью",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, page)

        for environment in ("gym", "functional", "street", "home"):
            with self.subTest(environment=environment):
                self.assertIn(f'data-training-tab="{environment}"', page)
                self.assertIn(f'data-environment-mode="{environment}"', page)

        self.assertIn("MY_AI_TRAINER.EXE", page)
        self.assertIn("window-controls", page)
        self.assertIn("pixel-shards", page)
        self.assertIn("data-chaos-system", page)
        self.assertIn("data-chaos-toggle", page)
        self.assertIn("os-cursor", page)
        self.assertIn("data-reactive", page)
        self.assertIn("data-glitch", page)
        self.assertIn("--chrome", styles)
        self.assertIn("--ice", styles)
        self.assertIn("--lime", styles)
        self.assertIn("--display", styles)
        self.assertIn("Arial Black", styles)
        self.assertIn("pixel-break", styles)
        self.assertIn("scanline", styles)
        self.assertIn("decay-in", styles)
        self.assertIn("prefers-reduced-motion", styles)
        self.assertIn("trainingStates", script)
        self.assertIn("data-training-tab", script)
        self.assertIn("data-environment-mode", script)
        self.assertIn("data-chaos-toggle", script)
        self.assertIn("os-cursor", script)
        self.assertIn("prefers-reduced-motion", script)

    def test_required_legal_sections_and_health_disclaimer_are_reachable(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        for section in ("terms", "privacy", "refund", "seller", "support"):
            with self.subTest(section=section):
                self.assertIn(f'id="{section}"', page)
                self.assertIn(f'href="#{section}"', page)
        self.assertIn("не является медицинской помощью", page)
        self.assertIn("Данные банковских карт обрабатывает платёжный провайдер", page)

    def test_no_payment_secret_or_real_seller_details_are_embedded(self) -> None:
        page = LANDING.read_text(encoding="utf-8").lower()
        for forbidden in ("yookassa_secret", "bot_token", "authorization:", "seller_inn="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)
        self.assertIn("не считается готовой к коммерческой публикации", page)

    def test_development_landing_config_allows_absent_public_seller_details(self) -> None:
        config = build_landing_public_config(self.values({}), production=False)
        self.assertFalse(config.production_ready)
        self.assertIsNone(config.seller_full_name)

    def test_production_config_rejects_each_missing_required_public_value(self) -> None:
        valid = {
            "PUBLIC_SITE_URL": "https://trainer.example",
            "TELEGRAM_BOT_URL": "https://t.me/trainer_example_bot",
            "SELLER_FULL_NAME": "Иванов Иван Иванович",
            "SELLER_INN": "123456789012",
            "SUPPORT_EMAIL": "support@example.invalid",
        }
        for name in valid:
            with self.subTest(name=name):
                values = dict(valid)
                values.pop(name)
                with self.assertRaises(LandingConfigurationError) as error:
                    build_landing_public_config(self.values(values), production=True)
                self.assertIn(name, str(error.exception))
                self.assertNotIn("support@example.invalid", str(error.exception))

    def test_production_config_accepts_only_valid_public_details(self) -> None:
        valid = {
            "PUBLIC_SITE_URL": "https://trainer.example",
            "TELEGRAM_BOT_URL": "https://t.me/trainer_example_bot",
            "SELLER_FULL_NAME": "Иванов Иван Иванович",
            "SELLER_INN": "123456789012",
            "SUPPORT_EMAIL": "Support@Example.Invalid",
        }
        config = build_landing_public_config(self.values(valid), production=True)
        self.assertTrue(config.production_ready)
        self.assertEqual("support@example.invalid", config.support_email)
        for name, value in (
            ("PUBLIC_SITE_URL", "http://trainer.example"),
            ("TELEGRAM_BOT_URL", "https://example.invalid/not-a-bot"),
            ("SELLER_INN", "not-an-inn"),
            ("SUPPORT_EMAIL", "private contact@example.invalid"),
        ):
            with self.subTest(name=name):
                invalid = dict(valid)
                invalid[name] = value
                with self.assertRaises(LandingConfigurationError) as error:
                    build_landing_public_config(self.values(invalid), production=True)
                self.assertIn(name, str(error.exception))
                self.assertNotIn(value, str(error.exception))

    def test_production_render_uses_only_verified_public_values_and_activates_cta(self) -> None:
        config = build_landing_public_config(
            self.values(
                {
                    "PUBLIC_SITE_URL": "https://trainer.example",
                    "TELEGRAM_BOT_URL": "https://t.me/trainer_example_bot",
                    "SELLER_FULL_NAME": "Иванов Иван Иванович",
                    "SELLER_INN": "123456789012",
                    "SUPPORT_EMAIL": "support@example.invalid",
                }
            ),
            production=True,
        )
        page = render_production_landing(config)
        self.assertIn('data-telegram-url="https://t.me/trainer_example_bot"', page)
        self.assertIn('<link rel="canonical" href="https://trainer.example">', page)
        self.assertIn('content="index, follow"', page)
        self.assertIn("Иванов Иван Иванович", page)
        self.assertIn("123456789012", page)
        self.assertNotIn("YOOKASSA_SECRET", page)

    def test_production_render_rejects_incomplete_config(self) -> None:
        with self.assertRaises(ValueError):
            render_production_landing(build_landing_public_config(self.values({}), production=False))


if __name__ == "__main__":
    unittest.main()
