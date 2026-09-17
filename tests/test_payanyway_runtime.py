import unittest
from unittest.mock import patch

from storage.payanyway_runtime import build_payanyway_runtime
from storage.payment_runtime import PaymentMode, PaymentRuntimeConfigurationError


VALID = {
    "PAYMENTS_MODE": "production",
    "PAYANYWAY_MNT_ID": "12345678",
    "PAYANYWAY_INTEGRITY_CODE": "fixture-secret",
    "PAYANYWAY_SUCCESS_URL": "https://www.traineros.ru/payment/success",
    "PAYANYWAY_FAIL_URL": "https://www.traineros.ru/payment/fail",
    "PAYANYWAY_RETURN_URL": "https://www.traineros.ru/payment/return",
}


def reader(values):
    def read(name: str) -> str:
        if name not in values:
            raise KeyError(name)
        return values[name]

    return read


class PayAnyWayRuntimeTests(unittest.TestCase):
    def test_explicit_production_mode_builds_production_service(self) -> None:
        with patch("storage.payanyway_runtime.PayAnyWayService") as service:
            runtime = build_payanyway_runtime(reader(VALID))
        self.assertEqual(PaymentMode.PRODUCTION, runtime.mode)
        config = service.call_args.args[0]
        self.assertFalse(config.test_mode)
        self.assertEqual("12345678", config.merchant_id)

    def test_explicit_test_mode_uses_test_contract(self) -> None:
        values = {**VALID, "PAYMENTS_MODE": "test"}
        with patch("storage.payanyway_runtime.PayAnyWayService") as service:
            runtime = build_payanyway_runtime(reader(values))
        self.assertEqual(PaymentMode.TEST, runtime.mode)
        self.assertTrue(service.call_args.args[0].test_mode)

    def test_missing_mode_or_any_merchant_value_fails_closed(self) -> None:
        for missing in VALID:
            with self.subTest(missing=missing):
                values = {key: value for key, value in VALID.items() if key != missing}
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payanyway_runtime(reader(values))

    def test_http_return_urls_are_rejected(self) -> None:
        for invalid in (
            "http://www.traineros.ru/success",
            "https://user:password@www.traineros.ru/success",
            "https://www.traineros.ru/bad path",
        ):
            with self.subTest(invalid=invalid):
                values = {**VALID, "PAYANYWAY_SUCCESS_URL": invalid}
                with self.assertRaises(PaymentRuntimeConfigurationError):
                    build_payanyway_runtime(reader(values))


if __name__ == "__main__":
    unittest.main()
