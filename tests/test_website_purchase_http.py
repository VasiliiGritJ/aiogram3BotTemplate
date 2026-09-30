import unittest

from aiohttp import web

from storage.website_purchase_http import _prodamus_form_payload, add_website_purchase_routes
from storage.website_purchase_runtime import WebsitePurchaseRuntime


class _Service:
    pass


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


if __name__ == "__main__":
    unittest.main()
