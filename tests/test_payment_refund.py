"""Full refunds: fixture provider evidence and disposable DBs, no network."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db.migrations import run_migrations
from db.models import PaymentRefund, PaymentReceipt, SqliteSession, SubscriptionPayment, UserAccess
from services.access import AccessEntitlement, evaluate_access
from services.payment_domain import PaymentStatus
from services.payment_refund import FullRefundService, RefundRejected, RefundStatus, VerifiedFullRefund
from services.prodamus import ProdamusConfig, ProdamusService, build_prodamus_signature


NOW = datetime(2026, 10, 16, 11, 11)
RETURNED = NOW + timedelta(days=1)
SECRET = "fixture-not-production-refund-tests"


class FullRefundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteSession(f"sqlite:///{Path(self.temp.name).as_posix()}/refund.db")
        self.now = NOW
        run_migrations(self.db.engine)
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO users(id,tg_id,fullname,username,inviter_id) "
                                 "VALUES(1,1001,'Fixture','fixture',0)")
            conn.exec_driver_sql("INSERT INTO user_access(user_id,updated_at) VALUES(1,?)", (NOW,))
        self.prodamus = ProdamusService(
            ProdamusConfig("https://fixture.payform.ru", SECRET,
                           "https://payments.example.invalid/webhooks/prodamus",
                           "https://payments.example.invalid/payment/success",
                           "https://payments.example.invalid/payment/fail",
                           "https://payments.example.invalid/buy"),
            session_factory=self.db, now_factory=lambda: self.now,
        )
        self.order = self.prodamus.create_order(1, "buyer@example.com")
        self.payload = dict(order_id="provider-operation", order_num=self.order.transaction_id,
                            sum="499.00", currency="rub", domain="fixture.payform.ru",
                            customer_email="buyer@example.com", payment_status="success")
        self.signature = build_prodamus_signature(self.payload, SECRET)
        self.assertTrue(self.prodamus.process_notification(self.payload, self.signature).accepted)
        self.refunds = FullRefundService(session_factory=self.db, now_factory=lambda: RETURNED)
        self.proof = VerifiedFullRefund(
            self.order.payment_id, 1, self.order.transaction_id, "provider-operation",
            "fixture-refund-1", "a" * 64, 49900, "RUB", RETURNED, "returned",
        )

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()

    def access(self):
        with self.db() as session:
            return session.get(UserAccess, 1)

    def test_completed_full_refund_revokes_only_remaining_grant_preserves_history(self):
        with self.db() as session:
            payment = session.get(SubscriptionPayment, self.order.payment_id)
            receipt = session.scalar(select(PaymentReceipt))
            original = (payment.status, payment.access_applied_at, payment.grant_started_at,
                        payment.grant_ends_at, receipt.status, receipt.attempts)
        result = self.refunds.reconcile_verified(self.proof)
        self.assertTrue(result.applied)
        self.assertEqual(RETURNED, self.access().subscription_ends_at)
        self.assertNotEqual(AccessEntitlement.ACTIVE_PAID, evaluate_access(self.access(), RETURNED).entitlement)
        self.assertNotEqual(AccessEntitlement.TRIAL_AVAILABLE, evaluate_access(self.access(), RETURNED).entitlement)
        with self.db() as session:
            payment = session.get(SubscriptionPayment, self.order.payment_id)
            receipt = session.scalar(select(PaymentReceipt))
            self.assertEqual(original, (payment.status, payment.access_applied_at,
                             payment.grant_started_at, payment.grant_ends_at, receipt.status, receipt.attempts))
            self.assertEqual(1, session.scalar(select(func.count(PaymentRefund.payment_id))))
        self.assertEqual(RefundStatus.CONFIRMED,
                         self.prodamus.inspect_order_status(self.order.payment_id, user_id=1))
        self.assertIsNone(self.prodamus.inspect_order_status(self.order.payment_id, user_id=2))
        self.assertFalse(self.prodamus.inspect_browser_success(self.order.transaction_id, user_id=1))

    def test_duplicate_concurrent_refund_is_exactly_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.refunds.reconcile_verified(self.proof), range(4)))
        self.assertEqual(1, sum(r.applied for r in results))
        self.assertEqual(RETURNED, self.access().subscription_ends_at)

    def test_late_success_preserves_original_confirmation_and_refund_replay(self):
        self.now = RETURNED + timedelta(hours=1)
        later_refunds = FullRefundService(session_factory=self.db, now_factory=lambda: self.now)
        # A delayed success BEFORE reconciliation must not invalidate completed
        # provider evidence; another one AFTER reconciliation cannot regrant.
        self.prodamus.process_notification(self.payload, self.signature)
        self.assertTrue(later_refunds.reconcile_verified(self.proof).applied)
        self.now += timedelta(hours=1)
        self.assertFalse(self.prodamus.process_notification(self.payload, self.signature).access_applied)
        self.assertFalse(later_refunds.reconcile_verified(self.proof).applied)
        with self.db() as session:
            self.assertEqual(NOW, session.get(SubscriptionPayment, self.order.payment_id).confirmed_at)
        self.assertEqual(RETURNED, self.access().subscription_ends_at)

    def test_late_signed_success_and_refund_race_cannot_restore_access(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.refunds.reconcile_verified, self.proof),
                     pool.submit(self.prodamus.process_notification, self.payload, self.signature)]
            for task in tasks:
                task.result(timeout=10)
        for _ in range(3):
            result = self.prodamus.process_notification(self.payload, self.signature)
            self.assertTrue(result.accepted)
            self.assertFalse(result.access_applied)
        self.assertEqual(RETURNED, self.access().subscription_ends_at)
        with self.db() as session:
            self.assertEqual(1, session.scalar(select(func.count(PaymentReceipt.id))))

    def test_incomplete_negative_unknown_and_untrusted_callbacks_never_revoke(self):
        for status in ("returning", "pending", "order_canceled", "order_denied", "success", ""):
            with self.subTest(status=status), self.assertRaises(RefundRejected):
                self.refunds.reconcile_verified(replace(self.proof, provider_status=status))
        for status in ("order_canceled", "refunded", "refund", "order_refunded"):
            payload = dict(self.payload, payment_status=status)
            self.assertFalse(self.prodamus.process_notification(
                payload, build_prodamus_signature(payload, SECRET)).accepted)
        self.assertFalse(self.prodamus.process_notification(self.payload, "0" * 64).accepted)
        self.assertEqual(NOW + timedelta(days=30), self.access().subscription_ends_at)

    def test_binding_amount_currency_evidence_and_time_mismatches_fail_closed(self):
        cases = [dict(user_id=2), dict(payment_id=999), dict(transaction_id="wrong"),
                 dict(provider_payment_id="wrong"), dict(amount_minor=499), dict(amount_minor=True),
                 dict(currency="USD"), dict(evidence_sha256="invalid"), dict(provider_refund_id=""),
                 dict(confirmed_at=NOW - timedelta(seconds=1)),
                 dict(confirmed_at=RETURNED + timedelta(seconds=1))]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(RefundRejected):
                self.refunds.reconcile_verified(replace(self.proof, **case))
        with self.db() as session:
            self.assertEqual(0, session.scalar(select(func.count(PaymentRefund.payment_id))))

    def test_conflicting_replay_rejected_without_new_effect(self):
        self.refunds.reconcile_verified(self.proof)
        for case in (dict(provider_refund_id="another"), dict(evidence_sha256="b" * 64),
                     dict(confirmed_at=RETURNED - timedelta(seconds=1))):
            with self.assertRaises(RefundRejected):
                self.refunds.reconcile_verified(replace(self.proof, **case))
        self.assertEqual(RETURNED, self.access().subscription_ends_at)

    def test_later_or_manual_access_change_requires_review_no_blind_subtraction(self):
        with self.db() as session, session.begin():
            session.get(UserAccess, 1).subscription_ends_at += timedelta(days=30)
        with self.assertRaisesRegex(RefundRejected, "access_requires_manual_reconciliation"):
            self.refunds.reconcile_verified(self.proof)
        self.assertEqual(NOW + timedelta(days=60), self.access().subscription_ends_at)

    def test_trial_and_previous_paid_access_are_not_revoked(self):
        for prior_paid in (False, True):
            with self.subTest(prior_paid=prior_paid):
                with self.db() as session, session.begin():
                    # Disposable fixture only: a paid grant queued after prior access.
                    session.query(PaymentRefund).delete()
                    payment = session.get(SubscriptionPayment, self.order.payment_id)
                    payment.grant_started_at = NOW + timedelta(days=3)
                    payment.grant_ends_at = NOW + timedelta(days=33)
                    access = session.get(UserAccess, 1)
                    access.subscription_started_at = NOW if prior_paid else payment.grant_started_at
                    access.subscription_ends_at = payment.grant_ends_at
                    access.trial_started_at = NOW
                    access.trial_ends_at = NOW + timedelta(days=3)
                    access.trial_consumed_at = NOW
                self.refunds.reconcile_verified(self.proof)
                access = self.access()
                self.assertEqual(NOW + timedelta(days=3), access.subscription_ends_at)
                self.assertEqual(NOW, access.trial_consumed_at)
                self.assertEqual(AccessEntitlement.ACTIVE_PAID if prior_paid else AccessEntitlement.ACTIVE_TRIAL,
                                 evaluate_access(access, RETURNED).entitlement)

    def test_refund_of_expired_grant_does_not_recreate_access(self):
        later = NOW + timedelta(days=40)
        service = FullRefundService(session_factory=self.db, now_factory=lambda: later)
        service.reconcile_verified(replace(self.proof, confirmed_at=later))
        self.assertEqual(NOW + timedelta(days=30), self.access().subscription_ends_at)
        self.assertNotEqual(AccessEntitlement.ACTIVE_PAID, evaluate_access(self.access(), later).entitlement)

    def test_insert_failure_rolls_back_access_and_audit_atomically(self):
        with self.db.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TRIGGER fail_refund BEFORE INSERT ON payment_refunds "
                                "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with self.assertRaises(IntegrityError):
            self.refunds.reconcile_verified(self.proof)
        self.assertEqual(NOW + timedelta(days=30), self.access().subscription_ends_at)

    def test_second_order_race_and_old_refund_replay_never_revoke_new_purchase(self):
        # An ordinary separately paid order may race the refund. Either the
        # refund wins first, or it fails closed on the changed access endpoint.
        new_order = self.prodamus.create_order(1, "buyer@example.com")
        new_payload = dict(self.payload, order_num=new_order.transaction_id, order_id="second-operation")
        def refund():
            try:
                return self.refunds.reconcile_verified(self.proof).applied
            except RefundRejected as error:
                self.assertEqual("access_requires_manual_reconciliation", str(error))
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(refund)
            two = pool.submit(self.prodamus.process_notification, new_payload,
                              build_prodamus_signature(new_payload, SECRET))
            refunded = one.result(timeout=10)
            self.assertTrue(two.result(timeout=10).access_applied)
        expected = RETURNED + timedelta(days=30) if refunded else NOW + timedelta(days=60)
        self.assertEqual(expected, self.access().subscription_ends_at)
        if refunded:
            self.assertFalse(self.refunds.reconcile_verified(self.proof).applied)
            self.assertEqual(expected, self.access().subscription_ends_at)

    def test_refund_reference_cannot_be_reused_for_another_paid_order(self):
        self.refunds.reconcile_verified(self.proof)
        new_order = self.prodamus.create_order(1, "buyer@example.com")
        new_payload = dict(self.payload, order_num=new_order.transaction_id, order_id="second-operation")
        self.prodamus.process_notification(new_payload, build_prodamus_signature(new_payload, SECRET))
        new_proof = replace(self.proof, payment_id=new_order.payment_id,
                            transaction_id=new_order.transaction_id, provider_payment_id="second-operation")
        before = self.access().subscription_ends_at
        with self.assertRaisesRegex(RefundRejected, "refund_reference_reused"):
            self.refunds.reconcile_verified(new_proof)
        self.assertEqual(before, self.access().subscription_ends_at)

    def test_migration_18_is_additive_repeatable_and_retains_paid_history(self):
        other = SqliteSession(f"sqlite:///{Path(self.temp.name).as_posix()}/migration.db")
        try:
            self.assertEqual(tuple(range(1, 18)), run_migrations(other.engine, target_version=17))
            with other.engine.begin() as conn:
                conn.exec_driver_sql("INSERT INTO users(id,tg_id,fullname,username,inviter_id) "
                                     "VALUES(1,1001,'Fixture','fixture',0)")
                conn.exec_driver_sql("INSERT INTO subscription_payments(user_id,provider,idempotency_key,"
                                     "product_code,amount_minor,currency,period_days,status) "
                                     "VALUES(1,'prodamus','fixture','monthly_30d_v1',49900,'RUB',30,'succeeded')")
                before = conn.exec_driver_sql("SELECT * FROM subscription_payments").fetchall()
            self.assertEqual((18,), run_migrations(other.engine))
            self.assertEqual((), run_migrations(other.engine))
            with other.engine.connect() as conn:
                self.assertEqual(before, conn.exec_driver_sql("SELECT * FROM subscription_payments").fetchall())
                self.assertEqual("ok", conn.exec_driver_sql("PRAGMA quick_check").scalar_one())
                self.assertEqual([], conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
        finally:
            other.dispose()
