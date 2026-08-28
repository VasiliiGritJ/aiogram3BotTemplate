"""Deploy-ready aiohttp boundary for payment webhook processing.

This module is deliberately separate from ``bot.py``.  A deployment may run
the HTTP application as its own process without creating a Telegram Bot or a
second polling owner.
"""

import asyncio
from dataclasses import dataclass
from json import JSONDecodeError
import logging
from time import perf_counter
from typing import Callable, Mapping, Protocol

from aiohttp import web

from services.payment_webhook import (
    PaymentWebhookReason,
    PaymentWebhookResult,
    SUPPORTED_PAYMENT_EVENTS,
    YooKassaWebhookProcessor,
)
from storage.payment_runtime import (
    PaymentMode,
    PaymentRuntimeConfigurationError,
    build_payment_runtime,
)


PAYMENT_WEBHOOK_PATH = "/webhooks/yookassa/payment"
PAYMENT_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
PAYMENT_WEBHOOK_LIVENESS_PATH = "/health/live"
PAYMENT_WEBHOOK_READINESS_PATH = "/health/ready"
LOCAL_SAFE_BIND_HOST = "127.0.0.1"
LOCAL_SAFE_BIND_PORT = 8080


logger = logging.getLogger(__name__)


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
            or self.path.startswith("//")
            or "\\" in self.path
            or ".." in self.path.split("/")
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
        started_at = perf_counter()
        if request.content_type != "application/json":
            return _observed_response(
                "invalid", "unsupported_media_type", 415, started_at
            )
        try:
            payload = await request.json()
        except web.HTTPRequestEntityTooLarge:
            return _observed_response("invalid", "payload_too_large", 413, started_at)
        except (JSONDecodeError, ValueError, UnicodeDecodeError, TypeError):
            return _observed_response("invalid", "malformed_request", 400, started_at)

        try:
            # Provider reconciliation uses the existing synchronous SDK and
            # SQLAlchemy service.  Keep it off aiohttp's event loop.
            result = await asyncio.to_thread(processor.process, payload)
        except Exception:
            # Never disclose DB/provider errors or the raw request.  A retryable
            # response lets the provider redeliver after a transient failure.
            return _observed_response(
                _event_category(payload), "temporarily_unavailable", 503, started_at
            )
        response = _result_response(result)
        _log_webhook_result(
            _event_category(payload),
            result.reason.value,
            response.status,
            started_at,
        )
        return response

    return handle


def create_payment_webhook_app(
    processor: PaymentWebhookProcessor,
    config: PaymentWebhookServerConfig,
) -> web.Application:
    """Build an aiohttp app without starting a socket or Telegram polling."""
    app = web.Application(client_max_size=config.max_body_bytes)
    app.router.add_get(PAYMENT_WEBHOOK_LIVENESS_PATH, _liveness_handler)
    app.router.add_get(PAYMENT_WEBHOOK_READINESS_PATH, _readiness_handler)
    app.router.add_post(config.path, create_payment_webhook_handler(processor))
    return app


def build_payment_webhook_http_runtime(
    read_required: Callable[[str], str],
) -> PaymentWebhookHttpRuntime:
    """Compose the separate deploy process from injected server-side config."""
    try:
        payment_runtime = build_payment_runtime(read_required)
        config = _build_server_config(read_required, payment_runtime.mode)
    except (PaymentRuntimeConfigurationError, ValueError, TypeError):
        raise PaymentWebhookHttpConfigurationError(
            "Payment webhook configuration is invalid."
        ) from None
    processor = YooKassaWebhookProcessor(payment_runtime.payment_service)
    return PaymentWebhookHttpRuntime(
        create_payment_webhook_app(processor, config),
        config,
    )


def _build_server_config(
    read_required: Callable[[str], str],
    payment_mode: PaymentMode,
) -> PaymentWebhookServerConfig:
    """Allow loopback defaults only in test/local mode, never in production."""
    if payment_mode is PaymentMode.PRODUCTION:
        bind_host = _read_required(read_required, "PAYMENT_WEBHOOK_BIND_HOST")
        bind_port_value = _read_required(read_required, "PAYMENT_WEBHOOK_BIND_PORT")
        path = _read_required(read_required, "PAYMENT_WEBHOOK_PATH")
    else:
        bind_host = _read_optional(read_required, "PAYMENT_WEBHOOK_BIND_HOST")
        bind_port_value = _read_optional(read_required, "PAYMENT_WEBHOOK_BIND_PORT")
        path = _read_optional(read_required, "PAYMENT_WEBHOOK_PATH")
        bind_host = bind_host or LOCAL_SAFE_BIND_HOST
        bind_port_value = bind_port_value or str(LOCAL_SAFE_BIND_PORT)
        path = path or PAYMENT_WEBHOOK_PATH
    return PaymentWebhookServerConfig(bind_host, int(bind_port_value), path)


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


def _read_optional(read_required: Callable[[str], str], name: str) -> str | None:
    try:
        value = read_required(name)
    except Exception:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
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


async def _liveness_handler(request: web.Request) -> web.Response:
    del request
    return _response(200, "live")


async def _readiness_handler(request: web.Request) -> web.Response:
    del request
    # This application is created only after runtime configuration has passed.
    # Provider availability is intentionally not part of local readiness.
    return _response(200, "ready")


def _event_category(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return "malformed"
    event = payload.get("event")
    if event in SUPPORTED_PAYMENT_EVENTS:
        return event
    if isinstance(event, str):
        return "unsupported"
    return "malformed"


def _observed_response(
    event: str,
    outcome: str,
    status: int,
    started_at: float,
) -> web.Response:
    _log_webhook_result(event, outcome, status, started_at)
    return _response(status, outcome)


def _log_webhook_result(
    event: str,
    outcome: str,
    status: int,
    started_at: float,
) -> None:
    duration_ms = round((perf_counter() - started_at) * 1000)
    logger.info(
        "payment_webhook event=%s outcome=%s status=%s duration_ms=%s",
        event,
        outcome,
        status,
        duration_ms,
    )
