"""Public website-first purchase routes added to the existing webhook role."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from hmac import compare_digest
from html import escape
import logging
import re
from time import perf_counter

from aiohttp import web

from services.prodamus import ProdamusCallbackReason
from services.payment_domain import PaymentStatus
from services.payment_refund import RefundStatus
from services.website_purchase import WebsitePurchaseError, WebsiteLoginError, WebsiteLoginFailure
from services.telegram_oidc import TelegramOidcFailure
from services.private_offer import OFFER_VERSION
from storage.website_purchase_runtime import WebsitePurchaseRuntime


SESSION_COOKIE = "traineros_purchase"
logger = logging.getLogger(__name__)
_PRODUCT_FIELD = re.compile(r"^products\[(\d+)]\[([A-Za-z_][A-Za-z0-9_]*)]$")


def add_website_purchase_routes(app: web.Application, runtime: WebsitePurchaseRuntime) -> None:
    app.middlewares.append(_private_purchase_response)
    app.router.add_get("/buy", _buy_handler(runtime))
    app.router.add_get("/purchase/offer", _offer_handler(runtime))
    app.router.add_get("/auth/telegram/callback", _telegram_callback_handler(runtime))
    app.router.add_post("/api/payment/create", _payment_create_handler(runtime))
    app.router.add_post("/webhooks/prodamus", _prodamus_webhook_handler(runtime))
    app.router.add_get("/payment/success", _return_page(runtime, success=True))
    app.router.add_get("/payment/fail", _return_page(runtime, success=False))


def _buy_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        if await asyncio.to_thread(runtime.service.is_authenticated, token):
            try:
                paid = await asyncio.to_thread(runtime.service.has_paid_access, token)
                available = paid is True or await asyncio.to_thread(runtime.service.checkout_available, token)
            except WebsitePurchaseError:
                # The session can expire between these independent DB reads.
                return web.Response(status=401, text="Сессия истекла. Откройте страницу покупки заново")
            if paid is True:
                return web.Response(
                    text=("<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
                          "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                          "<p>Доступ уже оплачен. Повторная оплата не требуется.</p>"
                          f'<a href="{escape(runtime.telegram_bot_url, quote=True)}">Открыть @GritJBot</a>'),
                    content_type="text/html", charset="utf-8",
                )
            if not available:
                return web.Response(status=503, text="Покупка временно недоступна")
            if runtime.offer is None:
                return web.Response(status=503, text="Условия покупки временно недоступны")
            return web.Response(text=_email_page(token, runtime.offer.public.public_site_url), content_type="text/html", charset="utf-8")
        started = await asyncio.to_thread(runtime.service.start_login)
        response = web.HTTPFound(started.authorization_url)
        _set_session_cookie(response, started.session_token)
        logger.warning("website_login event=login_started")
        raise response
    return handle


def _offer_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        if not await asyncio.to_thread(runtime.service.is_authenticated,
                                      request.cookies.get(SESSION_COOKIE, "")):
            return web.Response(status=401, text="Войдите через Telegram для просмотра оферты")
        if runtime.offer is None:
            return web.Response(status=503, text="Оферта временно недоступна")
        return web.Response(text=runtime.offer.render(), content_type="text/html", charset="utf-8")
    return handle


def _telegram_callback_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        # Standalone webhook uses Python's default WARNING threshold. Keep these
        # temporary value-free launch diagnostics visible without global logging.
        logger.warning(
            "website_login event=callback_observed cookie_present=%s state_present=%s code_present=%s provider_error_present=%s",
            bool(token), bool(request.query.get("state")), bool(request.query.get("code")),
            bool(request.query.get("error")),
        )
        try:
            await asyncio.to_thread(
                runtime.service.complete_login,
                token,
                request.query.get("state", ""),
                request.query.get("code", ""),
            )
        except WebsitePurchaseError as error:
            # Only fixed enums and stage names are log data. Never log exceptions,
            # callback values, browser headers, identity or provider payloads.
            stage, reason, oidc_reason = "unknown", "unclassified", "unclassified"
            if isinstance(error, WebsiteLoginError):
                if error.stage in {"session_before_exchange", "exchange_or_identity", "session_after_exchange", "session_claim"}:
                    stage = error.stage
                if isinstance(error.reason, WebsiteLoginFailure):
                    reason = error.reason.value
                if isinstance(error.oidc_reason, TelegramOidcFailure):
                    oidc_reason = error.oidc_reason.value
            logger.warning("website_login event=callback_rejected stage=%s reason=%s oidc_reason=%s", stage, reason, oidc_reason)
            return web.Response(status=400, text="Вход через Telegram не подтверждён", content_type="text/plain")
        except Exception:
            logger.warning("website_login event=callback_rejected stage=unknown reason=internal_error oidc_reason=unclassified")
            return web.Response(status=503, text="Вход временно недоступен", content_type="text/plain")
        logger.warning("website_login event=login_completed")
        raise web.HTTPFound("/buy")
    return handle


def _payment_create_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        try:
            values = await request.post()
            token = request.cookies.get(SESSION_COOKIE, "")
            if (
                runtime.offer is None
                or len(values.getall("offer_version", [])) != 1
                or values.get("offer_version") != OFFER_VERSION
                or len(values.getall("email", [])) != 1
                or len(values.getall("csrf", [])) != 1
                or not isinstance(values.get("csrf"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", values["csrf"])
                or not compare_digest(values["csrf"], _csrf_token(token))
            ):
                raise WebsitePurchaseError("Payment request is invalid.")
            email = values.get("email")
            if not isinstance(email, str):
                raise WebsitePurchaseError("Email is invalid.")
            order = await asyncio.to_thread(
                runtime.service.create_order,
                token,
                email,
            )
        except (WebsitePurchaseError, web.HTTPBadRequest, ValueError):
            logger.info("website_purchase event=checkout_rejected outcome=invalid_request")
            return web.Response(status=400, text="Проверьте email и повторите попытку", content_type="text/plain")
        except Exception:
            logger.info("website_purchase event=checkout_rejected outcome=temporarily_unavailable")
            return web.Response(status=503, text="Оплата временно недоступна. Повторите попытку позже", content_type="text/plain")
        logger.info("website_purchase event=checkout_requested payment_id=%d", order.payment_id)
        raise web.HTTPSeeOther(order.checkout_url)
    return handle


def _prodamus_webhook_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        started = perf_counter()
        logger.info("prodamus event=webhook_received")
        def response(status: int, outcome: str, text: str) -> web.Response:
            logger.info("prodamus event=webhook_result outcome=%s status=%d duration_ms=%d",
                        outcome, status, round((perf_counter() - started) * 1000))
            return web.Response(status=status, text=text)
        if request.content_type != "application/x-www-form-urlencoded":
            return response(415, "unsupported_media_type", "notification_rejected")
        try:
            values = await request.post()
            payload = _prodamus_form_payload(values)
            if payload is None:
                return response(400, "malformed", "notification_rejected")
            result = await asyncio.to_thread(
                runtime.service.process_prodamus_notification,
                payload,
                request.headers.get("Sign"),
            )
        except web.HTTPRequestEntityTooLarge:
            return response(413, "payload_too_large", "notification_rejected")
        except (web.HTTPBadRequest, UnicodeDecodeError, ValueError):
            return response(400, "malformed", "notification_rejected")
        except Exception:
            return response(503, "temporarily_unavailable", "temporarily_unavailable")
        if result.accepted:
            return response(200, result.reason.value, "ok")
        status = 400 if result.reason is ProdamusCallbackReason.INVALID_SIGNATURE else 422
        return response(status, result.reason.value, "notification_rejected")
    return handle


def _prodamus_form_payload(values) -> dict[str, object] | None:
    """Mirror PHP-style bracketed product fields before signature validation."""
    payload: dict[str, object] = {}
    products: dict[int, dict[str, str]] = {}
    for name in values:
        if len(values.getall(name)) != 1:
            return None
        value = values.get(name)
        if not isinstance(value, str):
            return None
        match = _PRODUCT_FIELD.fullmatch(name)
        if match is None:
            payload[name] = value
            continue
        index, field = int(match.group(1)), match.group(2)
        if index > 100 or field in products.setdefault(index, {}):
            return None
        products[index][field] = value
    if products:
        if "products" in payload:
            return None
        if sorted(products) != list(range(len(products))):
            return None
        payload["products"] = [products[index] for index in range(len(products))]
    return payload


def _return_page(runtime: WebsitePurchaseRuntime, *, success: bool):
    pending_text = (
        "Оплата обрабатывается. Доступ появится после подтверждения платежа."
        if success
        else "Оплата не завершена. Можно вернуться и попробовать позже."
    )
    async def handle(request: web.Request) -> web.Response:
        try:
            status = await asyncio.to_thread(
                runtime.service.inspect_order_status, request.cookies.get(SESSION_COOKIE, "")
            )
        except WebsitePurchaseError:
            status = None
        text = pending_text
        if status is RefundStatus.CONFIRMED:
            text = "Возврат оплаты подтверждён. Доступ по этому заказу отозван."
        elif status is PaymentStatus.SUCCEEDED:
            text = "Оплата подтверждена. Доступ на 30 дней активирован."
        elif status in {PaymentStatus.FAILED, PaymentStatus.CANCELED, PaymentStatus.EXPIRED}:
            text = "Оплата не завершена. Можно вернуться и попробовать позже."
        elif status is None:
            text = "Войдите через Telegram, чтобы проверить заказ. Возврат с оплаты не подтверждает доступ."
        elif not success:
            text = "Подтверждения оплаты пока нет. Можно вернуться к заказу и повторить попытку."
        return web.Response(
            text=("<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
                  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                  f"<p>{escape(text)}</p><a href=\"/buy\">Вернуться к заказу</a> "
                  f"<a href=\"{escape(runtime.telegram_bot_url, quote=True)}\">Открыть @GritJBot</a>"),
            content_type="text/html", charset="utf-8",
        )
    return handle


def _set_session_cookie(response: web.StreamResponse, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=True, samesite="Lax", path="/"
    )


def _csrf_token(token: str) -> str:
    return sha256(f"purchase-post:{token}".encode()).hexdigest()


@web.middleware
async def _private_purchase_response(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = error
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _email_page(token: str, public_site_url: str) -> str:
    return f"""<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>TRAINER.OS — доступ</title><h1>Доступ к «Мой AI Тренер»</h1>
<p>499 ₽ за 30 дней · разовая покупка · без автопродления и автоматических списаний</p>
<p>После подтверждения оплаты доступ появится в @GritJBot</p>
<nav><a href="/purchase/offer">Полная оферта и сведения об Исполнителе</a>
<a href="{escape(public_site_url, quote=True)}/privacy.html">Приватность</a>
<a href="/purchase/offer#refund">Отказ и возврат</a></nav>
<form method=\"post\" action=\"/api/payment/create\">
<input type=\"hidden\" name=\"csrf\" value=\"{_csrf_token(token)}\"><label>Email для чека
<input required type=\"email\" name=\"email\" autocomplete=\"email\"></label>
<label><input required type="checkbox" name="offer_version" value="{OFFER_VERSION}">Ознакомился с полной офертой до оплаты</label>
<button type=\"submit\">Перейти к оплате</button></form></html>"""
