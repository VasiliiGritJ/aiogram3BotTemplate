"""Local activation contract: fake OIDC and temporary SQLite, no network."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from db.migrations import run_migrations
from db.models import SqliteSession, SubscriptionPayment, User, WebsitePurchaseSession
from services.payment_domain import PaymentStatus
from services.prodamus import ProdamusConfig, ProdamusService, build_prodamus_signature
from services.telegram_oidc import TelegramIdentity
from services.website_purchase import WebsitePurchaseError, WebsitePurchaseService


SECRET = "fixture-prodamus-secret-not-production"
NOW = datetime(2026, 10, 3, 12)


class FakeOidc:
    barrier = None
    after_exchange = None

    def authorization_url(self, *, state, nonce, verifier):
        return f"https://example.invalid/auth?state={state}"

    def exchange_code(self, **kwargs):
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if self.after_exchange is not None:
            self.after_exchange()
        return TelegramIdentity(777, "Fixture Buyer", "fixture")


class WebsitePurchaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteSession(f"sqlite:///{Path(self.temp.name).as_posix()}/purchase.db")
        run_migrations(self.db.engine)
        self.now = NOW
        self.oidc = FakeOidc()
        base = "https://payments.example.invalid"
        self.provider = ProdamusService(
            ProdamusConfig("https://fixture.payform.ru", SECRET, f"{base}/webhooks/prodamus",
                           f"{base}/payment/success", f"{base}/payment/fail", f"{base}/buy"),
            session_factory=self.db, now_factory=lambda: self.now,
        )
        self.service = WebsitePurchaseService(self.oidc, self.provider, session_factory=self.db,
                                              now_factory=lambda: self.now)

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()

    def start(self):
        started = self.service.start_login()
        state = parse_qs(urlparse(started.authorization_url).query)["state"][0]
        return started.session_token, state

    def login(self):
        token, state = self.start()
        self.service.complete_login(token, state, "fake-code")
        return token

    def test_email_is_bound_to_server_owned_telegram_user_not_client_identity(self):
        token = self.login()
        order = self.service.create_order(token, " Buyer@Example.com ")
        with self.db() as session:
            user = session.scalar(select(User))
            payment = session.get(SubscriptionPayment, order.payment_id)
            self.assertEqual(777, user.tg_id)
            self.assertEqual(user.id, payment.user_id)
            self.assertEqual("buyer@example.com", payment.buyer_email)
            self.assertEqual(49900, payment.amount_minor)
        with self.assertRaises(WebsitePurchaseError):
            self.service.create_order(token, "other@example.com")
        with self.assertRaises(WebsitePurchaseError):
            self.service.create_order(token, "invalid-email")

    def test_anonymous_wrong_state_and_expired_session_cannot_create_order(self):
        token, state = self.start()
        with self.assertRaises(WebsitePurchaseError):
            self.service.create_order(token, "buyer@example.com")
        with self.assertRaises(WebsitePurchaseError):
            self.service.complete_login(token, "wrong-state", "fake-code")
        self.now += timedelta(minutes=11)
        with self.assertRaises(WebsitePurchaseError):
            self.service.complete_login(token, state, "fake-code")
        with self.db() as session:
            self.assertIsNone(session.scalar(select(SubscriptionPayment)))

    def test_session_expiring_during_token_exchange_is_not_authenticated(self):
        token, state = self.start()
        self.oidc.after_exchange = lambda: setattr(self, "now", NOW + timedelta(minutes=11))
        with self.assertRaises(WebsitePurchaseError):
            self.service.complete_login(token, state, "fake-code")
        self.assertFalse(self.service.is_authenticated(token))

    def test_concurrent_oidc_replay_consumes_session_exactly_once(self):
        token, state = self.start()
        self.oidc.barrier = Barrier(2)
        def authenticate(_):
            try:
                self.service.complete_login(token, state, "fake-code")
                return True
            except WebsitePurchaseError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(authenticate, range(2)))
        self.assertEqual(1, sum(results))
        with self.db() as session:
            self.assertEqual(1, len(session.scalars(select(User)).all()))

    def test_redirect_before_and_after_webhook_only_reads_the_bound_order(self):
        token = self.login()
        order = self.service.create_order(token, "buyer@example.com")
        self.assertEqual(PaymentStatus.PENDING, self.service.inspect_order_status(token))
        payload = {"order_id": "operation-fixture", "order_num": order.transaction_id,
                   "sum": "499.00", "customer_email": "buyer@example.com", "payment_status": "success"}
        signature = build_prodamus_signature(payload, SECRET)
        self.service.process_prodamus_notification(payload, signature)
        self.assertEqual(PaymentStatus.SUCCEEDED, self.service.inspect_order_status(token))
        self.assertFalse(self.service.process_prodamus_notification(payload, signature).access_applied)
        # A different purchase session does not inherit a transaction query ID.
        another_token = self.login()
        self.assertIsNone(self.service.inspect_order_status(another_token))
        self.now += timedelta(minutes=11)
        with self.assertRaises(WebsitePurchaseError):
            self.service.inspect_order_status(token)

    def test_sessions_store_only_hashed_browser_token(self):
        token, _ = self.start()
        with self.db() as session:
            record = session.scalar(select(WebsitePurchaseSession))
            self.assertNotEqual(token, record.session_token_hash)


if __name__ == "__main__":
    unittest.main()
