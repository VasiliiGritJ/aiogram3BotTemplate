import unittest
from decimal import Decimal

from services.payment_domain import PaymentSpec, PaymentStatus
from services.payment_provider import (
    PaymentCreateRequest,
    PaymentProviderPermanentError,
    PaymentProviderProtocolError,
    PaymentProviderTransientError,
    PaymentProviderUnknownCreateOutcome,
)
from services.yookassa_provider import (
    YooKassaCredentials,
    YooKassaProvider,
    minor_to_yookassa_amount,
    yookassa_amount_to_minor,
)


SPEC = PaymentSpec("monthly_30d_v1", 99000, "RUB", 30)
CREDENTIALS = YooKassaCredentials("test-shop", "secret-for-tests")
RETURN_URL = "https://example.invalid/return"


class RecordingApi:
    def __init__(
        self,
        *,
        create_result=None,
        get_result=None,
        account_result=None,
    ) -> None:
        self.create_result = create_result
        self.get_result = get_result
        self.account_result = account_result
        self.create_calls: list[tuple[object, str]] = []
        self.get_calls: list[str] = []
        self.account_calls = 0

    def create_payment(self, payload, idempotency_key):
        self.create_calls.append((payload, idempotency_key))
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result

    def get_payment(self, provider_payment_id):
        self.get_calls.append(provider_payment_id)
        if isinstance(self.get_result, Exception):
            raise self.get_result
        return self.get_result

    def get_account_info(self):
        self.account_calls += 1
        if isinstance(self.account_result, Exception):
            raise self.account_result
        return self.account_result


def response(status="pending", **overrides):
    value = {
        "id": "provider-payment-1",
        "status": status,
        "test": True,
        "amount": {"value": "990.00", "currency": "RUB"},
        "confirmation": {"confirmation_url": "https://example.invalid/pay"},
        "metadata": {"local_payment_id": "42", "product_code": "monthly_30d_v1"},
    }
    value.update(overrides)
    return value


