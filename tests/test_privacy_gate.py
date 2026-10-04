import asyncio
from datetime import timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import select, func

from db.models import User, UserAccess, WebsitePurchaseSession, SubscriptionPayment, FitnessProfile
from services.private_offer import PrivateOffer, OFFER_VERSION
from services.workout_plans import assign_workout_plan
from services.privacy_maintenance import (erase_account, erase_training_data,
    export_non_special_account_data, purge_expired_purchase_sessions, PrivacyReviewRequired)
from storage.website_purchase_http import (_offer_handler, _private_purchase_response,
    _payment_create_handler, _csrf_token)
from storage.website_purchase_runtime import WebsitePurchaseRuntime
from tests import test_website_purchase as fixtures
from tests import test_landing_legal as legal
from tests.test_website_purchase_http import _Request


class PrivacyGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebsitePurchaseTests()
        self.fixture.setUp()
        self.db = self.fixture.db
        self.offer = PrivateOffer(legal.LandingLegalTests().config(), "Private <address> fixture")
        self.runtime = WebsitePurchaseRuntime(self.fixture.service, "https://payments.example",
                                             "https://t.me/GritJBot", self.offer)

    def tearDown(self):
        self.fixture.tearDown()

    def offer_response(self, token):
        return asyncio.run(_private_purchase_response(_Request(token=token), _offer_handler(self.runtime)))

    def test_private_offer_requires_real_authenticated_unexpired_session(self):
        token, _ = self.fixture.start()
        for invalid in ("", "invented", token):
            response = self.offer_response(invalid)
            self.assertEqual(401, response.status)
            self.assertNotIn("address", response.text)
        token = self.fixture.login()
        with patch("storage.website_purchase_http.logger") as logger:
            response = self.offer_response(token)
        logger.info.assert_not_called()
        self.assertEqual(200, response.status)
        self.assertIn("Private &lt;address&gt; fixture", response.text)
        self.assertNotIn("private-offer-notice", response.text)
        self.assertNotIn('rel="canonical"', response.text)
        self.assertIn("noindex, nofollow, noarchive, nosnippet", response.text)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertEqual("noindex, nofollow, noarchive, nosnippet", response.headers["X-Robots-Tag"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        self.assertNotIn("Private <address>", repr(self.runtime))
        self.fixture.now += timedelta(minutes=11)
        self.assertEqual(401, self.offer_response(token).status)

    def test_offer_version_required_before_order_and_missing_config_fails_closed(self):
        token = self.fixture.login()
        request = _Request({"csrf": _csrf_token(token), "email": "test@example.com"}, token=token)
        response = asyncio.run(_payment_create_handler(self.runtime)(request))
        self.assertEqual(400, response.status)
        with self.db() as session:
            self.assertEqual(0, session.scalar(select(func.count()).select_from(SubscriptionPayment)))
        for value in ("", "bad\naddress", "x" * 301):
            with self.assertRaises(ValueError) as failure:
                PrivateOffer(self.offer.public, value)
            self.assertNotIn("bad\naddress", str(failure.exception))

    def test_expired_cleanup_does_not_remove_live_sessions_or_payment(self):
        token = self.fixture.login()
        order = self.fixture.service.create_order(token, "test@example.com")
        self.fixture.now += timedelta(minutes=11)
        self.fixture.start()
        with self.db() as session, session.begin():
            self.assertEqual(1, purge_expired_purchase_sessions(session, now=self.fixture.now))
        with self.db() as session:
            self.assertIsNotNone(session.get(SubscriptionPayment, order.payment_id))
            self.assertEqual(1, session.scalar(select(func.count()).select_from(WebsitePurchaseSession)))

    def test_verified_account_erasure_and_rollback(self):
        self.fixture.login()
        with self.db() as session:
            uid = session.scalar(select(User.id))
        with self.assertRaises(RuntimeError):
            with self.db() as session, session.begin():
                erase_account(session, user_id=uid, identity_verified=True,
                              retention_review_complete=True, now=self.fixture.now)
                raise RuntimeError("rollback fixture")
        with self.db() as session:
            self.assertIsNotNone(session.get(User, uid))
        with self.db() as session, session.begin():
            result = erase_account(session, user_id=uid, identity_verified=True,
                                   retention_review_complete=True, now=self.fixture.now)
            self.assertEqual(1, result["users"])
            self.assertEqual([], session.connection().exec_driver_sql("PRAGMA foreign_key_check").all())
        with self.db() as session:
            self.assertIsNone(session.get(User, uid))
            self.assertEqual(0, session.scalar(select(func.count()).select_from(WebsitePurchaseSession)))

    def test_financial_records_preserved_and_unverified_requests_rejected(self):
        token = self.fixture.login()
        order = self.fixture.service.create_order(token, "test@example.com")
        with self.db() as session:
            uid = session.scalar(select(User.id))
        with self.db() as session, session.begin():
            with self.assertRaises(PrivacyReviewRequired):
                erase_account(session, user_id=uid, identity_verified=True,
                              retention_review_complete=True, now=self.fixture.now)
            with self.assertRaises(PrivacyReviewRequired):
                erase_training_data(session, user_id=uid, identity_verified=False)
            erase_training_data(session, user_id=uid, identity_verified=True)
            exported = export_non_special_account_data(session, user_id=uid, identity_verified=True)
            self.assertNotIn("confirmation_url", str(exported))
            self.assertNotIn("limitations", str(exported))
            self.assertIsNotNone(session.get(SubscriptionPayment, order.payment_id))

    def test_training_erasure_removes_legacy_column_without_exporting_it(self):
        self.fixture.login()
        with self.db() as session, session.begin():
            uid = session.scalar(select(User.id))
            session.add(FitnessProfile(user_id=uid, age=30, sex="male", height_cm=180,
                weight_kg=80, goal="muscle_gain", experience_level="beginner",
                training_environment="gym", workouts_per_week=3, session_duration_minutes=60,
                limitations="historical medical fixture", completed_at=self.fixture.now))
        assign_workout_plan(uid, self.db)
        with self.db() as session, session.begin():
            exported = export_non_special_account_data(session, user_id=uid, identity_verified=True)
            self.assertNotIn("historical medical fixture", str(exported))
            counts = erase_training_data(session, user_id=uid, identity_verified=True)
            self.assertEqual(1, counts["fitness_profiles"])
            self.assertEqual(1, counts["user_workout_plans"])
            self.assertEqual([], session.connection().exec_driver_sql("PRAGMA foreign_key_check").all())
            self.assertNotIn("medical", str(counts))
        with self.db() as session:
            self.assertIsNone(session.get(FitnessProfile, uid))
            self.assertIsNotNone(session.get(UserAccess, uid))

    def test_active_contract_blocks_full_erasure_and_review_is_required(self):
        self.fixture.login()
        with self.db() as session, session.begin():
            uid = session.scalar(select(User.id))
            session.get(UserAccess, uid).subscription_ends_at = self.fixture.now + timedelta(days=1)
        with self.db() as session, session.begin():
            for approved in (False, True):
                with self.assertRaises(PrivacyReviewRequired):
                    erase_account(session, user_id=uid, identity_verified=True,
                                  retention_review_complete=approved, now=self.fixture.now)


if __name__ == "__main__":
    unittest.main()
