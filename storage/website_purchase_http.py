"""Public website-first purchase routes added to the existing webhook role."""

from __future__ import annotations

import asyncio
from html import escape
import re
from time import perf_counter

from aiohttp import web

from services.prodamus import ProdamusCallbackReason
from services.website_purchase import WebsitePurchaseError
from storage.website_purchase_runtime import WebsitePurchaseRuntime


SESSION_COOKIE = "traineros_purchase"
_PRODUCT_FIELD = re.compile(r"^products\[(\d+)]\[([A-Za-z_][A-Za-z0-9_]*)]$")


def add_website_purchase_routes(app: web.Application, runtime: WebsitePurchaseRuntime) -> None:
    app.router.add_get("/buy", _buy_handler(runtime))
    app.router.add_get("/auth/telegram/callback", _telegram_callback_handler(runtime))
    app.router.add_post("/api/payment/create", _payment_create_handler(runtime))
    app.router.add_post("/webhooks/prodamus", _prodamus_webhook_handler(runtime))
    app.router.add_get("/payment/success", _return_page(runtime, success=True))
    app.router.add_get("/payment/fail", _return_page(runtime, success=False))


def _buy_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        if runtime.service.is_authenticated(token):
            return web.Response(text=_email_page(), content_type="text/html", charset="utf-8")
        started = await asyncio.to_thread(runtime.service.start_login)
        response = web.HTTPFound(started.authorization_url)
        _set_session_cookie(response, started.session_token)
        raise response
    return handle


def _telegram_callback_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        try:
            await asyncio.to_thread(
                runtime.service.complete_login,
                token,
                request.query.get("state", ""),
                request.query.get("code", ""),
            )
        except WebsitePurchaseError:
            return web.Response(status=400, text="Вход через Telegram не подтверждён", content_type="text/plain")
        raise web.HTTPFound("/buy")
    return handle


def _payment_create_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        try:
            values = await request.post()
            email = values.get("email")
            if not isinstance(email, str):
                raise WebsitePurchaseError("Email is invalid.")
            order = await asyncio.to_thread(
                runtime.service.create_order,
                request.cookies.get(SESSION_COOKIE, ""),
                email,
            )
        except (WebsitePurchaseError, web.HTTPBadRequest, ValueError):
            return web.Response(status=400, text="Проверьте email и повторите попытку", content_type="text/plain")
        raise web.HTTPFound(order.checkout_url)
    return handle


def _prodamus_webhook_handler(runtime: WebsitePurchaseRuntime):
    async def handle(request: web.Request) -> web.Response:
        started = perf_counter()
        try:
            values = await request.post()
            payload = _prodamus_form_payload(values)
            if payload is None:
                return web.Response(status=400, text="notification_rejected")
            result = await asyncio.to_thread(
                runtime.service.process_prodamus_notification,
                payload,
                request.headers.get("Sign"),
            )
        except Exception:
            return web.Response(status=503, text="temporarily_unavailable")
        if result.accepted:
            return web.Response(status=200, text="ok")
        status = 400 if result.reason is ProdamusCallbackReason.INVALID_SIGNATURE else 422
        return web.Response(status=status, text="notification_rejected")
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
        if sorted(products) != list(range(len(products))):
            return None
        payload["products"] = [products[index] for index in range(len(products))]
    return payload


def _return_page(runtime: WebsitePurchaseRuntime, *, success: bool):
    text = (
        "Оплата обрабатывается. Доступ появится после подтверждения платежа."
        if success
        else "Оплата не завершена. Можно вернуться и попробовать позже."
    )
    async def handle(request: web.Request) -> web.Response:
        del request
        return web.Response(
            text=("<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
                  f"<p>{escape(text)}</p><a href=\"{escape(runtime.telegram_bot_url, quote=True)}\">Открыть @GritJBot</a>"),
            content_type="text/html", charset="utf-8",
        )
    return handle


def _set_session_cookie(response: web.StreamResponse, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=True, samesite="Lax", path="/"
    )


def _email_page() -> str:
    return """<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">
<title>TRAINER.OS — доступ</title><h1>Доступ к «Мой AI Тренер»</h1>
<p>499 ₽ за 30 дней · без автопродления</p>
<form method=\"post\" action=\"/api/payment/create\"><label>Email для чека
<input required type=\"email\" name=\"email\" autocomplete=\"email\"></label>
<button type=\"submit\">Перейти к оплате</button></form></html>"""
