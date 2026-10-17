"""Full seller disclosure belongs only to an authenticated server response."""

from dataclasses import dataclass, field
from html import escape
import re

from landing.render import render_legal_document
from landing.site_config import LandingPublicConfig, build_landing_public_config

OFFER_VERSION = "2026-10-04"


@dataclass(frozen=True)
class PrivateOffer:
    public: LandingPublicConfig
    residence: str = field(repr=False)

    def __post_init__(self):
        if (not self.public.production_ready or not isinstance(self.residence, str)
                or not 5 <= len(self.residence.strip()) <= 300
                or any(ord(char) < 32 for char in self.residence)):
            raise ValueError("Private offer configuration is incomplete.")

    @classmethod
    def from_settings(cls, settings):
        return cls(build_landing_public_config(settings.optional, production=True),
                   settings("SELLER_RESIDENCE_PRIVATE"))

    def render(self) -> str:
        page = render_legal_document("offer.html", self.public)
        page = re.sub(r'    <p id="private-offer-notice".*?</p>\n?', '', page)
        page = page.replace("Условия сервиса и оферта", "Публичная оферта")
        page = page.replace("<!-- private-seller-disclosure -->",
                            f'<p>Место жительства Исполнителя: {escape(self.residence)}.</p>')
        page = re.sub(r'<link rel="canonical"[^>]*>', '', page)
        page = page.replace('</head>', '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet"></head>')
        for filename in ("index.html", "privacy.html", "consent.html", "legal.css"):
            page = page.replace(f'"{filename}', f'"{self.public.public_site_url}/{filename}')
        return page
