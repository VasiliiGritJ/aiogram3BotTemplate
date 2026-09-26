"""PayAnyWay payment request and authoritative PayURL processing.

The module is network-free: it prepares the hosted payment URL and accepts
only signed PayURL reports.  Browser redirects are deliberately read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import md5
from hmac import compare_digest
import json
import re
from typing import Callable, Mapping
from urllib.parse import urlencode, urlparse
from uuid import uuid4
from xml.etree import ElementTree

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import SubscriptionPayment, User, UserAccess, dbSession
from services.access import as_utc_naive, utc_now
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from services.payment_domain import PaymentStatus
from services.payment_provider import PaymentProviderError, ProviderPayment
from services.payment_service import (
    PaymentService,
    PaymentServiceReason,
    PaymentServiceResult,
)
from services.receipt_email import (
    ReceiptEmailRequired,
    normalize_receipt_email,
    receipt_email_in_session,
)


PAYANYWAY_PROVIDER = "payanyway"
PAYANYWAY_PAYMENT_URL = "https://www.payanyway.ru/assistant.htm"
PAYANYWAY_TEST_PAYMENT_URL = "https://demo.moneta.ru/assistant.htm"
PAYANYWAY_SUCCESS_CODE = "200"
PAYANYWAY_REJECT_CODE = "500"
PAYANYWAY_VAT_NONE = "1105"
_TRANSACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class PayAnyWayConfigurationError(ValueError):
    """PayAnyWay configuration is incomplete or unsafe."""


class PayAnyWayCallbackReason(StrEnum):
    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    MALFORMED = "malformed"
    INVALID_SIGNATURE = "invalid_signature"
    MERCHANT_MISMATCH = "merchant_mismatch"
    MODE_MISMATCH = "mode_mismatch"
    PAYMENT_NOT_FOUND = "payment_not_found"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    OPERATION_MISMATCH = "operation_mismatch"
    CONFIRMATION_REJECTED = "confirmation_rejected"


@dataclass(frozen=True)
class PayAnyWayConfig:
    merchant_id: str
    integrity_code: str
    test_mode: bool
    success_url: str
    fail_url: str
    return_url: str

    def __post_init__(self) -> None:
        if not _safe_identifier(self.merchant_id, 64):
            raise PayAnyWayConfigurationError("Merchant identifier is invalid.")
        if not isinstance(self.integrity_code, str) or not self.integrity_code:
            raise PayAnyWayConfigurationError("Integrity code is missing.")
        if type(self.test_mode) is not bool:
            raise PayAnyWayConfigurationError("Payment mode is invalid.")
        for value in (self.success_url, self.fail_url, self.return_url):
            if not isinstance(value, str):
                raise PayAnyWayConfigurationError("Return URL is invalid.")
            parsed = urlparse(value)
            if (
                any(character.isspace() or ord(character) < 32 for character in value)
                or parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise PayAnyWayConfigurationError("Return URL is invalid.")

    @property
    def test_mode_value(self) -> str:
        return "1" if self.test_mode else "0"

    @property
    def payment_url(self) -> str:
        return PAYANYWAY_TEST_PAYMENT_URL if self.test_mode else PAYANYWAY_PAYMENT_URL


@dataclass(frozen=True)
class PayAnyWayOrder:
    payment_id: int
    transaction_id: str
    payment_url: str
    customer_email: str
    inventory: str
    status: PaymentStatus
    created: bool


@dataclass(frozen=True)
class PayAnyWayCallbackResult:
    accepted: bool
    reason: PayAnyWayCallbackReason
    response_xml: str
    payment_id: int | None = None
    access_applied: bool = False


@dataclass(frozen=True)
class PayAnyWayBrowserReturn:
    transaction_id: str
    status: PaymentStatus | None
    access_confirmed: bool


class _NoNetworkPaymentProvider:
    """Prevents callback-only confirmation from accidentally doing I/O."""

    def create_payment(self, request, idempotency_key):
        del request, idempotency_key
        raise PaymentProviderError("PayAnyWay orders are created locally.")

    def get_payment(self, provider_payment_id):
        del provider_payment_id
        raise PaymentProviderError("PayAnyWay has no pull status API.")


class PayAnyWayService:
    """Create signed orders and process signed PayURL notifications."""

    def __init__(
        self,
        config: PayAnyWayConfig,
        *,
        session_factory: Callable[[], Session] = dbSession,
        now_factory: Callable[[], datetime] = utc_now,
        transaction_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._transaction_id_factory = transaction_id_factory or (
            lambda: uuid4().hex
        )
        self._product = LAUNCH_COMMERCIAL_PRODUCT
        self._confirmation_service = PaymentService(
            _NoNetworkPaymentProvider(),
            self._product.payment_product,
            session_factory=session_factory,
            now_factory=now_factory,
            expected_test_mode=config.test_mode,
            provider_name=PAYANYWAY_PROVIDER,
        )

    def create_order(self, user_id: int, customer_email: str) -> PayAnyWayOrder:
        """Persist one server-owned order, then return its signed hosted URL."""
        email = normalize_receipt_email(customer_email)
        existing = self._find_active_order(user_id)
        if existing is not None:
            self._assert_customer(existing.id, user_id, email)
            return self._order_from_payment(existing, email, created=False)

        transaction_id = self._transaction_id_factory()
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID.fullmatch(
            transaction_id
        ):
            raise PayAnyWayConfigurationError("Transaction identifier is invalid.")
        now = self._now()
        try:
            with self._session_factory() as session:
                with session.begin():
                    self._assert_user_and_customer(session, user_id, email)
                    payment = SubscriptionPayment(
                        user_id=user_id,
                        provider=PAYANYWAY_PROVIDER,
                        merchant_transaction_id=transaction_id,
                        idempotency_key=f"payanyway:{transaction_id}",
                        product_code=self._product.product_code,
                        amount_minor=self._product.amount_minor,
                        currency=self._product.currency,
                        period_days=self._product.period_days,
                        status=PaymentStatus.PENDING.value,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(payment)
                    session.flush()
                    payment.confirmation_url = self._build_payment_url(
                        payment, email
                    )
                    session.flush()
                    payment_id = payment.id
        except IntegrityError:
            existing = self._find_active_order(user_id)
            if existing is None:
                raise
            self._assert_customer(existing.id, user_id, email)
            return self._order_from_payment(existing, email, created=False)

        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            assert payment is not None
            return self._order_from_payment(payment, email, created=True)

    def get_or_create_payment(self, user_id: int) -> PaymentServiceResult:
        """Adapter used by the provider-neutral Telegram purchase flow."""
        with self._session_factory() as session:
            customer_email = receipt_email_in_session(session, user_id)
        if customer_email is None:
            raise ReceiptEmailRequired("Receipt email is required.")
        order = self.create_order(user_id, customer_email)
        return self._result_from_order(order)

    def get_latest_payment(self, user_id: int) -> PaymentServiceResult | None:
        """Return local state only; PayAnyWay has no pull status endpoint."""
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment)
                .where(
                    SubscriptionPayment.user_id == user_id,
                    SubscriptionPayment.product_code == self._product.product_code,
                    (
                        (SubscriptionPayment.provider == PAYANYWAY_PROVIDER)
                        | (SubscriptionPayment.status == PaymentStatus.SUCCEEDED.value)
                    ),
                )
                .order_by(SubscriptionPayment.id.desc())
            )
            if payment is None:
                return None
            return self._result_from_payment(payment)

    def reconcile_payment(
        self, payment_id: int, *, user_id: int | None = None
    ) -> PaymentServiceResult:
        """Read PayURL-confirmed local state without inventing a status API."""
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None or payment.provider != PAYANYWAY_PROVIDER:
                raise LookupError(PaymentServiceReason.PAYMENT_NOT_FOUND.value)
            if user_id is not None and payment.user_id != user_id:
                raise PermissionError(PaymentServiceReason.OWNERSHIP_MISMATCH.value)
            return self._result_from_payment(payment)

    def process_payurl(self, payload: Mapping[str, object]) -> PayAnyWayCallbackResult:
        """Authenticate a PayURL report and confirm its order exactly once."""
        parsed = self._parse_callback(payload)
        if isinstance(parsed, PayAnyWayCallbackResult):
            return parsed
        fields = parsed

        expected_signature = self._payurl_signature(fields)
        if not compare_digest(fields["MNT_SIGNATURE"].lower(), expected_signature):
            return self._rejected(fields, PayAnyWayCallbackReason.INVALID_SIGNATURE)
        if fields["MNT_ID"] != self._config.merchant_id:
            return self._rejected(fields, PayAnyWayCallbackReason.MERCHANT_MISMATCH)
        if fields["MNT_TEST_MODE"] != self._config.test_mode_value:
            return self._rejected(fields, PayAnyWayCallbackReason.MODE_MISMATCH)
        if fields["MNT_AMOUNT"] != self._amount_text():
            return self._rejected(fields, PayAnyWayCallbackReason.AMOUNT_MISMATCH)
        if fields["MNT_CURRENCY_CODE"] != self._product.currency:
            return self._rejected(fields, PayAnyWayCallbackReason.CURRENCY_MISMATCH)

        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.provider == PAYANYWAY_PROVIDER,
                    SubscriptionPayment.merchant_transaction_id
                    == fields["MNT_TRANSACTION_ID"],
                    SubscriptionPayment.product_code == self._product.product_code,
                )
            )
            if payment is None:
                return self._rejected(
                    fields, PayAnyWayCallbackReason.PAYMENT_NOT_FOUND
                )
            payment_id = payment.id
            user_id = payment.user_id
            customer = receipt_email_in_session(session, user_id)
            if customer is None:
                return self._rejected(
                    fields, PayAnyWayCallbackReason.OWNERSHIP_MISMATCH
                )
            if fields["MNT_SUBSCRIBER_ID"] != str(user_id):
                return self._rejected(
                    fields, PayAnyWayCallbackReason.OWNERSHIP_MISMATCH
                )
            if (
                payment.amount_minor != self._product.amount_minor
                or payment.currency != self._product.currency
                or payment.period_days != self._product.period_days
            ):
                return self._rejected(
                    fields, PayAnyWayCallbackReason.AMOUNT_MISMATCH
                )

        if not self._claim_operation(payment_id, fields["MNT_OPERATION_ID"]):
            return self._rejected(
                fields, PayAnyWayCallbackReason.OPERATION_MISMATCH
            )

        provider_payment = ProviderPayment(
            provider_payment_id=fields["MNT_OPERATION_ID"],
            status=PaymentStatus.SUCCEEDED,
            amount_minor=self._product.amount_minor,
            currency=self._product.currency,
            metadata={
                "local_payment_id": str(payment_id),
                "product_code": self._product.product_code,
            },
            is_test=self._config.test_mode,
        )
        try:
            result = self._confirmation_service.confirm_authoritative_payment(
                payment_id,
                provider=PAYANYWAY_PROVIDER,
                provider_payment=provider_payment,
            )
        except (LookupError, PermissionError, IntegrityError):
            return self._rejected(
                fields, PayAnyWayCallbackReason.CONFIRMATION_REJECTED
            )
        accepted_reasons = {
            PaymentServiceReason.ACCESS_APPLIED,
            PaymentServiceReason.ACCESS_ALREADY_APPLIED,
        }
        if result.reason not in accepted_reasons:
            return self._rejected(
                fields, PayAnyWayCallbackReason.CONFIRMATION_REJECTED
            )
        reason = (
            PayAnyWayCallbackReason.CONFIRMED
            if result.reason is PaymentServiceReason.ACCESS_APPLIED
            else PayAnyWayCallbackReason.ALREADY_CONFIRMED
        )
        return PayAnyWayCallbackResult(
            True,
            reason,
            self._response_xml(
                fields["MNT_TRANSACTION_ID"],
                PAYANYWAY_SUCCESS_CODE,
                customer=customer,
            ),
            payment_id=payment_id,
            access_applied=result.access_applied,
        )

    def inspect_browser_success(
        self, transaction_id: str, *, user_id: int
    ) -> PayAnyWayBrowserReturn:
        """Read browser-return state; never promote or reconcile a payment."""
        if not isinstance(transaction_id, str):
            return PayAnyWayBrowserReturn("", None, False)
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.provider == PAYANYWAY_PROVIDER,
                    SubscriptionPayment.merchant_transaction_id == transaction_id,
                    SubscriptionPayment.user_id == user_id,
                )
            )
            if payment is None:
                return PayAnyWayBrowserReturn(transaction_id, None, False)
            status = PaymentStatus(payment.status)
            return PayAnyWayBrowserReturn(
                transaction_id,
                status,
                status is PaymentStatus.SUCCEEDED
                and payment.access_applied_at is not None,
            )

    def _parse_callback(
        self, payload: Mapping[str, object]
    ) -> dict[str, str] | PayAnyWayCallbackResult:
        required = (
            "MNT_ID",
            "MNT_TRANSACTION_ID",
            "MNT_OPERATION_ID",
            "MNT_AMOUNT",
            "MNT_CURRENCY_CODE",
            "MNT_SUBSCRIBER_ID",
            "MNT_TEST_MODE",
            "MNT_SIGNATURE",
        )
        if not isinstance(payload, Mapping):
            return self._malformed()
        fields: dict[str, str] = {}
        for name in required:
            value = payload.get(name)
            if not isinstance(value, str) or not value or len(value) > 512:
                return self._malformed()
            fields[name] = value
        if (
            not _TRANSACTION_ID.fullmatch(fields["MNT_TRANSACTION_ID"])
            or not _OPERATION_ID.fullmatch(fields["MNT_OPERATION_ID"])
            or not re.fullmatch(r"[0-9a-fA-F]{32}", fields["MNT_SIGNATURE"])
            or not re.fullmatch(r"[0-9]+", fields["MNT_SUBSCRIBER_ID"])
            or fields["MNT_TEST_MODE"] not in {"0", "1"}
        ):
            return self._malformed(fields.get("MNT_TRANSACTION_ID"))
        try:
            amount = Decimal(fields["MNT_AMOUNT"])
        except InvalidOperation:
            return self._malformed(fields["MNT_TRANSACTION_ID"])
        if amount.as_tuple().exponent != -2 or amount <= 0:
            return self._malformed(fields["MNT_TRANSACTION_ID"])
        return fields

    def _claim_operation(self, payment_id: int, operation_id: str) -> bool:
        with self._session_factory() as session:
            with session.begin():
                payment = session.get(SubscriptionPayment, payment_id)
                if payment is None:
                    return False
                if payment.provider_payment_id is not None:
                    return payment.provider_payment_id == operation_id
                claimed = session.execute(
                    update(SubscriptionPayment)
                    .where(
                        SubscriptionPayment.id == payment_id,
                        SubscriptionPayment.provider == PAYANYWAY_PROVIDER,
                        SubscriptionPayment.provider_payment_id.is_(None),
                    )
                    .values(provider_payment_id=operation_id, updated_at=self._now())
                )
                if claimed.rowcount == 1:
                    return True
                session.refresh(payment)
                return payment.provider_payment_id == operation_id

    def _find_active_order(self, user_id: int) -> SubscriptionPayment | None:
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment)
                .where(
                    SubscriptionPayment.user_id == user_id,
                    SubscriptionPayment.provider == PAYANYWAY_PROVIDER,
                    SubscriptionPayment.product_code == self._product.product_code,
                    SubscriptionPayment.status.in_(
                        (
                            PaymentStatus.CREATING.value,
                            PaymentStatus.PENDING.value,
                            PaymentStatus.WAITING_FOR_CAPTURE.value,
                        )
                    ),
                )
                .order_by(SubscriptionPayment.id.desc())
            )
            if payment is not None:
                session.expunge(payment)
            return payment

    def _assert_customer(self, payment_id: int, user_id: int, email: str) -> None:
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None or payment.user_id != user_id:
                raise PermissionError("Payment ownership mismatch.")
            self._assert_user_and_customer(session, user_id, email)

    @staticmethod
    def _assert_user_and_customer(
        session: Session, user_id: int, email: str
    ) -> None:
        if session.get(User, user_id) is None or session.get(UserAccess, user_id) is None:
            raise LookupError("Payment user does not exist.")
        stored = receipt_email_in_session(session, user_id)
        if stored is None or stored != email:
            raise PermissionError("Receipt email mismatch.")

    def _order_from_payment(
        self, payment: SubscriptionPayment, email: str, *, created: bool
    ) -> PayAnyWayOrder:
        transaction_id = payment.merchant_transaction_id
        if transaction_id is None:
            raise RuntimeError("PayAnyWay transaction identity is missing.")
        payment_url = payment.confirmation_url or self._build_payment_url(payment, email)
        return PayAnyWayOrder(
            payment.id,
            transaction_id,
            payment_url,
            email,
            self._inventory_json(),
            PaymentStatus(payment.status),
            created,
        )

    @staticmethod
    def _result_from_order(order: PayAnyWayOrder) -> PaymentServiceResult:
        return PaymentServiceResult(
            payment_id=order.payment_id,
            status=order.status,
            confirmation_url=order.payment_url,
            reason=(
                PaymentServiceReason.CREATED
                if order.created
                else PaymentServiceReason.ACTIVE_PAYMENT_REUSED
            ),
            created=order.created,
        )

    @staticmethod
    def _result_from_payment(payment: SubscriptionPayment) -> PaymentServiceResult:
        status = PaymentStatus(payment.status)
        reason = (
            PaymentServiceReason.ACCESS_ALREADY_APPLIED
            if status is PaymentStatus.SUCCEEDED
            and payment.access_applied_at is not None
            else PaymentServiceReason.NOT_SUCCEEDED
        )
        return PaymentServiceResult(
            payment_id=payment.id,
            status=status,
            confirmation_url=payment.confirmation_url,
            reason=reason,
            access_applied=payment.access_applied_at is not None,
            grant_started_at=payment.grant_started_at,
            grant_ends_at=payment.grant_ends_at,
        )

    def _build_payment_url(
        self, payment: SubscriptionPayment, customer_email: str
    ) -> str:
        transaction_id = payment.merchant_transaction_id
        if transaction_id is None:
            raise RuntimeError("PayAnyWay transaction identity is missing.")
        fields = {
            "MNT_ID": self._config.merchant_id,
            "MNT_TRANSACTION_ID": transaction_id,
            "MNT_AMOUNT": self._amount_text(),
            "MNT_CURRENCY_CODE": self._product.currency,
            "MNT_SUBSCRIBER_ID": str(payment.user_id),
            "MNT_TEST_MODE": self._config.test_mode_value,
            "MNT_DESCRIPTION": self._product.receipt_description,
            "MNT_SUCCESS_URL": self._config.success_url,
            "MNT_FAIL_URL": self._config.fail_url,
            "MNT_RETURN_URL": self._config.return_url,
            "MNT_CUSTOM1": customer_email,
            "MNT_CUSTOM2": self._request_inventory_json(customer_email),
        }
        fields["MNT_SIGNATURE"] = self._payment_request_signature(
            transaction_id, str(payment.user_id)
        )
        return f"{self._config.payment_url}?{urlencode(fields)}"

    def _payment_request_signature(
        self, transaction_id: str, subscriber_id: str
    ) -> str:
        return _md5_hex(
            self._config.merchant_id,
            transaction_id,
            self._amount_text(),
            self._product.currency,
            subscriber_id,
            self._config.test_mode_value,
            self._config.integrity_code,
        )

    def _payurl_signature(self, fields: Mapping[str, str]) -> str:
        return _md5_hex(
            fields["MNT_ID"],
            fields["MNT_TRANSACTION_ID"],
            fields["MNT_OPERATION_ID"],
            fields["MNT_AMOUNT"],
            fields["MNT_CURRENCY_CODE"],
            fields["MNT_SUBSCRIBER_ID"],
            fields["MNT_TEST_MODE"],
            self._config.integrity_code,
        )

    def _response_signature(self, result_code: str, transaction_id: str) -> str:
        return _md5_hex(
            result_code,
            self._config.merchant_id,
            transaction_id,
            self._config.integrity_code,
        )

    def _response_xml(
        self,
        transaction_id: str,
        result_code: str,
        *,
        customer: str | None = None,
    ) -> str:
        root = ElementTree.Element("MNT_RESPONSE")
        for name, value in (
            ("MNT_ID", self._config.merchant_id),
            ("MNT_TRANSACTION_ID", transaction_id),
            ("MNT_RESULT_CODE", result_code),
            (
                "MNT_DESCRIPTION",
                "Payment accepted" if result_code == PAYANYWAY_SUCCESS_CODE else "Payment rejected",
            ),
            ("MNT_AMOUNT", self._amount_text()),
            ("MNT_SIGNATURE", self._response_signature(result_code, transaction_id)),
        ):
            ElementTree.SubElement(root, name).text = value
        if result_code == PAYANYWAY_SUCCESS_CODE and customer is not None:
            attributes = ElementTree.SubElement(root, "MNT_ATTRIBUTES")
            self._append_attribute(attributes, "INVENTORY", self._inventory_json())
            self._append_attribute(attributes, "CUSTOMER", customer)
        xml_body = ElementTree.tostring(root, encoding="unicode", short_empty_elements=False)
        return f'<?xml version="1.0" encoding="UTF-8"?>{xml_body}'

    @staticmethod
    def _append_attribute(parent, key: str, value: str) -> None:
        attribute = ElementTree.SubElement(parent, "ATTRIBUTE")
        ElementTree.SubElement(attribute, "KEY").text = key
        ElementTree.SubElement(attribute, "VALUE").text = value

    def _inventory_json(self) -> str:
        return json.dumps(
            [
                {
                    "name": self._product.receipt_description,
                    "price": self._amount_text(),
                    "quantity": "1",
                    "vatTag": PAYANYWAY_VAT_NONE,
                    "pm": "full_payment",
                    "po": "service",
                    "idInternal": self._product.product_code,
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _request_inventory_json(self, customer: str) -> str:
        return json.dumps(
            {
                "customer": customer,
                "items": [
                    {
                        "n": self._product.receipt_description,
                        "p": self._amount_text(),
                        "q": "1",
                        "t": PAYANYWAY_VAT_NONE,
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _rejected(
        self, fields: Mapping[str, str], reason: PayAnyWayCallbackReason
    ) -> PayAnyWayCallbackResult:
        transaction_id = fields.get("MNT_TRANSACTION_ID", "invalid")
        if not _TRANSACTION_ID.fullmatch(transaction_id):
            transaction_id = "invalid"
        return PayAnyWayCallbackResult(
            False,
            reason,
            self._response_xml(transaction_id, PAYANYWAY_REJECT_CODE),
        )

    def _malformed(self, transaction_id: str | None = None) -> PayAnyWayCallbackResult:
        fields = {"MNT_TRANSACTION_ID": transaction_id or "invalid"}
        return self._rejected(fields, PayAnyWayCallbackReason.MALFORMED)

    def _amount_text(self) -> str:
        return f"{Decimal(self._product.amount_minor) / Decimal(100):.2f}"

    def _now(self) -> datetime:
        return as_utc_naive(self._now_factory())


def _safe_identifier(value: object, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and all(not character.isspace() and ord(character) >= 32 for character in value)
    )


def _md5_hex(*parts: str) -> str:
    """Implement the provider-mandated MD5 protocol without logging inputs."""
    return md5("".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
