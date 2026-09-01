"""Render a production-only static landing from verified public configuration."""

from __future__ import annotations

from html import escape
from pathlib import Path

from landing.site_config import LandingPublicConfig


_TEMPLATE = Path(__file__).with_name("index.html")
_SELLER_PLACEHOLDER = (
    "ФИО, ИНН и контакт продавца публикуются в production-версии сайта только "
    "после заполнения и проверки публичной конфигурации. До этого страница не "
    "считается готовой к коммерческой публикации."
)
_SUPPORT_PLACEHOLDER = (
    "По вопросам доступа, оплаты, чека или возврата используйте контакт "
    "поддержки, опубликованный в production-версии сайта."
)


def render_production_landing(config: LandingPublicConfig) -> str:
    """Return publishable HTML only after all required public facts are present."""
    if not config.production_ready:
        raise ValueError("Production landing configuration is incomplete.")
    assert config.public_site_url is not None
    assert config.telegram_bot_url is not None
    assert config.seller_full_name is not None
    assert config.seller_inn is not None
    assert config.support_email is not None
    html = _TEMPLATE.read_text(encoding="utf-8")
    seller = (
        f"Продавец: {escape(config.seller_full_name)}. "
        f"ИНН: {escape(config.seller_inn)}. "
        f"Контакт: {escape(config.support_email)}."
    )
    support = (
        "По вопросам доступа, оплаты, чека или возврата: "
        f"{escape(config.support_email)}."
    )
    return (
        html.replace('<html lang="ru">', f'<html lang="ru" data-telegram-url="{escape(config.telegram_bot_url, quote=True)}">')
        .replace('<meta name="robots" content="noindex, nofollow">', '<meta name="robots" content="index, follow">')
        .replace('<!-- canonical-url -->', f'<link rel="canonical" href="{escape(config.public_site_url, quote=True)}">')
        .replace(_SELLER_PLACEHOLDER, seller)
        .replace(_SUPPORT_PLACEHOLDER, support)
    )
