import io
import gzip
import os
import unittest
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request
from urllib.response import addinfourl

from services.telegram_oidc import (
    TELEGRAM_OIDC_JWKS_URL,
    TELEGRAM_OIDC_TOKEN_URL,
    TelegramOidcClient,
    TelegramOidcConfig,
    TelegramOidcError,
    _NoOidcRedirects,
    _TelegramOnlyProxy,
    _read_oidc_json,
)


class TelegramOidcProxyTests(unittest.TestCase):
    def config(self, proxy="http://127.0.0.1:13128"):
        return TelegramOidcConfig("fixture-client", "fixture-secret", "https://payments.example.invalid/auth/telegram/callback", proxy)

    def response(self, body):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = body
        response.headers = {}
        return response

    def test_only_explicit_numeric_loopback_http_proxy_is_accepted(self):
        self.config(None)
        self.config()
        for value in (
            "", "https://127.0.0.1:13128", "http://localhost:13128",
            "http://proxy.example.invalid:13128", "http://127.0.0.1",
            "http://127.0.0.1:0", "http://127.0.0.1:65536",
            "http://127.0.0.1:013128", "http://127.0.0.1:13128/",
            "http://user:pass@127.0.0.1:13128", "http://127.0.0.1:13128/?q=x",
            "http://127.0.0.1:13128#x", "http://127.0.0.1:13128\n",
        ):
            with self.subTest(value=value), self.assertRaises(TelegramOidcError):
                self.config(value)

    def test_explicit_proxy_cannot_be_bypassed_by_environment(self):
        handler = _TelegramOnlyProxy({"https": self.config().proxy_url})
        for url in (TELEGRAM_OIDC_TOKEN_URL, TELEGRAM_OIDC_JWKS_URL):
            with self.subTest(url=url), patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*", "HTTPS_PROXY": "http://other.invalid:1"}):
                request = Request(url)
                self.assertIsNone(handler.https_open(request))
                self.assertEqual("127.0.0.1:13128", request.host)
                self.assertEqual("oauth.telegram.org", request._tunnel_host)
                self.assertEqual("https", request.type)
                self.assertEqual("*", os.environ["NO_PROXY"])

    def test_proxy_rejects_other_hosts_paths_and_non_https(self):
        handler = _TelegramOnlyProxy({"https": self.config().proxy_url})
        for url in ("https://example.invalid/", "https://api.telegram.org/", "http://oauth.telegram.org/token", "https://oauth.telegram.org/token?q=x"):
            with self.subTest(url=url), self.assertRaises(TelegramOidcError):
                handler.proxy_open(Request(url), self.config().proxy_url, "https")

    def test_token_and_jwks_use_only_per_client_opener(self):
        opener = Mock()
        opener.open.side_effect = [self.response(b'{"id_token":"fixture-token"}'), self.response(b'{"keys":[]}')]
        with patch("services.telegram_oidc.build_opener", return_value=opener), patch("urllib.request.urlopen") as direct:
            client = TelegramOidcClient(self.config())
            self.assertEqual({"id_token": "fixture-token"}, client._request_tokens({"code": "fake-code"}))
            self.assertEqual({"keys": []}, client._fetch_jwks())
            direct.assert_not_called()
            self.assertEqual(TELEGRAM_OIDC_TOKEN_URL, opener.open.call_args_list[0].args[0].full_url)
            self.assertEqual(TELEGRAM_OIDC_JWKS_URL, opener.open.call_args_list[1].args[0])
            self.assertTrue(all(call.kwargs == {"timeout": 10} for call in opener.open.call_args_list))

    def test_proxy_failure_never_falls_back_or_exposes_provider_details(self):
        opener = Mock()
        opener.open.side_effect = URLError("fixture-private-provider-details")
        with patch("services.telegram_oidc.build_opener", return_value=opener), patch("urllib.request.urlopen") as direct:
            client = TelegramOidcClient(self.config())
            for operation in (lambda: client._request_tokens({"code": "fake-code"}), client._fetch_jwks):
                with self.assertRaises(TelegramOidcError) as raised:
                    operation()
                self.assertNotIn("fixture-private-provider-details", str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)
            direct.assert_not_called()

    def test_direct_mode_uses_own_opener_without_ambient_proxy_or_redirects(self):
        opener = Mock()
        opener.open.return_value = self.response(b'{"keys":[]}')
        with patch("services.telegram_oidc.build_opener", return_value=opener) as build:
            client = TelegramOidcClient(self.config(None))
            self.assertEqual({"keys": []}, client._fetch_jwks())
            opener.open.assert_called_once_with(TELEGRAM_OIDC_JWKS_URL, timeout=10)
            self.assertIsInstance(build.call_args.args[0], ProxyHandler)
            self.assertEqual({}, build.call_args.args[0].proxies)
            self.assertIsInstance(build.call_args.args[1], _NoOidcRedirects)

    def test_redirects_cannot_forward_oidc_credentials(self):
        handler = _NoOidcRedirects()
        request = Request(TELEGRAM_OIDC_TOKEN_URL, data=b"code=fixture", headers={"Authorization": "Basic fixture"})
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(handler.redirect_request(request, io.BytesIO(), status, "redirect", {}, "https://elsewhere.invalid/"))

    def test_real_opener_rejects_cross_origin_redirect_without_second_request(self):
        for proxy in (None, "http://127.0.0.1:13128"):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(proxy=proxy, status=status):
                    calls = []
                    class FakeHTTPS(HTTPSHandler):
                        handler_order = 399
                        def https_open(self, request):
                            calls.append(request.full_url)
                            headers = Message()
                            headers["Location"] = "https://elsewhere.invalid/"
                            response = addinfourl(io.BytesIO(b'{}'), headers, request.full_url, status)
                            response.msg = "Fixture redirect"
                            return response
                    client = TelegramOidcClient(self.config(proxy))
                    client._opener.add_handler(FakeHTTPS())
                    with patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}), self.assertRaises(TelegramOidcError):
                        client._request_tokens({"code": "fake-code"})
                    self.assertEqual([TELEGRAM_OIDC_TOKEN_URL], calls)

    def test_internal_transport_rejects_arbitrary_url(self):
        opener = Mock()
        with patch("services.telegram_oidc.build_opener", return_value=opener):
            client = TelegramOidcClient(self.config(None))
            with self.assertRaises(TelegramOidcError):
                client._open_oidc("https://prodamus.example.invalid/")
        opener.open.assert_not_called()

    def test_runtime_reads_the_optional_oidc_proxy_setting(self):
        from storage.website_purchase_runtime import build_website_purchase_runtime
        values = {
            "WEBSITE_PURCHASE_BASE_URL": "https://payments.example.invalid",
            "TELEGRAM_BOT_URL": "https://t.me/FixtureBot",
            "TELEGRAM_OIDC_CLIENT_ID": "fixture-client",
            "TELEGRAM_OIDC_CLIENT_SECRET": "fixture-secret",
            "TELEGRAM_OIDC_PROXY_URL": "http://127.0.0.1:13128",
            "PRODAMUS_PAYFORM_URL": "https://fixture.payform.ru",
            "PRODAMUS_SECRET_KEY": "fixture-payment-secret",
        }
        settings = Mock(side_effect=values.__getitem__)
        settings.optional.side_effect = values.get
        with patch("storage.website_purchase_runtime.PrivateOffer.from_settings"), patch("storage.website_purchase_runtime.TelegramOidcClient") as client:
            build_website_purchase_runtime(settings)
        self.assertEqual("http://127.0.0.1:13128", client.call_args.args[0].proxy_url)

    def test_identity_and_gzip_json_responses_are_both_supported(self):
        for value in (b'{"keys":[]}', b'{"id_token":"fixture-token"}'):
            response = self.response(value)
            self.assertIsInstance(_read_oidc_json(response), dict)
            response = self.response(gzip.compress(value))
            response.headers = {"Content-Encoding": "gzip"}
            self.assertIsInstance(_read_oidc_json(response), dict)

    def test_compressed_and_wire_size_limits_fail_closed(self):
        for body, encoding in (
            (b"x" * 262145, "identity"),
            (gzip.compress(b"x" * 262145), "gzip"),
            (gzip.compress(b'{}')[:-2], "gzip"),
            (gzip.compress(b'{}') + gzip.compress(b'{}'), "gzip"),
            (b'{}', "br"),
        ):
            with self.subTest(encoding=encoding, size=len(body)):
                response = self.response(body)
                response.headers = {"Content-Encoding": encoding}
                with self.assertRaises(TelegramOidcError):
                    _read_oidc_json(response)
