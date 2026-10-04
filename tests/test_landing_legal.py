"""Own legal-document content, public config and complete static-link contract."""

from html.parser import HTMLParser
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urljoin, urlparse

from landing.build import build_production_artifact
from landing.render import LEGAL_DOCUMENTS, render_legal_document, render_production_landing
from landing.site_config import LandingConfigurationError, build_landing_public_config


ROOT = Path(__file__).resolve().parents[1] / "landing"


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs["href"])


class LandingLegalTests(unittest.TestCase):
    def values(self):
        return {
            "PUBLIC_SITE_URL": "https://trainer.example",
            "TELEGRAM_BOT_URL": "https://t.me/GritJBot",
            "TELEGRAM_CHANNEL_URL": "https://t.me/traineros",
            "TERMS_URL": "https://trainer.example/offer.html",
            "PRIVACY_URL": "https://trainer.example/privacy.html",
            "REFUND_URL": "https://trainer.example/#refund",
            "SELLER_FULL_NAME": "Иванов Иван Иванович",
            "SELLER_INN": "123456789012",
            "SELLER_DISCLOSURE_ADDRESS": "Тестовый город, тестовый адрес 1",
            "SUPPORT_EMAIL": "kurtbachotto@gmail.com",
        }

    def config(self, values=None):
        return build_landing_public_config((values or self.values()).get, production=True)

    def test_offer_matches_actual_service_and_subscription_semantics(self):
        page = render_legal_document("offer.html", self.config())
        for phrase in (
            "самозанятый", "Налог на профессиональный доход", "499 ₽ за 30 дней",
            "100% предоплата", "Договор заключается при оплате", "разовая покупка",
            "автоматического продления нет", "автоматические списания не производятся",
            "после подтверждённой оплаты", "webhook", "30 × 24 часа", "UTC",
            "после окончания действующего платного периода", "после окончания пробного периода",
            "18+", "электронный чек НПД", "№ 422-ФЗ", "не заменяет консультацию врача",
            "не гарантирует похудение", "не является согласием на рекламу",
            "Иванов Иван Иванович", "123456789012",
            'href="https://t.me/GritJBot"', 'href="mailto:kurtbachotto@gmail.com"',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, page)
        self.assertNotIn("{{", page)
        self.assertNotIn("Prodamus", page)
        self.assertNotIn("PayAnyWay", page)

    def test_refund_does_not_waive_statutory_rights_or_extend_legal_deadlines(self):
        page = render_legal_document("offer.html", self.config())
        for phrase in (
            'id="refund"', "вправе отказаться", "в любое время", "782 ГК РФ",
            "документально подтверждённых фактически понесённых расходов",
            "не продлевает эти сроки", "не является ограничением права обратиться в суд",
            "безусловная невозвратность оплаты", "не устанавливаются",
        ):
            self.assertIn(phrase, page)
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="offer.html#refund"', index)
        self.assertIn("Индивидуальная проверка не продлевает", index)

    def test_privacy_and_minimal_consent_are_separate_not_payment_acceptance(self):
        privacy = render_legal_document("privacy.html", self.config())
        consent = render_legal_document("consent.html", self.config())
        offer = render_legal_document("offer.html", self.config())
        self.assertIn('href="privacy.html"', offer)
        self.assertIn('href="consent.html"', offer)
        for phrase in ("Telegram ID", "возраст", "пол", "рост", "вес", "история упражнений"):
            self.assertIn(phrase, privacy + consent)
        self.assertIn("сама не собирает его", consent)
        self.assertIn("Просмотр страницы и оплата не означают согласия", consent)
        self.assertIn("не распространяется на сведения о здоровье", consent)
        self.assertIn("Обычный checkbox не заменяет", consent)
        self.assertIn("до его отзыва", consent)
        self.assertIn("mailto:kurtbachotto@gmail.com", privacy)
        # No forms, analytics, hidden acceptance, or broad consent to third parties.
        for page in (privacy, consent, offer):
            self.assertNotIn("<form", page)
            self.assertNotIn("<script", page)
            self.assertNotIn("банковский счёт Исполнителя", page)

    def test_privacy_matches_minimal_mvp_and_does_not_fake_implemented_deletion(self):
        privacy = render_legal_document("privacy.html", self.config())
        for text in ("MVP не запрашивает", "не сохраняет новые сведения о здоровье",
                     "SQLite", "не отправляются внешнему AI-провайдеру",
                     "не пересылает произвольные сообщения", "самостоятельной кнопки удаления",
                     "не удаляет запись из базы", "не разрешает бессрочно"):
            self.assertIn(text, privacy)
        offer = render_legal_document("offer.html", self.config())
        for text in ("не выполняет диагностику или лечение", "получите рекомендации врача",
                     "прекратите тренировку", "не запрашивает сведения о здоровье"):
            self.assertIn(text, offer)

    def test_public_build_never_reads_private_residence(self):
        values = self.values()
        def read(name):
            if name in {"SELLER_DISCLOSURE_ADDRESS", "SELLER_RESIDENCE_PRIVATE"}:
                raise AssertionError("Static build must not read residence")
            return values.get(name)
        config = build_landing_public_config(read, production=True)
        with tempfile.TemporaryDirectory() as directory:
            output = build_production_artifact(config, Path(directory) / "public")
            for filename in ("index.html", *LEGAL_DOCUMENTS):
                page = (output / filename).read_text(encoding="utf-8")
                self.assertNotIn("Тестовый город", page)
                self.assertNotIn("SELLER_DISCLOSURE_ADDRESS", page)
                self.assertNotIn("SELLER_RESIDENCE_PRIVATE", page)

    def test_legal_config_accepts_new_urls_and_normalizes_legacy_anchors(self):
        values = self.values()
        self.assertEqual("https://trainer.example/offer.html", self.config().terms_url)
        self.assertEqual("https://trainer.example/privacy.html", self.config().privacy_url)
        values.update(TERMS_URL="https://trainer.example/#terms", PRIVACY_URL="https://trainer.example/#privacy")
        self.assertEqual(self.config().terms_url, self.config(values).terms_url)
        self.assertEqual(self.config().privacy_url, self.config(values).privacy_url)
        for bad in ("https://evil.example/offer.html", "https://trainer.example/offer.html?x=1", "https://trainer.example/offer.html#missing"):
            values["TERMS_URL"] = bad
            with self.assertRaises(LandingConfigurationError):
                self.config(values)

    def test_legal_renderer_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            render_legal_document("../db.db", self.config())

    def test_static_and_rendered_bundle_has_no_dead_local_links_or_missing_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            output = build_production_artifact(self.config(), Path(directory) / "public")
            for root, rendered in ((ROOT, False), (output, True)):
                pages = {}
                for filename in ("index.html", *LEGAL_DOCUMENTS):
                    parser = PageLinks()
                    parser.feed((root / filename).read_text(encoding="utf-8"))
                    pages[filename] = parser
                for filename, parser in pages.items():
                    for href in parser.links + parser.styles:
                        with self.subTest(rendered=rendered, page=filename, href=href):
                            self.assertTrue(href)
                            self.assertNotEqual("#", href)
                            self.assertFalse(href.lower().startswith(("javascript:", "file:")))
                            if "{{" in href:  # source-only public config template
                                self.assertFalse(rendered)
                                continue
                            if not rendered and filename == "index.html" and href == "#website-purchase":
                                # Existing inactive CTA placeholder is removed in
                                # the production artifact until /buy is authorized.
                                continue
                            url = urlparse(urljoin(f"https://trainer.example/{filename}", href))
                            if url.scheme == "mailto":
                                self.assertEqual("kurtbachotto@gmail.com", url.path)
                                continue
                            if url.netloc != "trainer.example":
                                self.assertEqual("https", url.scheme)
                                continue
                            target = url.path.lstrip("/") or "index.html"
                            self.assertTrue((root / target).is_file(), target)
                            if url.fragment:
                                self.assertIn(url.fragment, pages[target].ids)
            for name in LEGAL_DOCUMENTS:
                page = (output / name).read_text(encoding="utf-8")
                self.assertIn(f'href="https://trainer.example/{name}"', page)
                self.assertNotIn("{{", page)
            self.assertTrue((output / "legal.css").is_file())
            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("data-purchase-cta", index)
            self.assertNotIn("Тестовый город", index)


if __name__ == "__main__":
    unittest.main()
