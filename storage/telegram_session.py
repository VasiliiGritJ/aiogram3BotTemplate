"""Optional Telegram transport configuration without local-machine defaults."""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession


def normalize_proxy_url(proxy_url: str | None) -> str | None:
    """Treat missing or whitespace-only proxy configuration as disabled."""
    if proxy_url is None:
        return None
    value = proxy_url.strip()
    return value or None


def create_telegram_bot(bot_token: str, proxy_url: str | None = None) -> Bot:
    """Create the project bot with an optional aiogram-managed proxy session.

    ``AiohttpSession`` delegates SOCKS URLs to the installed ``aiohttp-socks``
    connector. For SOCKS5, aiogram configures remote DNS resolution, so the
    Telegram hostname is resolved through the proxy rather than hard-coded
    local networking settings.
    """
    normalized_proxy = normalize_proxy_url(proxy_url)
    session = (
        AiohttpSession(proxy=normalized_proxy)
        if normalized_proxy is not None
        else None
    )
    return Bot(
        bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode="html"),
    )
