import unittest
import asyncio
from unittest.mock import Mock

from aiohttp import web
from multidict import MultiDict

from storage.website_purchase_http import (
    _prodamus_form_payload, add_website_purchase_routes, _payment_create_handler,
    _return_page, _prodamus_webhook_handler, _csrf_token, SESSION_COOKIE,
    _buy_handler, _private_purchase_response,
)
from storage.website_purchase_runtime import WebsitePurchaseRuntime
from services.payment_domain import PaymentStatus
from services.prodamus import ProdamusCallbackReason, ProdamusCallbackResult, ProdamusOrder
from services.website_purchase import WebsitePurchaseError


class _Service:
    pass


class _Request:
    def __init__(self, values=(), *, token="session-fixture", content_type="application/x-www-form-urlencoded"):
        self.values = MultiDict(values)
        self.cookies = {SESSION_COOKIE: token}
        self.headers = {"Sign": "signature-fixture"}
        self.content_type = content_type

    async def post(self):
        return self.values


class WebsitePurchaseHttpTests(unittest.TestCase):
    def test_routes_are_added_to_existing_webhook_application(self):
        app = web.Application()
        runtime = WebsitePurchaseRuntime(
            _Service(), "https://payments.traineros.ru", "https://t.me/GritJBot"
        )
        add_website_purchase_routes(app, runtime)
        routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
        self.assertTrue({
            ("HEAD", "/buy"),
            ("GET", "/buy"),
            ("HEAD", "/auth/telegram/callback"),
            ("GET", "/auth/telegram/callback"),
            ("POST", "/api/payment/create"),
            ("POST", "/webhooks/prodamus"),
            ("HEAD", "/payment/success"),
            ("GET", "/payment/success"),
            ("HEAD", "/payment/fail"),
            ("GET", "/payment/fail"),
        }.issubset(routes))

    def test_prodamus_php_style_products_are_reconstructed_before_signature_check(self):
        class Values(dict):
            def getall(self, name):
                return [self[name]]
        payload = _prodamus_form_payload(Values({
            "order_id": "provider-1",
            "products[0][name]": "Service",
            "products[0][price]": "499.00",
            "products[1][name]": "Extra",
        }))
        self.assertEqual(
            [{"name": "Service", "price": "499.00"}, {"name": "Extra"}],
            payload["products"],
        )


    def runtime(self, service):
        return WebsitePurchaseRuntime(service, "https://payments.traineros.ru", "https://t.me/GritJBot")

    def test_redirect_reads_server_status_not_query_and_never_creates_or_confirms(self):
        service = Mock()
        for state, expected in (
            (PaymentStatus.PENDING, "обрабатывается"),
            (PaymentStatus.SUCCEEDED, "подтверждена"),
            (PaymentStatus.FAILED, "не завершена"),
            (PaymentStatus.CANCELED, "не завершена"),
            (None, "Войдите через Telegram"),
        ):
            service.inspect_order_status.return_value = state
            response = asyncio.run(_return_page(self.runtime(service), success=True)(_Request()))
            self.assertIn(expected, response.text)
            self.assertIn("Открыть @GritJBot", response.text)
        service.inspect_order_status.side_effect = WebsitePurchaseError("expired-session-private")
        response = asyncio.run(_return_page(self.runtime(service), success=True)(_Request()))
        self.assertNotIn("expired-session-private", response.text)
        service.create_order.assert_not_called()
        service.process_prodamus_notification.assert_not_called()

    def test_create_order_requires_bound_csrf_and_single_email_and_redirects_as_get(self):
        service = Mock()
        service.create_order.return_value = ProdamusOrder(1, "transaction", "https://trainer.payform.ru", "buyer@example.com", True)
        handler = _payment_create_handler(self.runtime(service))
        for values in (
            [("email", "buyer@example.com")],
            [("email", "buyer@example.com"), ("csrf", "wrong")],
            [("email", "buyer@example.com"), ("email", "other@example.com"), ("csrf", _csrf_token("session-fixture"))],
        ):
            response = asyncio.run(handler(_Request(values)))
            self.assertEqual(400, response.status)
        service.create_order.assert_not_called()
        with self.assertRaises(web.HTTPSeeOther) as redirect:
            asyncio.run(handler(_Request({"email": "buyer@example.com", "csrf": _csrf_token("session-fixture")})))
        self.assertEqual("https://trainer.payform.ru", redirect.exception.location)
        service.create_order.assert_called_once_with("session-fixture", "buyer@example.com")

    def test_webhook_transport_and_safe_failure_logging(self):
        service = Mock()
        service.process_prodamus_notification.side_effect = RuntimeError("private-error-fixture")
        handler = _prodamus_webhook_handler(self.runtime(service))
        with self.assertLogs("storage.website_purchase_http", level="INFO") as logs:
            response = asyncio.run(handler(_Request({"customer_email": "private-email@example.com"})))
        self.assertEqual(503, response.status)
        output = "\n".join(logs.output)
        for private in ("private-error-fixture", "private-email@example.com", "signature-fixture"):
            self.assertNotIn(private, output)
            self.assertNotIn(private, response.text)
        self.assertIn("temporarily_unavailable", output)
        service.process_prodamus_notification.reset_mock()
        response = asyncio.run(handler(_Request(content_type="application/json")))
        self.assertEqual(415, response.status)
        service.process_prodamus_notification.assert_not_called()

    def test_webhook_valid_duplicate_invalid_and_unknown_responses(self):
        for accepted, reason, status in (
            (True, ProdamusCallbackReason.CONFIRMED, 200),
            (True, ProdamusCallbackReason.ALREADY_CONFIRMED, 200),
            (False, ProdamusCallbackReason.INVALID_SIGNATURE, 400),
            (False, ProdamusCallbackReason.PAYMENT_NOT_FOUND, 422),
        ):
            service = Mock()
            service.process_prodamus_notification.return_value = ProdamusCallbackResult(accepted, reason)
            response = asyncio.run(_prodamus_webhook_handler(self.runtime(service))(_Request()))
            self.assertEqual(status, response.status)

    def test_ambiguous_duplicate_and_sparse_products_rejected(self):
        for values in (
            [("sum", "499"), ("sum", "1")],
            [("products", "value"), ("products[0][name]", "service")],
            [("products[2][name]", "service")],
        ):
            self.assertIsNone(_prodamus_form_payload(MultiDict(values)))

    def test_authenticated_buy_has_summary_email_csrf_and_private_headers(self):
        service = Mock()
        service.is_authenticated.return_value = True
        handler = _buy_handler(self.runtime(service))
        response = asyncio.run(_private_purchase_response(_Request(), handler))
        self.assertEqual(200, response.status)
        for text in ("499 ₽", "30 дней", "без автопродления", "Email для чека", _csrf_token("session-fixture")):
            self.assertIn(text, response.text)
        self.assertNotIn("session-fixture", response.text)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        service.start_login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