class YooKassaProviderTests(unittest.TestCase):
    def request(self) -> PaymentCreateRequest:
        return PaymentCreateRequest(
            local_payment_id=42,
            spec=SPEC,
            metadata={"local_payment_id": "42", "product_code": "monthly_30d_v1"},
        )

    def provider(
        self, api: RecordingApi, *, expected_test_mode: bool = True
    ) -> YooKassaProvider:
        if api.account_result is None:
            api.account_result = {"test": expected_test_mode}
        return YooKassaProvider(
            CREDENTIALS,
            RETURN_URL,
            api=api,
            expected_test_mode=expected_test_mode,
        )

    def test_create_pending_maps_server_owned_payload_and_key(self) -> None:
        api = RecordingApi(create_result=response("pending"))

        payment = self.provider(api).create_payment(self.request(), "same-key")

        payload, key = api.create_calls[0]
        self.assertEqual("same-key", key)
        self.assertEqual("990.00", payload["amount"]["value"])
        self.assertEqual("RUB", payload["amount"]["currency"])
        self.assertTrue(payload["capture"])
        self.assertEqual("redirect", payload["confirmation"]["type"])
        self.assertEqual(RETURN_URL, payload["confirmation"]["return_url"])
        self.assertEqual("42", payload["metadata"]["local_payment_id"])
        self.assertEqual(PaymentStatus.PENDING, payment.status)
        self.assertEqual("https://example.invalid/pay", payment.confirmation_url)

    def test_create_succeeded_and_get_all_provider_statuses(self) -> None:
        api = RecordingApi(create_result=response("succeeded"))
        provider = self.provider(api)

        self.assertEqual(
            PaymentStatus.SUCCEEDED,
            provider.create_payment(self.request(), "key").status,
        )
        for status, expected in (
            ("pending", PaymentStatus.PENDING),
            ("waiting_for_capture", PaymentStatus.WAITING_FOR_CAPTURE),
            ("succeeded", PaymentStatus.SUCCEEDED),
            ("canceled", PaymentStatus.CANCELED),
        ):
            with self.subTest(status=status):
                api.get_result = response(
                    status,
                    cancellation_details={"code": "expired_on_confirmation"}
                    if status == "canceled"
                    else None,
                )
                result = provider.get_payment("provider-payment-1")
                self.assertEqual(expected, result.status)
                if status == "canceled":
                    self.assertEqual("expired_on_confirmation", result.cancellation_code)

    def test_amount_conversion_is_decimal_only_and_deterministic(self) -> None:
        self.assertEqual("0.01", minor_to_yookassa_amount(1))
        self.assertEqual("990.00", minor_to_yookassa_amount(99000))
        self.assertEqual(1, yookassa_amount_to_minor("0.01"))
        self.assertEqual(1250, yookassa_amount_to_minor(Decimal("12.50")))
        for invalid in (0, -1, True, 12.5, "12.501", "NaN"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(PaymentProviderProtocolError):
                    if isinstance(invalid, int) and not isinstance(invalid, bool):
                        minor_to_yookassa_amount(invalid)
                    else:
                        yookassa_amount_to_minor(invalid)

    def test_retry_uses_identical_payload_and_caller_key(self) -> None:
        api = RecordingApi(create_result=response())
        provider = self.provider(api)

        provider.create_payment(self.request(), "retry-key")
        provider.create_payment(self.request(), "retry-key")

        self.assertEqual(["retry-key", "retry-key"], [call[1] for call in api.create_calls])
        self.assertEqual(api.create_calls[0][0], api.create_calls[1][0])

    def test_missing_confirmation_is_safe_and_metadata_is_preserved(self) -> None:
        api = RecordingApi(create_result=response(confirmation={}, metadata={"local_payment_id": "42", "product_code": "monthly_30d_v1"}))

        payment = self.provider(api).create_payment(self.request(), "key")

        self.assertIsNone(payment.confirmation_url)
        self.assertEqual("42", payment.metadata["local_payment_id"])

    def test_timeout_and_transient_errors_are_normalized_without_raw_details(self) -> None:
        create_api = RecordingApi(create_result=TimeoutError("secret-like detail"))
        get_api = RecordingApi(get_result=TimeoutError("secret-like detail"))

        with self.assertRaises(PaymentProviderUnknownCreateOutcome) as create_error:
            self.provider(create_api).create_payment(self.request(), "key")
        with self.assertRaises(PaymentProviderTransientError) as get_error:
            self.provider(get_api).get_payment("provider-payment-1")

        self.assertNotIn("secret-like detail", str(create_error.exception))
        self.assertNotIn("secret-like detail", str(get_error.exception))

    def test_permanent_sdk_error_and_malformed_response_are_safe(self) -> None:
        class BadRequestError(Exception):
            pass

        with self.assertRaises(PaymentProviderPermanentError):
            self.provider(RecordingApi(create_result=BadRequestError("sensitive"))).create_payment(
                self.request(), "key"
            )
        malformed_responses = (
            {**response(), "id": ""},
            {**response(), "status": "creating"},
            {**response(), "amount": {"value": "1.001", "currency": "RUB"}},
        )
        for malformed in malformed_responses:
            with self.subTest(malformed=malformed):
                with self.assertRaises(PaymentProviderProtocolError):
                    self.provider(RecordingApi(create_result=malformed)).create_payment(
                        self.request(), "key"
                    )

    def test_credentials_do_not_expose_secret_in_repr_or_validation_error(self) -> None:
        self.assertNotIn("secret-for-tests", repr(CREDENTIALS))
        with self.assertRaises(ValueError) as error:
            YooKassaCredentials("", "secret-for-tests")
        self.assertNotIn("secret-for-tests", str(error.exception))

    def test_test_shop_verification_allows_only_authoritative_true_and_caches_success(self) -> None:
        api = RecordingApi(
            account_result={"test": True},
            create_result=response(test=True),
        )
        provider = self.provider(api, expected_test_mode=True)

        provider.create_payment(self.request(), "first-key")
        provider.create_payment(self.request(), "second-key")

        self.assertEqual(1, api.account_calls)
        self.assertEqual(2, len(api.create_calls))

    def test_test_shop_verification_blocks_before_create_on_unknown_or_non_test_account(self) -> None:
        cases = (
            {"test": False},
            {},
            {"test": "true"},
            TimeoutError("secret-like detail"),
            RuntimeError("secret-like detail"),
        )
        for account_result in cases:
            with self.subTest(account_result=account_result):
                api = RecordingApi(
                    account_result=account_result,
                    create_result=response(test=True),
                )
                with self.assertRaises((
                    PaymentProviderPermanentError,
                    PaymentProviderProtocolError,
                    PaymentProviderTransientError,
                )) as error:
                    self.provider(api, expected_test_mode=True).create_payment(
                        self.request(), "key"
                    )
                self.assertEqual([], api.create_calls)
                self.assertNotIn("secret-like detail", str(error.exception))

    def test_test_mode_requires_test_flag_on_create_and_get(self) -> None:
        for test_flag in (False, None):
            with self.subTest(create_test_flag=test_flag):
                api = RecordingApi(
                    account_result={"test": True},
                    create_result=response(test=test_flag)
                    if test_flag is not None
                    else {
                        key: value
                        for key, value in response().items()
                        if key != "test"
                    },
                )
                with self.assertRaises(PaymentProviderProtocolError):
                    self.provider(api, expected_test_mode=True).create_payment(
                        self.request(), "key"
                    )

            with self.subTest(get_test_flag=test_flag):
                api = RecordingApi(
                    account_result={"test": True},
                    get_result=response(test=test_flag)
                    if test_flag is not None
                    else {
                        key: value
                        for key, value in response().items()
                        if key != "test"
                    },
                )
                with self.assertRaises(PaymentProviderProtocolError):
                    self.provider(api, expected_test_mode=True).get_payment(
                        "provider-payment-1"
                    )

        api = RecordingApi(
            account_result={"test": True},
            create_result=response(test=True),
            get_result=response(test=True),
        )
        provider = self.provider(api, expected_test_mode=True)
        self.assertTrue(provider.create_payment(self.request(), "key").is_test)
        self.assertTrue(provider.get_payment("provider-payment-1").is_test)

    def test_shop_type_mismatch_is_blocked_before_provider_create(self) -> None:
        cases = (
            (True, True, True),
            (True, False, False),
            (False, False, True),
            (False, True, False),
        )
        for expected_test_mode, account_is_test, allowed in cases:
            with self.subTest(
                expected_test_mode=expected_test_mode,
                account_is_test=account_is_test,
            ):
                api = RecordingApi(
                    account_result={"test": account_is_test},
                    create_result=response(test=expected_test_mode),
                )
                provider = self.provider(
                    api, expected_test_mode=expected_test_mode
                )
                if allowed:
                    provider.create_payment(self.request(), "key")
                    self.assertEqual(1, len(api.create_calls))
                else:
                    with self.assertRaises(PaymentProviderPermanentError) as error:
                        provider.create_payment(self.request(), "key")
                    self.assertEqual([], api.create_calls)
                    self.assertNotIn("secret-for-tests", str(error.exception))

    def test_production_mode_requires_false_payment_flag(self) -> None:
        api = RecordingApi(
            account_result={"test": False},
            create_result=response(test=False),
            get_result=response(test=False),
        )
        provider = self.provider(api, expected_test_mode=False)

        self.assertFalse(provider.create_payment(self.request(), "key").is_test)
        self.assertFalse(provider.get_payment("provider-payment-1").is_test)

        for test_flag in (True, None):
            with self.subTest(test_flag=test_flag):
                api.create_result = (
                    response(test=test_flag) if test_flag is not None else {
                        key: value for key, value in response().items() if key != "test"
                    }
                )
                with self.assertRaises(PaymentProviderProtocolError):
                    provider.create_payment(self.request(), "other-key")


if __name__ == "__main__":
    unittest.main()
