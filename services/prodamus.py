"""Prodamus hosted checkout and signed notification adapter.

The provider never decides price, access or Telegram identity.  This module
only prepares a signed hosted form and accepts an already signed notification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
import json
import secrets
from typing import Callable, Mapping
from urllib.parse import urlencode, urlparse

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import SubscriptionPayment, User, UserAccess, dbSession
from services.access import as_utc_naive, utc_now
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from services.payment_domain import PaymentStatus
from services.payment_provider import ProviderPayment
from services.payment_service import PaymentService, PaymentServiceReason
from services.receipt_email import normalize_receipt_email, receipt_email_in_session


PRODAMUS_PROVIDER = "prodamus"


class ProdamusConfigurationError(ValueError):
    """Configuration is incomplete or unsafe without echoing a secret."""


class ProdamusCallbackReason(StrEnum):
    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    INVALID_SIGNATURE = "invalid_signature"
    MALFORMED = "malformed"
    PAYMENT_NOT_FOUND = "payment_not_found"
    AMOUNT_MISMATCH = "amount_mismatch"
    CUSTOMER_MISMATCH = "customer_mismatch"
    OPERATION_MISMATCH = "operation_mismatch"
    STATUS_REJECTED = "status_rejected"


@dataclass(frozen=True)
class ProdamusConfig:
    payform_url: str
    secret_key: str
    notification_url: str
    success_url: str
    fail_url: str
    return_url: str
    system_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.secret_key, str) or len(self.secret_key) < 16:
            raise ProdamusConfigurationError("Prodamus secret is missing.")
        for value in (
            self.payform_url,
            self.notification_url,
            self.success_url,
            self.fail_url,
            self.return_url,
        ):
            _validate_https_url(value)
        if not isinstance(self.system_code, str):
            raise ProdamusConfigurationError("Prodamus system code is invalid.")


@dataclass(frozen=True)
class ProdamusOrder:
    payment_id: int
    transaction_id: str
    checkout_url: str
    customer_email: str
    created: bool


@dataclass(frozen=True)
class ProdamusCallbackResult:
    accepted: bool
    reason: ProdamusCallbackReason
    payment_id: int | None = None
    access_applied: bool = False


class _NoNetworkProvider:
    """For callback authentication: never allow a provider HTTP lookup."""

    def create_payment(self, request, idempotency_key):
        del request, idempotency_key
        raise RuntimeError("External creation is not available here.")

    def get_payment(self, provider_payment_id):
        del provider_payment_id
        raise RuntimeError("External lookup is not available here.")


def build_prodamus_signature(payload: Mapping[str, object], secret_key: str) -> str:
    """Canonical HMAC-SHA256 used by Prodamus for form and notification data."""
    if not isinstance(payload, Mapping) or not isinstance(secret_key, str):
        raise ValueError("Prodamus signature input is invalid.")
    canonical = json.dumps(
        _canonical_value(payload), ensure_ascii=False, separators=(",", ":")
    ).replace("/", r"\/")
    return hmac_new(secret_key.encode("utf-8"), canonical.encode("utf-8"), sha256).hexdigest()


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class ProdamusService:
    """Persist a server-owned 499 RUB order and confirm it exactly once."""

    def __init__(
        self,
        config: ProdamusConfig,
        *,
        session_factory: Callable[[], Session] = dbSession,
        now_factory: Callable[[], datetime] = utc_now,
        transaction_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._transaction_id_factory = transaction_id_factory or (
            lambda: f"prodamus-{secrets.token_urlsafe(18)}"
        )
        self._product = LAUNCH_COMMERCIAL_PRODUCT
        self._confirmation_service = PaymentService(
            _NoNetworkProvider(),
            self._product.payment_product,
            session_factory=session_factory,
            now_factory=now_factory,
            expected_test_mode=False,
            provider_name=PRODAMUS_PROVIDER,
        )

    def create_order(self, user_id: int, customer_email: str) -> ProdamusOrder:
        email = normalize_receipt_email(customer_email)
        active = self._find_active_order(user_id)
        if active is not None:
            self._assert_customer(active, email)
            return self._order_from_payment(active, email, created=False)
        try:
            with self._session_factory() as session:
                with session.begin():
                    self._assert_user_and_access(session, user_id)
                    existing_email = receipt_email_in_session(session, user_id)
                    if existing_email is not None and existing_email != email:
                        raise PermissionError("Receipt email mismatch.")
                    transaction_id = self._transaction_id_factory()
                    if not isinstance(transaction_id, str) or len(transaction_id) > 255:
                        raise RuntimeError("Transaction identity is invalid.")
                    payment = SubscriptionPayment(
                        user_id=user_id,
                        provider=PRODAMUS_PROVIDER,
                        merchant_transaction_id=transaction_id,
                        buyer_email=email,
                        idempotency_key=f"prodamus:{secrets.token_urlsafe(18)}",
                        product_code=self._product.product_code,
                        amount_minor=self._product.amount_minor,
                        currency=self._product.currency,
                        period_days=self._product.period_days,
                        status=PaymentStatus.PENDING.value,
                        created_at=self._now(),
                        updated_at=self._now(),
                    )
                    session.add(payment)
                    session.flush()
                    payment.confirmation_url = self._build_checkout_url(payment, email)
                    return self._order_from_payment(payment, email, created=True)
        except IntegrityError:
            active = self._find_active_order(user_id)
            if active is None:
                raise
            self._assert_customer(active, email)
            return self._order_from_payment(active, email, created=False)

    def process_notification(
        self, payload: Mapping[str, object], signature: str | None
    ) -> ProdamusCallbackResult:
        if not isinstance(signature, str) or not compare_digest(
            build_prodamus_signature(payload, self._config.secret_key), signature.lower()
        ):
            return ProdamusCallbackResult(False, ProdamusCallbackReason.INVALID_SIGNATURE)
        fields = _notification_fields(payload)
        if fields is None:
            return ProdamusCallbackResult(False, ProdamusCallbackReason.MALFORMED)
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.provider == PRODAMUS_PROVIDER,
                    SubscriptionPayment.merchant_transaction_id == fields["order_num"],
                )
            )
            if payment is None:
                return ProdamusCallbackResult(False, ProdamusCallbackReason.PAYMENT_NOT_FOUND)
            payment_id = payment.id
            if payment.buyer_email != fields["customer_email"]:
                return ProdamusCallbackResult(False, ProdamusCallbackReason.CUSTOMER_MISMATCH)
            if payment.amount_minor != _amount_minor(fields["sum"]):
                return ProdamusCallbackResult(False, ProdamusCallbackReason.AMOUNT_MISMATCH)
        if not self._claim_operation(payment_id, fields["order_id"]):
            return ProdamusCallbackResult(False, ProdamusCallbackReason.OPERATION_MISMATCH)
        provider_payment = ProviderPayment(
            provider_payment_id=fields["order_id"],
            status=PaymentStatus.SUCCEEDED,
            amount_minor=_amount_minor(fields["sum"]),
            currency="RUB",
            confirmation_url=None,
            metadata={
                "local_payment_id": str(payment_id),
                "product_code": self._product.product_code,
            },
            is_test=False,
        )
        try:
            result = self._confirmation_service.confirm_authoritative_payment(
                payment_id, provider=PRODAMUS_PROVIDER, provider_payment=provider_payment
            )
        except (IntegrityError, LookupError, PermissionError):
            return ProdamusCallbackResult(False, ProdamusCallbackReason.STATUS_REJECTED)
        if result.reason is PaymentServiceReason.ACCESS_APPLIED:
            return ProdamusCallbackResult(True, ProdamusCallbackReason.CONFIRMED, payment_id, True)
        if result.reason is PaymentServiceReason.ACCESS_ALREADY_APPLIED:
            return ProdamusCallbackResult(True, ProdamusCallbackReason.ALREADY_CONFIRMED, payment_id, False)
        return ProdamusCallbackResult(False, ProdamusCallbackReason.STATUS_REJECTED)

    def inspect_browser_success(self, transaction_id: str, *, user_id: int) -> bool:
        """A browser return only reads durable state; it never grants access."""
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.provider == PRODAMUS_PROVIDER,
                    SubscriptionPayment.merchant_transaction_id == transaction_id,
                    SubscriptionPayment.user_id == user_id,
                )
            )
            return bool(
                payment is not None
                and payment.status == PaymentStatus.SUCCEEDED.value
                and payment.access_applied_at is not None
            )

    def _find_active_order(self, user_id: int) -> SubscriptionPayment | None:
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.user_id == user_id,
                    SubscriptionPayment.provider == PRODAMUS_PROVIDER,
                    SubscriptionPayment.product_code == self._product.product_code,
                    SubscriptionPayment.status.in_((
                        PaymentStatus.CREATING.value,
                        PaymentStatus.PENDING.value,
                        PaymentStatus.WAITING_FOR_CAPTURE.value,
                    )),
                )
            )
            if payment is not None:
                session.expunge(payment)
            return payment

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
                        SubscriptionPayment.provider == PRODAMUS_PROVIDER,
                        SubscriptionPayment.provider_payment_id.is_(None),
                    )
                    .values(provider_payment_id=operation_id, updated_at=self._now())
                )
                return claimed.rowcount == 1

    def _build_checkout_url(self, payment: SubscriptionPayment, email: str) -> str:
        transaction_id = payment.merchant_transaction_id
        assert transaction_id is not None
        fields: dict[str, object] = {
            "do": "pay",
            "order_id": transaction_id,
            "order_num": transaction_id,
            "customer_email": email,
            "customer_extra": json.dumps(
                {"local_payment_id": str(payment.id), "product_code": payment.product_code},
                separators=(",", ":"),
            ),
            "currency": "rub",
            "sys": self._config.system_code,
            "urlNotification": self._config.notification_url,
            "urlSuccess": self._config.success_url,
            "urlFail": self._config.fail_url,
            "urlReturn": self._config.return_url,
            "products": [{
                "name": self._product.receipt_description,
                "price": self._amount_text(),
                "quantity": 1,
                "sum": self._amount_text(),
            }],
        }
        signature = build_prodamus_signature(fields, self._config.secret_key)
        return f"{self._config.payform_url}?{urlencode(_query_pairs(fields))}&signature={signature}"

    def _order_from_payment(
        self, payment: SubscriptionPayment, email: str, *, created: bool
    ) -> ProdamusOrder:
        transaction_id = payment.merchant_transaction_id
        if transaction_id is None or payment.confirmation_url is None:
            raise RuntimeError("Prodamus order is incomplete.")
        return ProdamusOrder(payment.id, transaction_id, payment.confirmation_url, email, created)

    def _assert_customer(self, payment: SubscriptionPayment, email: str) -> None:
        if payment.buyer_email != email:
            raise PermissionError("Receipt email mismatch.")

    @staticmethod
    def _assert_user_and_access(session: Session, user_id: int) -> None:
        if session.get(User, user_id) is None or session.get(UserAccess, user_id) is None:
            raise LookupError("Website purchase user is unavailable.")

    def _amount_text(self) -> str:
        return f"{Decimal(self._product.amount_minor) / Decimal(100):.2f}"

    def _now(self) -> datetime:
        return as_utc_naive(self._now_factory())


def _query_pairs(fields: Mapping[str, object]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, value in fields.items():
        if name == "products":
            for index, item in enumerate(value):
                assert isinstance(item, Mapping)
                for item_name, item_value in item.items():
                    pairs.append((f"products[{index}][{item_name}]", str(item_value)))
        else:
            pairs.append((name, str(value)))
    return pairs


def _notification_fields(payload: Mapping[str, object]) -> dict[str, str] | None:
    required = ("order_id", "order_num", "sum", "customer_email", "payment_status")
    if not isinstance(payload, Mapping):
        return None
    result: dict[str, str] = {}
    for name in required:
        value = payload.get(name)
        if not isinstance(value, str) or not value or len(value) > 512:
            return None
        result[name] = value
    if result["payment_status"].lower() != "success":
        return None
    try:
        result["customer_email"] = normalize_receipt_email(result["customer_email"])
        _amount_minor(result["sum"])
    except (ValueError, InvalidOperation):
        return None
    return result


def _amount_minor(value: str) -> int:
    amount = Decimal(value)
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("Amount is invalid.")
    return int(amount * 100)


def _validate_https_url(value: str) -> None:
    parsed = urlparse(value)
    if (
        not isinstance(value, str)
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ProdamusConfigurationError("Prodamus URL is invalid.")
