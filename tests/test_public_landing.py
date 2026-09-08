"""Static landing and production publication-gate tests without a web server."""

from html.parser import HTMLParser
from pathlib import Path
import unittest

from landing.render import render_production_landing
from landing.site_config import LandingConfigurationError, build_landing_public_config


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing" / "index.html"
STYLES = ROOT / "landing" / "styles.css"
SCRIPT = ROOT / "landing" / "app.js"
HERO_ASSET = ROOT / "landing" / "assets" / "trainer-statue.png"


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
        self.assertIn("Без карты · без автопродления", page)
        self.assertIn("ПОПРОБОВАТЬ БЕСПЛАТНО — 3 ДНЯ", page)
        self.assertIn("Без карты", page)
        self.assertIn("затем 499 ₽ / 30 дней", page.lower())
        self.assertIn("не превращается в платную подписку автоматически", page)
        self.assertIn("TRAINER.OS / UPDATE FEED", page)
        self.assertIn("https://t.me/traineros", page)
        self.assertIn("Обновления продукта · тренировки · разработка · мемы", page)
        self.assertIn('class="hero-community-link" href="https://t.me/traineros"', page)
        self.assertLess(page.index('class="hero-community-link"'), page.index('class="hero-trust"'))
        self.assertLess(page.index('ПОПРОБОВАТЬ БЕСПЛАТНО — 3 ДНЯ'), page.index('class="hero-community-link"'))
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
            "одна персональная тренировка может стоить дороже",
            "сопоставимо с одним обычным походом в кофейню",
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
        self.assertIn("mirror-chrome", page)
        self.assertIn("--chrome", styles)
        self.assertIn("--ice", styles)
        self.assertIn("--lime", styles)
        self.assertIn("--display", styles)
        self.assertIn("Arial Black", styles)
        self.assertIn("pixel-break", styles)
        self.assertIn("scanline", styles)
        self.assertIn("decay-in", styles)
        self.assertIn("tab-scan", styles)
        self.assertIn("prefers-reduced-motion", styles)
        self.assertIn("trainingStates", script)
        self.assertIn("data-training-tab", script)
        self.assertIn("data-environment-mode", script)
        self.assertIn("data-chaos-toggle", script)
        self.assertIn("os-cursor", script)
        self.assertIn("prefers-reduced-motion", script)

    def test_final_free_trial_conversion_and_copy_freeze(self) -> None:
        page = LANDING.read_text(encoding="utf-8")

        for phrase in (
            "ПОПРОБОВАТЬ БЕСПЛАТНО — 3 ДНЯ",
            "Без карты · без автопродления",
            "Затем 499 ₽ / 30 дней",
            "3 дня знакомства с системой",
            "Подойдёт — продолжишь за 499 ₽ / 30 дней",
            "Не вместо хорошего тренера",
            "а вместо ситуации, когда ты остаёшься без плана",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, page)

        class MarketingCopyParser(HTMLParser):
            checked_tags = {"h1", "h2", "h3", "p", "strong", "span", "small", "li"}

            def __init__(self):
                super().__init__()
                self.current_tag = None
                self.in_legal = False
                self.trailing_periods = []

            def handle_starttag(self, tag, attrs):
                if dict(attrs).get("id") == "legal":
                    self.in_legal = True
                self.current_tag = tag

            def handle_data(self, data):
                text = data.strip()
                if (
                    not self.in_legal
                    and self.current_tag in self.checked_tags
                    and text.endswith(".")
                ):
                    self.trailing_periods.append(text)

        parser = MarketingCopyParser()
        parser.feed(page)
        self.assertEqual([], parser.trailing_periods)

    def test_primary_actions_use_lime_while_system_controls_remain_chrome(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")

        self.assertGreaterEqual(page.count('class="button primary"'), 3)
        self.assertIn(".button.primary", styles)
        self.assertIn("var(--lime) 45%", styles)
        self.assertIn("color: #111b00", styles)
        self.assertIn(".header-cta", styles)
        self.assertIn("#dfffa0", styles)
        self.assertIn(".button.ghost", styles)
        self.assertIn(".chaos-trigger", styles)
        self.assertIn("color: #edf6ff", styles)

    def test_approved_statue_asset_is_decorative_and_integrated_with_the_hero(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertTrue(HERO_ASSET.is_file())
        self.assertEqual(b"\x89PNG\r\n\x1a\n", HERO_ASSET.read_bytes()[:8])
        self.assertIn('src="assets/trainer-statue.png"', page)
        self.assertIn('width="2448" height="3264" alt=""', page)
        self.assertIn('class="hero-statue" aria-hidden="true"', page)
        self.assertIn("statue-data-link", page)
        self.assertIn("statue-decay", page)
        self.assertIn("marble-dust", page)
        self.assertIn("pixel-decay", page)
        self.assertIn("geometry-drift", page)
        self.assertIn("data-fragments", page)
        self.assertIn(".hero-statue", styles)
        self.assertIn("statue-reveal", styles)
        self.assertIn("data-pulse", styles)
        self.assertIn("marble-dust-drift", styles)
        self.assertIn("edge-rgb-instability", styles)
        self.assertIn("fragment-shimmer", styles)
        self.assertIn("dust-out-a", styles)
        self.assertIn("pixel-drift", styles)
        self.assertIn("geometry-float", styles)
        self.assertIn("edge-slice", styles)
        self.assertIn("corruption-pulse", styles)
        self.assertIn(".statue-data-link", styles)
        self.assertIn("hero-statue", script)
        self.assertIn("prefers-reduced-motion", script)

    def test_value_proof_makes_the_fixed_offer_and_trust_terms_prominent(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")

        self.assertIn('class="value-proof"', page)
        daily_start = page.index('<div class="value-proof">')
        daily_end = page.index('<div class="comparison-trust">', daily_start)
        daily_block = page[daily_start:daily_end]
        comparison_start = page.index('<div class="price-compare">')
        comparison_end = page.index('<div class="value-proof">', comparison_start)
        comparison_block = page[comparison_start:comparison_end]

        self.assertIn("≈ 16,6 ₽", daily_block)
        self.assertIn("Чтобы каждый раз знать, что делать дальше", daily_block)
        self.assertNotIn("кофейн", daily_block.lower())
        self.assertIn("одна персональная тренировка может стоить дороже", comparison_block)
        self.assertIn("30 дней — 499 ₽", comparison_block)
        self.assertIn("499 ₽ за 30 дней", comparison_block)
        self.assertIn("сопоставимо с одним обычным походом в кофейню", comparison_block)
        self.assertIn("Без карты · без автопродления", page)
        self.assertIn("чек", page.lower())
        self.assertIn(".value-proof", styles)
        self.assertIn(".proof-icon", styles)
        self.assertIn(".price-compare .current p", styles)
        self.assertIn(".trial-value-strip", styles)
        self.assertIn(".comparison-trust", styles)
        self.assertIn(".community-link", styles)
        self.assertIn("chrome-sweep", styles)

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
