"""Temporary-DB, fake-provider tests for the one-buyer, one-order E2E gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import select

from db.models import SubscriptionPayment
from services.payment_domain import PaymentStatus
from services.prodamus import ProdamusService,build_prodamus_signature
from services.telegram_oidc import TelegramIdentity
from services.website_purchase import ControlledCheckoutGate,WebsitePurchaseError,WebsitePurchaseService
from storage.website_purchase_runtime import build_website_purchase_runtime,WebsitePurchaseRuntimeConfigurationError
from tests import test_website_purchase as purchase_fixtures
from tests import test_website_purchase_runtime as runtime_fixtures


class ControlledCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture=purchase_fixtures.WebsitePurchaseTests()
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.gate=ControlledCheckoutGate(777,purchase_fixtures.NOW+timedelta(minutes=5))
        self.fixture.service._checkout_gate=self.gate

    def order_count(self):
        with self.fixture.db() as session:return len(session.scalars(select(SubscriptionPayment)).all())

    def test_closed_default_and_other_identity_cannot_create_order(self):
        token=self.fixture.login()
        self.fixture.service._checkout_gate=ControlledCheckoutGate()
        with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(token,'buyer@example.com')
        self.fixture.service._checkout_gate=self.gate
        with patch.object(self.fixture.oidc,'exchange_code',return_value=TelegramIdentity(778,'Other fixture','')):
            other=self.fixture.login()
        with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(other,'buyer@example.com')
        self.assertEqual(0,self.order_count())

    def test_expired_gate_rejects_still_valid_browser_session(self):
        token=self.fixture.login()
        self.fixture.now+=timedelta(minutes=5)
        self.assertTrue(self.fixture.service.is_authenticated(token))
        with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(token,'buyer@example.com')
        self.assertEqual(0,self.order_count())

    def test_gate_expiring_before_provider_write_transaction_creates_no_order(self):
        token=self.fixture.login()
        original=self.fixture.provider.create_order
        def delayed(user_id,email,**kwargs):
            self.fixture.now+=timedelta(minutes=5)
            return original(user_id,email,**kwargs)
        with patch.object(self.fixture.provider,'create_order',side_effect=delayed):
            with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(token,'buyer@example.com')
        self.assertEqual(0,self.order_count())

    def test_allowed_buyer_gets_one_499_order_and_duplicate_post_reuses_it(self):
        token=self.fixture.login()
        first=self.fixture.service.create_order(token,'buyer@example.com')
        second=self.fixture.service.create_order(token,'buyer@example.com')
        self.assertEqual(first.payment_id,second.payment_id)
        self.assertFalse(second.created)
        with self.fixture.db() as session:
            order=session.get(SubscriptionPayment,first.payment_id)
            self.assertEqual((49900,'RUB',30),(order.amount_minor,order.currency,order.period_days))
        with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(token,'different@example.com')
        self.assertEqual(1,self.order_count())

    def test_cancelled_failed_and_confirmed_orders_do_not_reopen_quota_after_restart(self):
        token=self.fixture.login();order=self.fixture.service.create_order(token,'buyer@example.com')
        for terminal in (PaymentStatus.FAILED,PaymentStatus.CANCELED,PaymentStatus.EXPIRED,PaymentStatus.SUCCEEDED):
            with self.fixture.db() as session,session.begin():
                session.get(SubscriptionPayment,order.payment_id).status=terminal.value
            restarted_provider=ProdamusService(self.fixture.provider._config,session_factory=self.fixture.db,now_factory=lambda:self.fixture.now)
            restarted=WebsitePurchaseService(self.fixture.oidc,restarted_provider,session_factory=self.fixture.db,now_factory=lambda:self.fixture.now,checkout_gate=self.gate)
            with self.assertRaises(WebsitePurchaseError):restarted.create_order(token,'buyer@example.com')
            self.assertEqual(1,self.order_count())

    def test_concurrent_controlled_requests_create_only_one_order(self):
        token=self.fixture.login()
        with ThreadPoolExecutor(max_workers=4) as pool:
            orders=list(pool.map(lambda _:self.fixture.service.create_order(token,'buyer@example.com'),range(8)))
        self.assertEqual(1,len({o.payment_id for o in orders}))
        self.assertEqual(1,sum(o.created for o in orders))
        self.assertEqual(1,self.order_count())

    def test_independent_provider_instances_share_durable_single_order_quota(self):
        token=self.fixture.login()
        with self.fixture.db() as session:
            from db.models import User
            user_id=session.scalar(select(User.id).where(User.tg_id==777))
        providers=[ProdamusService(self.fixture.provider._config,session_factory=self.fixture.db,now_factory=lambda:self.fixture.now) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            orders=list(pool.map(lambda p:p.create_order(user_id,'buyer@example.com',single_order=True),providers))
        self.assertEqual(1,len({o.payment_id for o in orders}))
        self.assertEqual(1,sum(o.created for o in orders))
        self.assertEqual(1,self.order_count())

    def test_expired_creation_gate_does_not_block_signed_webhook_or_duplicate(self):
        token=self.fixture.login();order=self.fixture.service.create_order(token,'buyer@example.com')
        self.fixture.now+=timedelta(minutes=6)
        payload={'order_id':'fixture-operation','order_num':order.transaction_id,'sum':'499.00','currency':'rub','domain':'fixture.payform.ru','customer_email':'buyer@example.com','payment_status':'success'}
        signature=build_prodamus_signature(payload,purchase_fixtures.SECRET)
        first=self.fixture.service.process_prodamus_notification(payload,signature)
        second=self.fixture.service.process_prodamus_notification(payload,signature)
        self.assertTrue(first.access_applied);self.assertFalse(second.access_applied)
        self.assertTrue(self.fixture.service.is_authenticated(token))
        self.assertEqual(PaymentStatus.SUCCEEDED,self.fixture.service.inspect_order_status(token))
        with self.assertRaises(WebsitePurchaseError):self.fixture.service.create_order(token,'buyer@example.com')
        self.assertEqual(1,self.order_count())

    def test_runtime_defaults_closed_and_requires_explicit_controlled_identity_and_utc_lease(self):
        runtime=build_website_purchase_runtime(runtime_fixtures.settings(runtime_fixtures.COMPLETE_CONFIG))
        self.assertFalse(runtime.service._checkout_gate.allows(777,purchase_fixtures.NOW))
        valid=dict(runtime_fixtures.COMPLETE_CONFIG,WEBSITE_CHECKOUT_MODE='controlled',WEBSITE_CHECKOUT_TELEGRAM_USER_ID='777',WEBSITE_CHECKOUT_EXPIRES_AT='2026-10-03T12:05:00Z')
        configured=build_website_purchase_runtime(runtime_fixtures.settings(valid))
        self.assertTrue(configured.service._checkout_gate.allows(777,purchase_fixtures.NOW))
        self.assertNotIn('777',repr(configured.service._checkout_gate))
        for changes in ({'WEBSITE_CHECKOUT_MODE':'invalid'},{'WEBSITE_CHECKOUT_TELEGRAM_USER_ID':'0'},{'WEBSITE_CHECKOUT_TELEGRAM_USER_ID':'-1'},{'WEBSITE_CHECKOUT_TELEGRAM_USER_ID':'00777'},{'WEBSITE_CHECKOUT_TELEGRAM_USER_ID':'٧٧٧'},{'WEBSITE_CHECKOUT_TELEGRAM_USER_ID':str(2**63)},{'WEBSITE_CHECKOUT_EXPIRES_AT':'2026-10-03T12:05:00'},{'WEBSITE_CHECKOUT_EXPIRES_AT':'2026-10-03T16:05:00+04:00'}):
            with self.assertRaises(WebsitePurchaseRuntimeConfigurationError):build_website_purchase_runtime(runtime_fixtures.settings(dict(valid,**changes)))
        for name in ('WEBSITE_CHECKOUT_TELEGRAM_USER_ID','WEBSITE_CHECKOUT_EXPIRES_AT'):
            values=dict(valid);del values[name]
            with self.assertRaises(WebsitePurchaseRuntimeConfigurationError):build_website_purchase_runtime(runtime_fixtures.settings(values))


if __name__=='__main__':unittest.main()
