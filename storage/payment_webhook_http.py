"""Deploy-ready aiohttp boundary for payment webhook processing.

This module is deliberately separate from ``bot.py``.  A deployment may run
the HTTP application as its own process without creating a Telegram Bot or a
second polling owner.
"""

import asyncio
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Callable, Protocol

from aiohttp import web

from services.payment_webhook import (
    PaymentWebhookReason,
    PaymentWebhookResult,
    YooKassaWebhookProcessor,
)
from storage.payment_runtime import build_payment_runtime


PAYMENT_WEBHOOK_PATH = "/webhooks/yookassa/payment"
PAYMENT_WEBHOOK_MAX_BODY_BYTES = 64 * 1024


class PaymentWebhookHttpConfigurationError(RuntimeError):
    """Raised when the separate HTTP runtime cannot fail closed."""


class PaymentWebhookProcessor(Protocol):
    def process(self, payload: object) -> PaymentWebhookResult:
        """Process one decoded provider notification."""


@dataclass(frozen=True)
class PaymentWebhookServerConfig:
    bind_host: str
    bind_port: int
    path: str = PAYMENT_WEBHOOK_PATH
    max_body_bytes: int = PAYMENT_WEBHOOK_MAX_BODY_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.bind_host, str) or not self.bind_host.strip():
            raise ValueError("Webhook bind host is required.")
        if isinstance(self.bind_port, bool) or not isinstance(self.bind_port, int):
            raise ValueError("Webhook bind port is invalid.")
        if not 1 <= self.bind_port <= 65535:
            raise ValueError("Webhook bind port is invalid.")
        if (
            not isinstance(self.path, str)
            or not self.path.startswith("/")
            or "?" in self.path
            or "#" in self.path
        ):
            raise ValueError("Webhook path is invalid.")
        if (
            isinstance(self.max_body_bytes, bool)
            or not isinstance(self.max_body_bytes, int)
            or self.max_body_bytes <= 0
        ):
            raise ValueError("Webhook body limit is invalid.")


@dataclass(frozen=True)
class PaymentWebhookHttpRuntime:
    app: web.Application
    config: PaymentWebhookServerConfig


def create_payment_webhook_handler(
    processor: PaymentWebhookProcessor,
) -> Callable[[web.Request], object]:
    """Create the thin HTTP-to-application adapter with stable responses."""

    async def handle(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return _response(415, "unsupported_media_type")
        try:
            payload = await request.json()
        except web.HTTPRequestEntityTooLarge:
            return _response(413, "payload_too_large")
        except (JSONDecodeError, ValueError, UnicodeDecodeError, TypeError):
            return _response(400, "malformed_request")

        try:
            # Provider reconciliation uses the existing synchronous SDK and
            # SQLAlchemy service.  Keep it off aiohttp's event loop.
            result = await asyncio.to_thread(processor.process, payload)
        except Exception:
            # Never disclose DB/provider errors or the raw request.  A retryable
            # response lets the provider redeliver after a transient failure.
            return _response(503, "temporarily_unavailable")
        return _result_response(result)

    return handle


def create_payment_webhook_app(
    processor: PaymentWebhookProcessor,
    config: PaymentWebhookServerConfig,
) -> web.Application:
    """Build an aiohttp app without starting a socket or Telegram polling."""
    app = web.Application(client_max_size=config.max_body_bytes)
    app.router.add_post(config.path, create_payment_webhook_handler(processor))
    return app


def build_payment_webhook_http_runtime(
    read_required: Callable[[str], str],
) -> PaymentWebhookHttpRuntime:
    """Compose the separate deploy process from injected server-side config."""
    try:
        bind_host = _read_required(read_required, "PAYMENT_WEBHOOK_BIND_HOST")
        bind_port = int(_read_required(read_required, "PAYMENT_WEBHOOK_BIND_PORT"))
        config = PaymentWebhookServerConfig(bind_host, bind_port)
        payment_runtime = build_payment_runtime(read_required)
    except (ValueError, TypeError):
        raise PaymentWebhookHttpConfigurationError(
            "Payment webhook configuration is invalid."
        ) from None
    processor = YooKassaWebhookProcessor(payment_runtime.payment_service)
    return PaymentWebhookHttpRuntime(
        create_payment_webhook_app(processor, config),
        config,
    )


def _read_required(read_required: Callable[[str], str], name: str) -> str:
    try:
        value = read_required(name)
    except Exception:
        raise PaymentWebhookHttpConfigurationError(
            "Payment webhook configuration is incomplete."
        ) from None
    if not isinstance(value, str) or not value.strip():
        raise PaymentWebhookHttpConfigurationError(
            "Payment webhook configuration is incomplete."
        )
    return value.strip()


def _result_response(result: PaymentWebhookResult) -> web.Response:
    if result.reason in {
        PaymentWebhookReason.RECONCILED,
        PaymentWebhookReason.UNSUPPORTED_EVENT,
        PaymentWebhookReason.PAYMENT_NOT_FOUND,
    }:
        return _response(200, "acknowledged")
    if result.reason is PaymentWebhookReason.MALFORMED_PAYLOAD:
        return _response(400, "malformed_request")
    if result.reason is PaymentWebhookReason.PROVIDER_UNAVAILABLE:
        return _response(503, "temporarily_unavailable")
    return _response(409, "reconcile_rejected")


def _response(status: int, outcome: str) -> web.Response:
    return web.json_response({"status": outcome}, status=status)
