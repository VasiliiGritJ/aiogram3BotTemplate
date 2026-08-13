"""YooKassa implementation of the provider-neutral payment contract.

The adapter does not read environment variables or import the project config.
Credentials and the return URL are supplied by the application composition
layer, while this module only maps server-owned payment data to the SDK.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, Protocol

from services.payment_domain import PaymentStatus
from services.payment_provider import (
    PaymentCreateRequest,
    ProviderAccountInfo,
    PaymentProviderPermanentError,
    PaymentProviderProtocolError,
    PaymentProviderTransientError,
    PaymentProviderUnknownCreateOutcome,
    ProviderPayment,
)


_MINOR_UNITS = Decimal("100")
_YOOKASSA_STATUSES = {
    "pending": PaymentStatus.PENDING,
    "waiting_for_capture": PaymentStatus.WAITING_FOR_CAPTURE,
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.CANCELED,
}


@dataclass(frozen=True)
class YooKassaCredentials:
    """Injected credentials; the secret deliberately never appears in repr."""

    shop_id: str
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.shop_id, str) or not self.shop_id.strip():
            raise ValueError("YooKassa shop ID is required.")
        if not isinstance(self.secret_key, str) or not self.secret_key.strip():
            raise ValueError("YooKassa secret key is required.")


class YooKassaApi(Protocol):
    """Small SDK boundary that makes the adapter fully mockable."""

    def create_payment(
        self, payload: Mapping[str, object], idempotency_key: str
    ) -> object:
        """Create a payment through the provider SDK."""

    def get_payment(self, provider_payment_id: str) -> object:
        """Load a payment through the provider SDK."""

    def get_account_info(self) -> object:
        """Load the authenticated shop information without creating a payment."""


class SdkYooKassaApi:
    """Lazy wrapper around YooKassa's class-level SDK API.

    The SDK has global configuration.  It is intentionally configured only
    when this injected boundary is constructed, never while importing a
    module, and tests inject a fake instead.
    """

    def __init__(self, credentials: YooKassaCredentials) -> None:
        from yookassa import Configuration, Payment, Settings

        Configuration.configure(credentials.shop_id, credentials.secret_key)
        self._payment = Payment
        self._settings = Settings

    def create_payment(
        self, payload: Mapping[str, object], idempotency_key: str
    ) -> object:
        return self._payment.create(dict(payload), idempotency_key)

    def get_payment(self, provider_payment_id: str) -> object:
        return self._payment.find_one(provider_payment_id)

    def get_account_info(self) -> object:
        return self._settings.get_account_settings()


def minor_to_yookassa_amount(amount_minor: int) -> str:
    """Convert integer minor units to YooKassa's two-decimal string format."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise PaymentProviderProtocolError("Invalid local payment amount.")
    if amount_minor <= 0:
        raise PaymentProviderProtocolError("Invalid local payment amount.")
    return format(Decimal(amount_minor) / _MINOR_UNITS, ".2f")


