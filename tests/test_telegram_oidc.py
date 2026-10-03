import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select

from db.migrations import run_migrations
from db.models import SqliteSession, User, UserAccess
from services.prodamus import ProdamusConfig, ProdamusService, build_prodamus_signature
from services.telegram_oidc import (
    TelegramOidcClient,
    TelegramOidcConfig,
    TelegramOidcError,
)
from services.website_purchase import WebsitePurchaseError, WebsitePurchaseService


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class TelegramOidcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [{
            "kty": "RSA",
            "kid": "telegram-test-key",
            "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
            "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
        }]}
        self.config = TelegramOidcConfig(
            "123456",
            "not-a-real-secret",
            "https://payments.traineros.ru/auth/telegram/callback",
        )

    def token(self, *, nonce="nonce", subject="777", expires=NOW + timedelta(minutes=5)):
        header = _b64(json.dumps({"alg": "RS256", "kid": "telegram-test-key"}, separators=(",", ":")).encode())
        claims = _b64(json.dumps({
            "iss": "https://oauth.telegram.org", "aud": "123456", "exp": int(expires.timestamp()),
            "nonce": nonce, "sub": subject, "name": "Telegram Buyer", "preferred_username": "buyer",
        }, separators=(",", ":")).encode())
        signed = f"{header}.{claims}".encode()
        signature = self.private_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_b64(signature)}"

    def client(self, token):
        return TelegramOidcClient(
            self.config,
            token_request=lambda fields: {"id_token": token},
            jwks_fetcher=lambda: self.jwks,
            now_factory=lambda: NOW,
        )

    def test_valid_authorization_code_flow_validates_signature_claims_nonce_and_pkce(self):
        client = self.client(self.token())
        verifier = "v" * 43
        url = client.authorization_url(state="state", nonce="nonce", verifier=verifier)
        query = parse_qs(urlparse(url).query)
        self.assertEqual("S256", query["code_challenge_method"][0])
        self.assertEqual("openid profile", query["scope"][0])
        identity = client.exchange_code(code="authorization-code", verifier=verifier, expected_nonce="nonce")
        self.assertEqual(777, identity.telegram_user_id)
        self.assertEqual("buyer", identity.username)

    def test_invalid_or_expired_id_token_is_rejected(self):
        for token, nonce in (
            (self.token(nonce="other"), "nonce"),
            (self.token(expires=NOW - timedelta(seconds=1)), "nonce"),
            (self.token(expires=NOW), "nonce"),
        ):
            with self.subTest(token=token[:10]):
                with self.assertRaises(TelegramOidcError):
                    self.client(token).exchange_code(code="code", verifier="v" * 43, expected_nonce=nonce)

    def test_server_session_binds_identity_once_and_creates_entitlement_owner(self):
        temp = tempfile.TemporaryDirectory()
        database = SqliteSession(sqlite_url(Path(temp.name) / "oidc.db"))
        try:
            self.assertEqual(tuple(range(1, 18)), run_migrations(database.engine))
            prodamus = ProdamusService(
                ProdamusConfig(
                    "https://trainer.payform.ru/", "test-prodamus-secret-key-not-production",
                    "https://payments.traineros.ru/webhooks/prodamus",
                    "https://payments.traineros.ru/payment/success",
                    "https://payments.traineros.ru/payment/fail", "https://payments.traineros.ru/buy",
                ), session_factory=database, now_factory=lambda: NOW.replace(tzinfo=None),
                transaction_id_factory=lambda: "prodamus-oidc-1",
            )
            service = WebsitePurchaseService(
                self.client(self.token()), prodamus, session_factory=database,
                now_factory=lambda: NOW.replace(tzinfo=None),
            )
            started = service.start_login()
            query = parse_qs(urlparse(started.authorization_url).query)
            state = query["state"][0]
            nonce = query["nonce"][0]
            service._oidc._token_request = lambda fields: {"id_token": self.token(nonce=nonce)}
            identity = service.complete_login(started.session_token, state, "code")
            self.assertEqual(777, identity.telegram_user_id)
            with self.assertRaises(WebsitePurchaseError):
                service.complete_login(started.session_token, state, "code")
            order = service.create_order(started.session_token, "buyer@example.com")
            self.assertTrue(order.checkout_url.startswith("https://trainer.payform.ru/"))
            payload = {
                "order_id": "provider-oidc-operation-1",
                "order_num": order.transaction_id,
                "sum": "499.00",
                "customer_email": "buyer@example.com",
                "payment_status": "success",
            }
            result = service.process_prodamus_notification(
                payload,
                build_prodamus_signature(payload, "test-prodamus-secret-key-not-production"),
            )
            self.assertTrue(result.access_applied)
            with database() as session:
                user = session.scalar(select(User).where(User.tg_id == 777))
                self.assertIsNotNone(user)
                access = session.get(UserAccess, user.id)
                self.assertIsNotNone(access)
                self.assertEqual(NOW.replace(tzinfo=None) + timedelta(days=30), access.subscription_ends_at)
        finally:
            database.dispose()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
