"""Public/off/controlled checkout: temporary DB, fake OIDC, no provider calls."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import select

from db.models import PaymentReceipt, SubscriptionPayment, User, UserAccess, WebsitePurchaseSession
from services.access import activate_trial_once
from services.payment_domain import PaymentStatus
from services.prodamus import ProdamusService, build_prodamus_signature
from services.telegram_oidc import TelegramIdentity
from services.website_purchase import ControlledCheckoutGate, PublicCheckoutGate, WebsitePurchaseError, WebsitePurchaseService
from storage.website_purchase_runtime import build_website_purchase_runtime, WebsitePurchaseRuntimeConfigurationError
from tests import test_website_purchase as fixtures
from tests.test_website_purchase_runtime import COMPLETE_CONFIG, settings


class PublicCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WebsitePurchaseTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def payments(self):
        with self.f.db() as session:
            return session.scalars(select(SubscriptionPayment).order_by(SubscriptionPayment.id)).all()

    def payload(self, order, operation="fixture-operation"):
        return dict(order_id=operation, order_num=order.transaction_id, sum="499.00",
                    currency="rub", domain="fixture.payform.ru", customer_email="buyer@example.com",
                    payment_status="success")

    def confirm(self, order, operation="fixture-operation"):
        payload = self.payload(order, operation)
        return self.f.provider.process_notification(payload, build_prodamus_signature(payload, fixtures.SECRET))

    def test_all_three_explicit_modes_and_missing_mode_closed(self):
        for mode in (None, "off", "controlled", "public"):
            values = dict(COMPLETE_CONFIG)
            if mode is not None:
                values["WEBSITE_CHECKOUT_MODE"] = mode
            if mode == "controlled":
                values.update(WEBSITE_CHECKOUT_TELEGRAM_USER_ID="777",
                              WEBSITE_CHECKOUT_EXPIRES_AT="2026-10-03T12:05:00Z")
            gate = build_website_purchase_runtime(settings(values)).service._checkout_gate
            self.assertEqual(mode in ("controlled", "public"), gate.allows(777, fixtures.NOW))
            self.assertEqual(mode == "public", gate.allows(778, fixtures.NOW))

    def test_bad_configuration_cannot_enable_public_or_echo_values(self):
        for mode in ("PUBLIC", "public,controlled", "pub\u200blic", "true", "unknown-fixture-sensitive"):
            with self.subTest(mode=mode):
                with self.assertRaises(WebsitePurchaseRuntimeConfigurationError) as error:
                    build_website_purchase_runtime(settings(dict(COMPLETE_CONFIG, WEBSITE_CHECKOUT_MODE=mode)))
                self.assertNotIn(mode, str(error.exception))
        gate = build_website_purchase_runtime(settings(dict(COMPLETE_CONFIG, WEBSITE_CHECKOUT_MODE=""))).service._checkout_gate
        self.assertFalse(gate.allows(777, fixtures.NOW))
        # The common SettingsReader strips surrounding whitespace for every
        # setting; this is still the explicit normalized value, not a fallback.
        gate = build_website_purchase_runtime(settings(dict(COMPLETE_CONFIG, WEBSITE_CHECKOUT_MODE=" public "))).service._checkout_gate
        self.assertTrue(gate.allows(777, fixtures.NOW))
        for name in ("TELEGRAM_OIDC_CLIENT_SECRET", "PRODAMUS_SECRET_KEY", "SELLER_RESIDENCE_PRIVATE"):
            values = dict(COMPLETE_CONFIG, WEBSITE_CHECKOUT_MODE="public")
            del values[name]
            with self.assertRaises(WebsitePurchaseRuntimeConfigurationError):
                build_website_purchase_runtime(settings(values))

    def test_service_default_is_closed_and_public_rejects_bad_identity(self):
        token = self.f.login()
        default = WebsitePurchaseService(self.f.oidc, self.f.provider, session_factory=self.f.db,
                                         now_factory=lambda: self.f.now)
        with self.assertRaises(WebsitePurchaseError):
            default.create_order(token, "buyer@example.com")
        for identity in (None, True, "777", 0, -1, 2**63):
            self.assertFalse(PublicCheckoutGate().allows(identity, fixtures.NOW))
        self.assertEqual([], self.payments())

    def test_unauthenticated_and_expired_sessions_never_create(self):
        token, _ = self.f.start()
        for candidate in ("", "forged", token):
            with self.assertRaises(WebsitePurchaseError):
                self.f.service.create_order(candidate, "buyer@example.com")
        token = self.f.login()
        self.f.now += timedelta(minutes=11)
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        self.assertEqual([], self.payments())

    def test_public_buyers_are_bound_separately_and_keep_server_owned_product(self):
        token = self.f.login()
        one = self.f.service.create_order(token, "buyer@example.com")
        with patch.object(self.f.oidc, "exchange_code", return_value=TelegramIdentity(778, "Fixture Other", "")):
            other = self.f.login()
        two = self.f.service.create_order(other, "other@example.com")
        payments = self.payments()
        self.assertNotEqual(payments[0].user_id, payments[1].user_id)
        self.assertNotEqual(one.payment_id, two.payment_id)
        for payment in payments:
            self.assertEqual((49900, "RUB", 30), (payment.amount_minor, payment.currency, payment.period_days))
        self.assertEqual("buyer@example.com", payments[0].buyer_email)
        self.assertEqual("other@example.com", payments[1].buyer_email)

    def test_concurrent_sessions_and_provider_instances_reuse_one_pending_order(self):
        tokens = [self.f.login(), self.f.login()]
        providers = [ProdamusService(self.f.provider._config, session_factory=self.f.db,
                                     now_factory=lambda: self.f.now) for _ in range(2)]
        services = [WebsitePurchaseService(self.f.oidc, p, session_factory=self.f.db,
                                          now_factory=lambda: self.f.now, checkout_gate=PublicCheckoutGate())
                    for p in providers]
        with ThreadPoolExecutor(max_workers=4) as pool:
            orders = list(pool.map(lambda i: services[i % 2].create_order(tokens[i % 2], "buyer@example.com"), range(8)))
        self.assertEqual(1, len({o.payment_id for o in orders}))
        self.assertEqual(1, sum(o.created for o in orders))
        self.assertEqual(1, len(self.payments()))
        for token in tokens:
            self.assertEqual(PaymentStatus.PENDING, self.f.service.inspect_order_status(token))

    def test_success_replay_and_new_login_do_not_create_second_purchase(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        self.assertTrue(self.confirm(order).access_applied)
        self.assertFalse(self.confirm(order).access_applied)
        for current in (token, self.f.login()):
            self.assertTrue(self.f.service.has_paid_access(current))
            for _ in range(2):
                with self.assertRaises(WebsitePurchaseError):
                    self.f.service.create_order(current, "buyer@example.com")
        self.assertEqual(1, len(self.payments()))
        with self.f.db() as session:
            self.assertEqual(1, len(session.scalars(select(PaymentReceipt)).all()))

    def test_confirmation_winning_creation_race_cannot_create_another_order(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        another = self.f.login()
        original = self.f.provider.create_order
        def confirmed_before_locked_creation(*args, **kwargs):
            self.confirm(order)
            return original(*args, **kwargs)
        with patch.object(self.f.provider, "create_order", side_effect=confirmed_before_locked_creation):
            with self.assertRaises(WebsitePurchaseError):
                self.f.service.create_order(another, "buyer@example.com")
        self.assertEqual(1, len(self.payments()))

    def test_old_pending_is_not_cancelled_or_replaced_and_never_grants_access(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        self.f.now += timedelta(days=1)
        fresh = self.f.login()
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(fresh, "buyer@example.com")
        payment = self.payments()[0]
        self.assertEqual(order.payment_id, payment.id)
        self.assertEqual("pending", payment.status)
        self.assertIsNone(payment.access_applied_at)
        with self.f.db() as session:
            self.assertIsNone(session.get(UserAccess, payment.user_id).subscription_ends_at)

    def test_corrupt_or_other_provider_pending_fails_closed_without_second_order(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        for field, value in (("amount_minor", 10000), ("currency", "USD"), ("period_days", 31),
                             ("provider", "payanyway"), ("confirmation_url", "https://example.invalid/forged")):
            with self.f.db() as session, session.begin():
                payment = session.get(SubscriptionPayment, order.payment_id)
                old = getattr(payment, field)
                setattr(payment, field, value)
            with self.subTest(field=field), self.assertRaises(WebsitePurchaseError):
                self.f.service.create_order(token, "buyer@example.com")
            with self.f.db() as session, session.begin():
                setattr(session.get(SubscriptionPayment, order.payment_id), field, old)
        self.assertEqual(1, len(self.payments()))

    def test_terminal_order_post_requires_new_intent_and_invalid_email_rejected(self):
        token = self.f.login()
        for email in ("", "not-email"):
            with self.assertRaises(WebsitePurchaseError):
                self.f.service.create_order(token, email)
        order = self.f.service.create_order(token, "buyer@example.com")
        with self.f.db() as session, session.begin():
            session.get(SubscriptionPayment, order.payment_id).status = "failed"
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        self.assertEqual(1, len(self.payments()))

    def test_trial_preserved_and_paid_future_period_blocks_accidental_repurchase(self):
        token = self.f.login()
        with self.f.db() as session:
            user_id = session.scalar(select(User.id))
        activate_trial_once(user_id, self.f.now, self.f.db)
        order = self.f.service.create_order(token, "buyer@example.com")
        self.assertTrue(self.confirm(order).access_applied)
        with self.f.db() as session:
            access = session.get(UserAccess, user_id)
            self.assertEqual(fixtures.NOW + timedelta(days=3), access.trial_ends_at)
            self.assertEqual(fixtures.NOW + timedelta(days=33), access.subscription_ends_at)
            self.assertEqual(fixtures.NOW, access.trial_consumed_at)
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(self.f.login(), "buyer@example.com")

    def test_expired_paid_user_can_buy_new_period_without_restarting_trial(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        self.confirm(order)
        self.f.now += timedelta(days=31)
        token = self.f.login()
        next_order = self.f.service.create_order(token, "buyer@example.com")
        self.assertNotEqual(order.payment_id, next_order.payment_id)
        self.assertTrue(self.confirm(next_order, "next-operation").access_applied)
        with self.f.db() as session:
            access = session.get(UserAccess, self.payments()[0].user_id)
            self.assertEqual(self.f.now + timedelta(days=30), access.subscription_ends_at)
            self.assertIsNone(access.trial_started_at)

    def test_session_expiring_before_provider_transaction_never_creates_order(self):
        token = self.f.login()
        original = self.f.provider.create_order
        def delayed(*args, **kwargs):
            self.f.now += timedelta(minutes=10)
            return original(*args, **kwargs)
        with patch.object(self.f.provider, "create_order", side_effect=delayed):
            with self.assertRaises(WebsitePurchaseError):
                self.f.service.create_order(token, "buyer@example.com")
        self.assertEqual([], self.payments())

    def test_off_blocks_creation_but_not_authenticated_provider_confirmation(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        self.f.service._checkout_gate = ControlledCheckoutGate()
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        self.assertTrue(self.confirm(order).access_applied)
        self.assertFalse(self.confirm(order).access_applied)

    def test_actual_callback_and_creation_race_never_duplicates_order_or_grant(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        tokens = [token, self.f.login()]
        def attempt(index):
            if index == 0:
                return self.confirm(order).accepted
            try:
                return self.f.service.create_order(tokens[index % 2], "buyer@example.com").payment_id == order.payment_id
            except WebsitePurchaseError:
                return True  # Confirmation won; already-paid creation is closed.
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(attempt, range(8))))
        self.assertEqual(1, len(self.payments()))
        self.assertFalse(self.confirm(order).access_applied)
        with self.f.db() as session:
            access = session.get(UserAccess, self.payments()[0].user_id)
            self.assertEqual(fixtures.NOW + timedelta(days=30), access.subscription_ends_at)
            self.assertEqual(1, len(session.scalars(select(PaymentReceipt)).all()))

    def test_wrong_session_order_and_changed_email_cannot_replace_purchase_binding(self):
        token = self.f.login()
        self.f.service.create_order(token, "buyer@example.com")
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "other@example.com")
        with patch.object(self.f.oidc, "exchange_code", return_value=TelegramIdentity(778, "Other", "")):
            other = self.f.login()
        other_order = self.f.service.create_order(other, "other@example.com")
        # Simulate corrupted server state, never a client-controlled parameter.
        from hashlib import sha256
        with self.f.db() as session, session.begin():
            session.get(WebsitePurchaseSession, sha256(token.encode()).hexdigest()).payment_id = other_order.payment_id
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        self.assertEqual(2, len(self.payments()))

    def test_corrupt_future_paid_end_and_future_pending_time_fail_closed(self):
        token = self.f.login()
        order = self.f.service.create_order(token, "buyer@example.com")
        payment = self.payments()[0]
        with self.f.db() as session, session.begin():
            session.get(SubscriptionPayment, payment.id).created_at = self.f.now + timedelta(seconds=1)
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        with self.f.db() as session, session.begin():
            session.get(SubscriptionPayment, payment.id).created_at = self.f.now
            session.get(UserAccess, payment.user_id).subscription_ends_at = self.f.now + timedelta(days=3)
        with self.assertRaises(WebsitePurchaseError):
            self.f.service.create_order(token, "buyer@example.com")
        self.assertEqual([order.payment_id], [p.id for p in self.payments()])