def yookassa_amount_to_minor(value: object) -> int:
    """Parse a provider decimal amount without accepting floating point input."""
    if isinstance(value, float):
        raise PaymentProviderProtocolError("Provider amount must not be float.")
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise PaymentProviderProtocolError("Invalid provider amount.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PaymentProviderProtocolError("Invalid provider amount.") from None
    if not amount.is_finite() or amount <= 0:
        raise PaymentProviderProtocolError("Invalid provider amount.")
    minor = amount * _MINOR_UNITS
    if minor != minor.to_integral_value():
        raise PaymentProviderProtocolError("Provider amount has unsupported precision.")
    return int(minor)


class YooKassaProvider:
    """Production adapter; it has no local DB or access-service side effects."""

    def __init__(
        self,
        credentials: YooKassaCredentials,
        return_url: str,
        *,
        api: YooKassaApi | None = None,
        expected_test_mode: bool,
    ) -> None:
        if not isinstance(return_url, str) or not return_url.strip():
            raise ValueError("YooKassa return URL is required.")
        self._credentials = credentials
        self._return_url = return_url
        self._api = api if api is not None else SdkYooKassaApi(credentials)
        if type(expected_test_mode) is not bool:
            raise ValueError("Expected YooKassa shop mode must be explicit.")
        self._expected_test_mode = expected_test_mode
        self._verified_shop_mode = False

    def ensure_payment_creation_allowed(self) -> None:
        """Verify that the authenticated shop matches the configured mode."""
        if self._verified_shop_mode:
            return
        account = self.get_account_info()
        if account.is_test is not self._expected_test_mode:
            raise PaymentProviderPermanentError(
                "YooKassa shop does not match the configured payment mode."
            )
        self._verified_shop_mode = True

    def get_account_info(self) -> ProviderAccountInfo:
        """Return only the provider's authoritative test-shop flag."""
        try:
            response = self._api.get_account_info()
        except (TimeoutError, OSError) as error:
            raise PaymentProviderTransientError(
                "YooKassa shop verification is temporarily unavailable."
            ) from error
        except Exception as error:
            raise self._normalize_sdk_error(error, creating=False) from error
        data = self._response_mapping(response)
        is_test = data.get("test")
        if type(is_test) is not bool:
            raise PaymentProviderProtocolError(
                "Malformed YooKassa shop verification response."
            )
        return ProviderAccountInfo(is_test=is_test)

    def create_payment(
        self,
        request: PaymentCreateRequest,
        idempotency_key: str,
    ) -> ProviderPayment:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise PaymentProviderPermanentError("Payment idempotency key is required.")
        self.ensure_payment_creation_allowed()
        payload = self._create_payload(request)
        try:
            response = self._api.create_payment(payload, idempotency_key)
        except (TimeoutError, OSError) as error:
            raise PaymentProviderUnknownCreateOutcome(
                "YooKassa create outcome is unknown."
            ) from error
        except Exception as error:
            raise self._normalize_sdk_error(error, creating=True) from error
        return self._require_expected_payment_mode(self._to_provider_payment(response))

    def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        if not isinstance(provider_payment_id, str) or not provider_payment_id:
            raise PaymentProviderPermanentError("Provider payment ID is required.")
        try:
            response = self._api.get_payment(provider_payment_id)
        except (TimeoutError, OSError) as error:
            raise PaymentProviderTransientError(
                "YooKassa payment lookup is temporarily unavailable."
            ) from error
        except Exception as error:
            raise self._normalize_sdk_error(error, creating=False) from error
        return self._require_expected_payment_mode(self._to_provider_payment(response))

    def _require_expected_payment_mode(
        self, payment: ProviderPayment
    ) -> ProviderPayment:
        if payment.is_test is not self._expected_test_mode:
            raise PaymentProviderProtocolError(
                "YooKassa payment does not match the configured payment mode."
            )
        return payment

    def _create_payload(self, request: PaymentCreateRequest) -> dict[str, object]:
        spec = request.spec
        if not isinstance(request.local_payment_id, int) or request.local_payment_id <= 0:
            raise PaymentProviderPermanentError("Invalid local payment reference.")
        metadata = dict(request.metadata)
        metadata["local_payment_id"] = str(request.local_payment_id)
        metadata["product_code"] = spec.product_code
        return {
            "amount": {
                "value": minor_to_yookassa_amount(spec.amount_minor),
                "currency": spec.currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self._return_url,
            },
            "description": f"Subscription {spec.product_code}",
            "metadata": metadata,
        }

    @staticmethod
    def _normalize_sdk_error(error: Exception, *, creating: bool) -> Exception:
        # The SDK's concrete exception hierarchy is intentionally kept inside
        # this adapter.  Its messages can contain request details, so callers
        # only receive stable, non-sensitive errors.
        error_name = error.__class__.__name__
        if error_name in {"TooManyRequestsError", "ResponseProcessingError"}:
            if creating:
                return PaymentProviderUnknownCreateOutcome(
                    "YooKassa create outcome is unknown."
                )
            return PaymentProviderTransientError(
                "YooKassa payment lookup is temporarily unavailable."
            )
        if error_name in {
            "BadRequestError",
            "UnauthorizedError",
            "ForbiddenError",
            "NotFoundError",
            "AuthorizeError",
        }:
            return PaymentProviderPermanentError("YooKassa rejected the request.")
        if creating:
            return PaymentProviderUnknownCreateOutcome(
                "YooKassa create outcome is unknown."
            )
        return PaymentProviderTransientError(
            "YooKassa payment lookup is temporarily unavailable."
        )

    @classmethod
    def _to_provider_payment(cls, response: object) -> ProviderPayment:
        data = cls._response_mapping(response)
        provider_payment_id = data.get("id")
        if not isinstance(provider_payment_id, str) or not provider_payment_id:
            raise PaymentProviderProtocolError("Malformed YooKassa payment ID.")
        status_value = data.get("status")
        if status_value not in _YOOKASSA_STATUSES:
            raise PaymentProviderProtocolError("Malformed YooKassa payment status.")
        amount_data = data.get("amount")
        if not isinstance(amount_data, Mapping):
            raise PaymentProviderProtocolError("Malformed YooKassa payment amount.")
        currency = amount_data.get("currency")
        if not isinstance(currency, str) or len(currency) != 3:
            raise PaymentProviderProtocolError("Malformed YooKassa payment currency.")
        confirmation_url = None
        confirmation = data.get("confirmation")
        if isinstance(confirmation, Mapping):
            raw_confirmation_url = confirmation.get("confirmation_url")
            if raw_confirmation_url is not None:
                if not isinstance(raw_confirmation_url, str):
                    raise PaymentProviderProtocolError("Malformed YooKassa confirmation URL.")
                confirmation_url = raw_confirmation_url
        cancellation_code = None
        cancellation = data.get("cancellation_details")
        if isinstance(cancellation, Mapping):
            raw_cancellation_code = cancellation.get("code")
            if raw_cancellation_code is not None:
                if not isinstance(raw_cancellation_code, str):
                    raise PaymentProviderProtocolError("Malformed YooKassa cancellation code.")
                cancellation_code = raw_cancellation_code
        expires_at = cls._parse_optional_datetime(data.get("expires_at"))
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise PaymentProviderProtocolError("Malformed YooKassa metadata.")
        return ProviderPayment(
            provider_payment_id=provider_payment_id,
            status=_YOOKASSA_STATUSES[status_value],
            amount_minor=yookassa_amount_to_minor(amount_data.get("value")),
            currency=currency,
            confirmation_url=confirmation_url,
            expires_at=expires_at,
            cancellation_code=cancellation_code,
            metadata=dict(metadata),
            is_test=cls._parse_optional_test_flag(data.get("test")),
        )

    @staticmethod
    def _response_mapping(response: object) -> Mapping[str, object]:
        if isinstance(response, Mapping):
            return response
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            raise PaymentProviderProtocolError("Malformed YooKassa response.")
        try:
            data = json.loads(json_method())
        except (TypeError, ValueError):
            raise PaymentProviderProtocolError("Malformed YooKassa response.") from None
        if not isinstance(data, Mapping):
            raise PaymentProviderProtocolError("Malformed YooKassa response.")
        return data

    @staticmethod
    def _parse_optional_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PaymentProviderProtocolError("Malformed YooKassa expiration time.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PaymentProviderProtocolError("Malformed YooKassa expiration time.") from None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _parse_optional_test_flag(value: object) -> bool | None:
        if value is None:
            return None
        if type(value) is not bool:
            raise PaymentProviderProtocolError("Malformed YooKassa test flag.")
        return value
