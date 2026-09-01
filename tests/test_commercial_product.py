"""Tests for one server-owned launch commercial contract."""

import unittest
from datetime import timedelta

from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from services.receipt_domain import NPD_RECEIPT_DESCRIPTION


class CommercialProductTests(unittest.TestCase):
    def test_launch_facts_are_fixed_and_user_facing_description_is_aligned(self) -> None:
        product = LAUNCH_COMMERCIAL_PRODUCT
        self.assertEqual("Мой AI Тренер", product.service_name)
        self.assertEqual("monthly_30d_v1", product.product_code)
        self.assertEqual(49_900, product.amount_minor)
        self.assertEqual("RUB", product.currency)
        self.assertEqual(30, product.period_days)
        self.assertFalse(product.auto_renew)
        self.assertEqual(3, product.trial_days)
        self.assertEqual(timedelta(hours=72), product.trial_duration)
        self.assertEqual(0, product.trial_price_minor)
        self.assertFalse(product.trial_requires_payment_method)
        self.assertFalse(product.trial_auto_renew)
        self.assertEqual(1, product.trial_lifetime_limit)
        self.assertEqual("Доступ к сервису «Мой AI Тренер» на 30 дней", product.receipt_description)
        self.assertEqual(product.receipt_description, NPD_RECEIPT_DESCRIPTION)

    def test_payment_product_uses_exactly_the_same_commercial_facts(self) -> None:
        product = LAUNCH_COMMERCIAL_PRODUCT
        payment_product = product.payment_product
        self.assertEqual(product.product_code, payment_product.product_code)
        self.assertEqual(product.amount_minor, payment_product.amount_minor)
        self.assertEqual(product.currency, payment_product.currency)
        self.assertEqual(product.period_days, payment_product.period_days)


if __name__ == "__main__":
    unittest.main()
