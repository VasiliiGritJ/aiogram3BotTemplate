"""Static landing and production publication-gate tests without a web server."""

from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from landing.build import LandingBuildError, build_production_artifact
from landing.render import render_production_landing
from landing.site_config import (
    OFFICIAL_TELEGRAM_BOT_URL,
    OFFICIAL_TELEGRAM_CHANNEL_URL,
    LandingConfigurationError,
    build_landing_public_config,
)


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing" / "index.html"
STYLES = ROOT / "landing" / "styles.css"
SCRIPT = ROOT / "landing" / "app.js"
HERO_ASSET = ROOT / "landing" / "assets" / "trainer-statue.png"


class PublicLandingTests(unittest.TestCase):
    @staticmethod
    def values(mapping: dict[str, str]):
        return mapping.get

    @staticmethod
    def production_values() -> dict[str, str]:
        return {
            "PUBLIC_SITE_URL": "https://trainer.example",
            "TELEGRAM_BOT_URL": OFFICIAL_TELEGRAM_BOT_URL,
            "TELEGRAM_CHANNEL_URL": OFFICIAL_TELEGRAM_CHANNEL_URL,
            "TERMS_URL": "https://trainer.example/#terms",
            "PRIVACY_URL": "https://trainer.example/#privacy",
            "REFUND_URL": "https://trainer.example/#refund",
            "SELLER_FULL_NAME": "Иванов Иван Иванович",
            "SELLER_INN": "123456789012",
            "SUPPORT_EMAIL": "support@example.invalid",
            "WEBSITE_PURCHASE_URL": "https://payments.traineros.ru/buy",
        }

    def test_page_contains_frozen_offer_cta_and_mobile_metadata(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        self.assertIn('<meta name="viewport"', page)
        self.assertIn("Мой AI Тренер", page)
        self.assertIn("499 ₽", page)
        self.assertIn("30 дней", page)
        self.assertIn("Без карты · без автопродления", page)
        self.assertIn("ПОПРОБОВАТЬ БЕСПЛАТНО — 3 ДНЯ", page)
        self.assertIn("Купить доступ - 499 ₽", page)
        self.assertIn('data-purchase-cta href="#website-purchase"', page)
        self.assertIn("Без карты", page)
        self.assertIn("3 дня / 72 часа", page)
        self.assertIn("затем 499 ₽ / 30 дней", page.lower())
        self.assertIn("не превращается в платную подписку автоматически", page)
        self.assertIn("TELEGRAM / TRAINER.OS ↗", page)
        self.assertIn("https://t.me/traineros", page)
        self.assertIn("обновления · тренировки · мемы · что нового", page)
        self.assertIn('class="hero-community-link" href="https://t.me/traineros"', page)
        self.assertIn('class="telegram-plane"', page)
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

    def test_responsive_hero_keeps_the_statue_in_its_own_first_viewport_layer(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")

        # The original desktop composition stays untouched; responsive overrides
        # begin only below the desktop breakpoint.
        self.assertIn(
            ".hero-statue { position: absolute; z-index: 0; top: -94px;",
            styles,
        )
        self.assertIn("@media (max-width: 1100px)", styles)
        self.assertIn(
            ".hero { grid-template-columns: 1fr; min-height: 0; padding-top: 68px; overflow: clip; }",
            styles,
        )

        # Tablet art is top-anchored in the hero instead of being pushed below
        # the normal-flow training-console card.
        self.assertIn("@media (min-width: 641px) and (max-width: 1100px)", styles)
        self.assertIn(
            ".hero-statue { top: clamp(160px, 21vw, 240px);",
            styles,
        )
        self.assertIn(
            ".statue-decay { top: clamp(205px, 25vw, 300px);",
            styles,
        )

        # Mobile deliberately recomposes the existing artwork into the reading
        # flow: headline, artwork, then the offer. The single statue element is
        # reused; tablet and desktop keep their absolute-art composition.
        self.assertIn(
            ".hero { display: flex; flex-direction: column; align-items: stretch; gap: 0; }",
            styles,
        )
        self.assertIn(
            ".hero-copy { display: contents; }",
            styles,
        )
        self.assertIn(
            ".system-label { position: relative; z-index: 4; order: 1; }",
            styles,
        )
        self.assertIn(
            ".hero-copy h1 { position: relative; z-index: 4; order: 2;",
            styles,
        )
        self.assertIn(
            ".hero-statue { position: relative; z-index: 3; order: 3; top: auto; right: auto; bottom: auto;",
            styles,
        )
        self.assertIn(
            ".hero-lead { position: relative; z-index: 4; order: 4;",
            styles,
        )
        self.assertIn(
            ".hero-actions { position: relative; z-index: 4; order: 5; }",
            styles,
        )
        self.assertIn(".statue-decay, .statue-data-link { display: none; }", styles)

        # Content and the interactive Trainer OS remain above the decorative
        # layer, and the document order keeps the art inside the hero before
        # the console rather than moving it with the console.
        self.assertIn(".hero-copy { position: relative; z-index: 4; }", styles)
        self.assertIn(".hero-machine { z-index: 2;", styles)
        self.assertLess(
            page.index('class="hero-statue"'),
            page.index('class="hero-machine os-window"'),
        )
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)

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
        self.assertIn(".legal-grid #safety { grid-column: 1 / -1; }", styles)
        self.assertIn("chrome-sweep", styles)

    def test_required_legal_sections_and_health_disclaimer_are_reachable(self) -> None:
        page = LANDING.read_text(encoding="utf-8")
        for section in ("terms", "privacy", "refund", "seller", "support"):
            with self.subTest(section=section):
                self.assertIn(f'id="{section}"', page)
                target = {"terms": "offer.html", "privacy": "privacy.html"}.get(section, f"#{section}")
                self.assertIn(f'href="{target}"', page)
        self.assertIn('id="payment"', page)
        self.assertIn("не является медицинской помощью", page)
        self.assertIn("Данные банковских карт обрабатывает платёжный провайдер", page)
        self.assertIn("Доступ открывается только после подтверждения оплаты", page)

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
        valid = self.production_values()
        required = dict(valid)
        required.pop("WEBSITE_PURCHASE_URL")
        for name in required:
            with self.subTest(name=name):
                values = dict(valid)
                values.pop(name)
                with self.assertRaises(LandingConfigurationError) as error:
                    build_landing_public_config(self.values(values), production=True)
                self.assertIn(name, str(error.exception))
                self.assertNotIn("support@example.invalid", str(error.exception))

    def test_production_config_accepts_only_valid_public_details(self) -> None:
        valid = self.production_values()
        valid["SUPPORT_EMAIL"] = "Support@Example.Invalid"
        config = build_landing_public_config(self.values(valid), production=True)
        self.assertTrue(config.production_ready)
        self.assertEqual(OFFICIAL_TELEGRAM_BOT_URL, config.telegram_bot_url)
        self.assertEqual(OFFICIAL_TELEGRAM_CHANNEL_URL, config.telegram_channel_url)
        self.assertEqual("https://trainer.example/offer.html", config.terms_url)
        self.assertEqual("https://trainer.example/privacy.html", config.privacy_url)
        self.assertEqual("https://trainer.example/#refund", config.refund_url)
        self.assertEqual("support@example.invalid", config.support_email)
        for name, value in (
            ("PUBLIC_SITE_URL", "https://trainer.example/landing"),
            ("TELEGRAM_BOT_URL", "https://t.me/other_bot"),
            ("TELEGRAM_CHANNEL_URL", "https://t.me/other_channel"),
            ("TERMS_URL", "https://trainer.example/terms"),
            ("PRIVACY_URL", "https://legal.example/#privacy"),
            ("REFUND_URL", "https://trainer.example/#terms"),
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
            self.values(self.production_values()),
            production=True,
        )
        page = render_production_landing(config)
        self.assertIn(f'data-telegram-url="{OFFICIAL_TELEGRAM_BOT_URL}"', page)
        self.assertIn(f'href="{OFFICIAL_TELEGRAM_BOT_URL}"', page)
        self.assertNotIn('href="#telegram-launch"', page)
        self.assertIn(f'href="{OFFICIAL_TELEGRAM_CHANNEL_URL}"', page)
        self.assertIn('href="https://payments.traineros.ru/buy"', page)
        self.assertIn('<link rel="canonical" href="https://trainer.example">', page)
        self.assertIn('<meta property="og:url" content="https://trainer.example">', page)
        self.assertIn('href="https://trainer.example/offer.html"', page)
        self.assertIn('href="https://trainer.example/privacy.html"', page)
        self.assertIn('href="https://trainer.example/#refund"', page)
        self.assertIn('href="https://trainer.example/#payment"', page)
        self.assertIn('href="https://trainer.example/#seller"', page)
        self.assertIn('href="https://trainer.example/#support"', page)
        self.assertIn('content="index, follow"', page)
        self.assertIn("ПУБЛИЧНАЯ ССЫЛКА НА БОТА АКТИВНА", page)
        self.assertIn("Иванов Иван Иванович", page)
        self.assertIn("123456789012", page)
        self.assertIn("Контакт: support@example.invalid.", page)
        self.assertIn(
            "По вопросам доступа, оплаты, чека или возврата: support@example.invalid.",
            page,
        )
        self.assertNotIn(
            "используйте контакт поддержки, опубликованный в production-версии сайта",
            page,
        )
        self.assertNotIn("YOOKASSA_SECRET", page)

    def test_production_hides_pending_website_purchase_cta_until_endpoint_is_live(self) -> None:
        values = self.production_values()
        values.pop("WEBSITE_PURCHASE_URL")
        config = build_landing_public_config(self.values(values), production=True)
        self.assertTrue(config.production_ready)
        self.assertIsNone(config.website_purchase_url)
        page = render_production_landing(config)
        self.assertNotIn("data-purchase-cta", page)
        self.assertNotIn('href="#website-purchase"', page)
        self.assertIn(f'href="{OFFICIAL_TELEGRAM_BOT_URL}"', page)

    def test_footer_legal_links_always_have_real_targets(self) -> None:
        config = build_landing_public_config(
            self.values(self.production_values()),
            production=True,
        )
        page = render_production_landing(config)

        class LinkParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.ids: set[str] = set()
                self.links: list[tuple[str, str]] = []
                self.in_footer = False

            def handle_starttag(self, tag, attrs) -> None:
                values = dict(attrs)
                if values.get("id"):
                    self.ids.add(values["id"])
                if tag == "footer":
                    self.in_footer = True
                if tag == "a" and self.in_footer:
                    self.links.append((values.get("href", ""), values.get("class", "")))

            def handle_endtag(self, tag) -> None:
                if tag == "footer":
                    self.in_footer = False

        parser = LinkParser()
        parser.feed(page)
        self.assertGreater(len(parser.links), 1)
        for href, _ in parser.links:
            with self.subTest(href=href):
                self.assertTrue(href)
                self.assertNotIn(href, {"#", "javascript:void(0)"})
                if href.startswith("#"):
                    self.assertIn(href[1:], parser.ids)

        footer_targets = {href for href, _ in parser.links}
        for target in (
            "https://trainer.example/offer.html",
            "https://trainer.example/privacy.html",
            "https://trainer.example/#refund",
            "https://trainer.example/#payment",
            "https://trainer.example/#seller",
            "https://trainer.example/#support",
        ):
            self.assertIn(target, footer_targets)

    def test_production_build_creates_complete_relative_static_artifact(self) -> None:
        config = build_landing_public_config(
            self.values(self.production_values()),
            production=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trainer-os-public"
            result = build_production_artifact(config, output)

            self.assertEqual(output, result)
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "styles.css").is_file())
            self.assertTrue((output / "app.js").is_file())
            self.assertEqual(
                HERO_ASSET.read_bytes(),
                (output / "assets" / "trainer-statue.png").read_bytes(),
            )
            page = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="styles.css"', page)
            self.assertIn('src="app.js"', page)
            self.assertIn('src="assets/trainer-statue.png"', page)
            self.assertIn('<link rel="icon" href="data:image/svg+xml,', page)
            self.assertIn('<meta property="og:url" content="https://trainer.example">', page)
            self.assertNotIn("file://", page)
            self.assertNotIn("localhost", page)
            self.assertNotIn("noindex, nofollow", page)
            self.assertNotIn("<!-- canonical-url -->", page)
            self.assertNotIn("ПУБЛИЧНАЯ ССЫЛКА НА БОТА ПОДКЛЮЧАЕТСЯ", page)
            with self.assertRaises(LandingBuildError):
                build_production_artifact(config, output)

    def test_production_render_rejects_incomplete_config(self) -> None:
        with self.assertRaises(ValueError):
            render_production_landing(build_landing_public_config(self.values({}), production=False))


if __name__ == "__main__":
    unittest.main()
