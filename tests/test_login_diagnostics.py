"""Value-free callback diagnostics: temporary DB, fake codes and signed fixtures."""

import asyncio
import json
import subprocess
import sys
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from aiohttp import web
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from services.telegram_oidc import TelegramOidcError, TelegramOidcFailure
from services.website_purchase import WebsiteLoginError, WebsiteLoginFailure, WebsitePurchaseError
from storage.website_purchase_http import SESSION_COOKIE, _telegram_callback_handler
from storage.website_purchase_runtime import WebsitePurchaseRuntime
from tests import test_telegram_oidc as oidc_fixtures
from tests import test_website_purchase as purchase_fixtures


class LoginDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = purchase_fixtures.WebsitePurchaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def invoke(self, token, state, code="FAKE_CODE_PRIVATE_MARKER", service=None):
        request = SimpleNamespace(
            cookies={SESSION_COOKIE: token},
            query={"state": state, "code": code, "error_description": "PRIVATE_PROVIDER_DETAIL"},
            headers={"Authorization": "PRIVATE_HEADER_MARKER"},
        )
        runtime = WebsitePurchaseRuntime(service or self.fixture.service,
                                         "https://example.invalid", "https://t.me/GritJBot")
        with self.assertLogs("storage.website_purchase_http", level="INFO") as captured:
            try:
                response = asyncio.run(_telegram_callback_handler(runtime)(request))
            except web.HTTPFound as redirect:
                response = redirect
        logs = "\n".join(captured.output)
        for value in (token, state, code, "PRIVATE_PROVIDER_DETAIL", "PRIVATE_HEADER_MARKER"):
            if value:
                self.assertNotIn(value, logs)
        self.assertNotIn("Traceback", logs)
        return response, logs

    def test_valid_callback_logs_success_without_identity_or_secrets(self):
        token, state = self.fixture.start()
        response, logs = self.invoke(token, state)
        self.assertEqual(302, response.status)
        self.assertEqual("/buy", response.location)
        self.assertIn("event=callback_observed cookie_present=True", logs)
        self.assertIn("event=login_completed", logs)
        self.assertNotIn("Fixture Buyer", logs)
        self.assertTrue(self.fixture.service.is_authenticated(token))

    def test_default_standalone_logging_emits_safe_diagnostics_without_configuration(self):
        code = '''import asyncio
from types import SimpleNamespace
from services.website_purchase import WebsiteLoginError,WebsiteLoginFailure
from storage.website_purchase_http import _telegram_callback_handler
from storage.website_purchase_runtime import WebsitePurchaseRuntime
class Service:
 def complete_login(self,*args):raise WebsiteLoginError(WebsiteLoginFailure.COOKIE_MISSING)
request=SimpleNamespace(cookies={},query={})
runtime=WebsitePurchaseRuntime(Service(),'https://example.invalid','https://t.me/GritJBot')
response=asyncio.run(_telegram_callback_handler(runtime)(request))
assert response.status==400
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        self.assertIn("website_login event=callback_observed cookie_present=False", result.stderr)
        self.assertIn("reason=cookie_missing", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual("", result.stdout)

    def test_missing_cookie_and_unknown_session_are_distinct_without_exchange(self):
        _, state = self.fixture.start()
        with patch.object(self.fixture.oidc, "exchange_code") as exchange:
            for token, reason in (("", "cookie_missing"), ("FAKE_UNKNOWN_COOKIE", "session_unknown")):
                response, logs = self.invoke(token, state)
                self.assertEqual(400, response.status)
                self.assertIn("stage=session_before_exchange reason=" + reason, logs)
            exchange.assert_not_called()

    def test_different_browser_cookie_is_state_mismatch_not_token_failure(self):
        first_token, first_state = self.fixture.start()
        other_token, _ = self.fixture.start()
        with patch.object(self.fixture.oidc, "exchange_code") as exchange:
            response, logs = self.invoke(other_token, first_state)
            self.assertEqual(400, response.status)
            self.assertIn("reason=state_mismatch", logs)
            exchange.assert_not_called()
        self.assertFalse(self.fixture.service.is_authenticated(first_token))
        self.assertFalse(self.fixture.service.is_authenticated(other_token))

    def test_expired_session_and_replay_are_distinct(self):
        token, state = self.fixture.start()
        self.fixture.service.complete_login(token, state, "FAKE_CODE")
        with patch.object(self.fixture.oidc, "exchange_code") as exchange:
            response, logs = self.invoke(token, state)
            self.assertEqual(400, response.status)
            self.assertIn("reason=callback_replayed", logs)
            exchange.assert_not_called()
        self.fixture.now += timedelta(minutes=11)
        response, logs = self.invoke(token, state)
        self.assertEqual(400, response.status)
        self.assertIn("reason=session_expired", logs)

    def test_expiry_during_exchange_records_post_exchange_stage(self):
        token, state = self.fixture.start()
        self.fixture.oidc.after_exchange = lambda: setattr(self.fixture, "now", purchase_fixtures.NOW + timedelta(minutes=11))
        response, logs = self.invoke(token, state)
        self.assertEqual(400, response.status)
        self.assertIn("stage=session_after_exchange reason=session_expired", logs)
        self.assertFalse(self.fixture.service.is_authenticated(token))

    def test_oidc_failure_reason_propagates_but_raw_exception_does_not(self):
        token, state = self.fixture.start()
        for reason in (TelegramOidcFailure.TOKEN_REJECTED, TelegramOidcFailure.NONCE,
                       TelegramOidcFailure.JWKS, TelegramOidcFailure.SIGNATURE,
                       TelegramOidcFailure.SUBJECT, TelegramOidcFailure.PROFILE_ID_MISSING,
                       TelegramOidcFailure.PROFILE_ID_STRING, TelegramOidcFailure.PROFILE_ID_TYPE,
                       TelegramOidcFailure.PROFILE_ID_RANGE):
            with patch.object(self.fixture.oidc, "exchange_code", side_effect=TelegramOidcError("PRIVATE_PROVIDER_DETAIL", reason=reason)):
                response, logs = self.invoke(token, state)
            self.assertEqual(400, response.status)
            self.assertIn("stage=exchange_or_identity reason=oidc_rejected oidc_reason=" + reason.value, logs)
            self.assertNotIn("PRIVATE_PROVIDER_DETAIL", logs)
            self.assertFalse(self.fixture.service.is_authenticated(token))

    def test_arbitrary_reason_stage_and_unexpected_exceptions_cannot_enter_logs(self):
        service = Mock()
        error = WebsiteLoginError(WebsiteLoginFailure.OIDC)
        error.stage = "PRIVATE_STAGE_MARKER"
        error.reason = "PRIVATE_REASON_MARKER"
        error.oidc_reason = "PRIVATE_OIDC_MARKER"
        for failure, status in ((error, 400), (WebsitePurchaseError("PRIVATE_EXCEPTION_MARKER"), 400),
                                (RuntimeError("PRIVATE_EXCEPTION_MARKER"), 503)):
            service.complete_login.side_effect = failure
            response, logs = self.invoke("FAKE_COOKIE", "FAKE_STATE", service=service)
            self.assertEqual(status, response.status)
            for forbidden in ("PRIVATE_STAGE_MARKER", "PRIVATE_REASON_MARKER", "PRIVATE_OIDC_MARKER", "PRIVATE_EXCEPTION_MARKER"):
                self.assertNotIn(forbidden, logs)
                self.assertNotIn(forbidden, response.text)


class OidcFailureCategoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = oidc_fixtures.TelegramOidcTests()
        self.fixture.setUp()

    def assert_reason(self, client, expected, *, code="FAKE_CODE"):
        with self.assertRaises(TelegramOidcError) as caught:
            client.exchange_code(code=code, verifier="v" * 43, expected_nonce="nonce")
        self.assertEqual(expected, caught.exception.reason)
        return caught.exception

    def test_provider_error_http_200_is_not_misclassified_as_missing_token(self):
        client = self.fixture.client(self.fixture.token())
        client._token_request = lambda fields: {"error": "PRIVATE_PROVIDER_ERROR", "error_description": "PRIVATE_DETAIL"}
        error = self.assert_reason(client, TelegramOidcFailure.TOKEN_REJECTED)
        self.assertNotIn("PRIVATE", str(error))

    def test_nonce_expiry_identity_and_signature_failures_are_separate(self):
        cases = ((self.fixture.token(nonce="different"), TelegramOidcFailure.NONCE),
                 (self.fixture.token(expires=oidc_fixtures.NOW), TelegramOidcFailure.EXPIRY),
                 (self.fixture.token(telegram_id=None), TelegramOidcFailure.PROFILE_ID_MISSING),
                 ("not.a.valid-token", TelegramOidcFailure.TOKEN_FORMAT))
        for token, reason in cases:
            self.assert_reason(self.fixture.client(token), reason)
        parts = self.fixture.token().split(".")
        parts[2] = oidc_fixtures._b64(b"invalid-signature")
        self.assert_reason(self.fixture.client(".".join(parts)), TelegramOidcFailure.SIGNATURE)

    def test_issuer_and_audience_reasons_still_fail_closed(self):
        for field, value, reason in (("iss", "https://evil.invalid", TelegramOidcFailure.ISSUER),
                                     ("aud", "other-client", TelegramOidcFailure.AUDIENCE)):
            parts = self.fixture.token().split(".")
            import base64
            claims=json.loads(base64.urlsafe_b64decode(parts[1]+"="*(-len(parts[1])%4)))
            claims[field]=value
            parts[1]=oidc_fixtures._b64(json.dumps(claims).encode())
            parts[2]=oidc_fixtures._b64(self.fixture.private_key.sign(".".join(parts[:2]).encode(),padding.PKCS1v15(),hashes.SHA256()))
            self.assert_reason(self.fixture.client(".".join(parts)),reason)

    def test_transport_http_and_body_decode_failures_are_separate_and_suppressed(self):
        client = self.fixture.client(self.fixture.token())
        # Exercise the actual client consumer, not the injected token fixture.
        client._token_request = client._request_tokens
        for error, reason in ((URLError("PRIVATE_NETWORK_ERROR"), TelegramOidcFailure.TOKEN_TRANSPORT),
                              (HTTPError("https://example.invalid/PRIVATE",400,"PRIVATE_HTTP_ERROR",{},None),TelegramOidcFailure.TOKEN_HTTP)):
            with patch.object(client,"_open_oidc",side_effect=error):
                caught=self.assert_reason(client,reason)
                self.assertTrue(caught.__suppress_context__)
                self.assertNotIn("PRIVATE",str(caught))
        with patch.object(client,"_open_oidc") as opening:
            opening.return_value.__enter__.return_value.headers={}
            opening.return_value.__enter__.return_value.read.return_value=b"INVALID_JSON_PRIVATE"
            self.assert_reason(client,TelegramOidcFailure.TOKEN_RESPONSE)

    def test_unknown_error_reason_cannot_become_freeform_diagnostic(self):
        error=TelegramOidcError("safe message",reason="PRIVATE_REASON_MARKER")
        self.assertEqual(TelegramOidcFailure.UNKNOWN,error.reason)

    def test_signed_identity_rejection_predicates_are_distinct_without_values(self):
        for subject in (None, 123, True, "", " ", "x" * 256):
            with self.subTest(case="invalid-subject", type=type(subject).__name__):
                self.assert_reason(self.fixture.client(self.fixture.token(subject=subject)), TelegramOidcFailure.SUBJECT)
        for value, reason in (
            (None, TelegramOidcFailure.PROFILE_ID_MISSING),
            ("00777", TelegramOidcFailure.PROFILE_ID_TYPE),
            ("+777", TelegramOidcFailure.PROFILE_ID_TYPE),
            ("PRIVATE_ID_MARKER", TelegramOidcFailure.PROFILE_ID_TYPE),
            ("٧٧٧", TelegramOidcFailure.PROFILE_ID_TYPE),
            (777.0, TelegramOidcFailure.PROFILE_ID_TYPE),
            (True, TelegramOidcFailure.PROFILE_ID_TYPE),
            ({"id":777}, TelegramOidcFailure.PROFILE_ID_TYPE),
            (0, TelegramOidcFailure.PROFILE_ID_RANGE),
            (-1, TelegramOidcFailure.PROFILE_ID_RANGE),
            (2**63, TelegramOidcFailure.PROFILE_ID_RANGE),
            (str(2**63), TelegramOidcFailure.PROFILE_ID_RANGE),
            ("0", TelegramOidcFailure.PROFILE_ID_RANGE),
            ("9" * 1000, TelegramOidcFailure.PROFILE_ID_RANGE),
        ):
            with self.subTest(reason=reason):
                error=self.assert_reason(self.fixture.client(self.fixture.token(telegram_id=value)),reason)
                self.assertEqual("Telegram identity is invalid.",str(error))

    def test_absent_profile_id_never_falls_back_to_signed_numeric_subject(self):
        import base64
        parts=self.fixture.token(subject="777").split(".")
        claims=json.loads(base64.urlsafe_b64decode(parts[1]+"="*(-len(parts[1])%4)))
        del claims["id"]
        parts[1]=oidc_fixtures._b64(json.dumps(claims).encode())
        parts[2]=oidc_fixtures._b64(self.fixture.private_key.sign(".".join(parts[:2]).encode(),padding.PKCS1v15(),hashes.SHA256()))
        self.assert_reason(self.fixture.client(".".join(parts)),TelegramOidcFailure.PROFILE_ID_MISSING)

    def test_bad_signature_is_rejected_before_identity_shape_diagnosis(self):
        parts=self.fixture.token(telegram_id="PRIVATE_ID_MARKER").split(".")
        parts[2]=oidc_fixtures._b64(b"invalid-signature")
        self.assert_reason(self.fixture.client(".".join(parts)),TelegramOidcFailure.SIGNATURE)


if __name__ == "__main__":
    unittest.main()
