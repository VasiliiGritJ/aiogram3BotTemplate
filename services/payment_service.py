"""Idempotent local payment orchestration without a concrete payment SDK."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import SubscriptionPayment, UserAccess, dbSession
from services.access import as_utc_naive, utc_now
from services.payment_domain import (
    PaymentReasonCode,
    PaymentSpec,
    PaymentState,
    PaymentStatus,
    calculate_subscription_grant,
    get_access_eligibility,
    get_creating_recovery,
    transition_payment_status,
    validate_payment_spec,
)
from services.payment_provider import (
    PaymentCreateRequest,
    PaymentCreationGuard,
    PaymentProvider,
    PaymentProviderError,
    ProviderPayment,
)


class PaymentServiceReason(StrEnum):
    CREATED = "created"
    ACTIVE_PAYMENT_REUSED = "active_payment_reused"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RECOVERY_REQUIRED = "recovery_required"
    PAYMENT_NOT_FOUND = "payment_not_found"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    PROVIDER_ID_MISMATCH = "provider_id_mismatch"
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    METADATA_MISMATCH = "metadata_mismatch"
    TEST_MODE_REJECTED = "test_mode_rejected"
    STATUS_REJECTED = "status_rejected"
    ACCESS_APPLIED = "access_applied"
    ACCESS_ALREADY_APPLIED = "access_already_applied"
    NOT_SUCCEEDED = "not_succeeded"


@dataclass(frozen=True)
class PaymentServiceResult:
    payment_id: int
    status: PaymentStatus
    confirmation_url: str | None
    reason: PaymentServiceReason
    created: bool = False
    access_applied: bool = False
    grant_started_at: datetime | None = None
    grant_ends_at: datetime | None = None


@dataclass(frozen=True)
class SubscriptionProduct:
    """Server-owned product configuration; price is supplied by application config."""

    product_code: str
    amount_minor: int
    currency: str
    period_days: int = 30

    @property
    def spec(self) -> PaymentSpec:
        return PaymentSpec(
            product_code=self.product_code,
            amount_minor=self.amount_minor,
            currency=self.currency,
            period_days=self.period_days,
        )


_ACTIVE_STATUSES = (
    PaymentStatus.CREATING.value,
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_FOR_CAPTURE.value,
)


class PaymentService:
    """Persist first, reconcile provider state, then atomically grant paid access."""

    def __init__(
        self,
        provider: PaymentProvider,
        product: SubscriptionProduct,
        *,
        session_factory: Callable[[], Session] = dbSession,
        now_factory: Callable[[], datetime] = utc_now,
        idempotency_key_factory: Callable[[], str] | None = None,
        provider_idempotency_window: timedelta = timedelta(hours=24),
        before_access_apply: Callable[[], None] | None = None,
        require_test_mode: bool = False,
    ) -> None:
        validation = validate_payment_spec(product.spec)
        if not validation.is_valid:
            raise ValueError(f"Invalid subscription product: {validation.reason}")
        self._provider = provider
        self._product = product
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._idempotency_key_factory = idempotency_key_factory or (
            lambda: str(uuid4())
        )
        self._provider_idempotency_window = provider_idempotency_window
        self._before_access_apply = before_access_apply
        if not isinstance(require_test_mode, bool):
            raise ValueError("require_test_mode must be a boolean")
        self._require_test_mode = require_test_mode

    def get_or_create_payment(self, user_id: int) -> PaymentServiceResult:
        """Return the one active payment or create one local request safely."""
        existing_payment_id = self._get_active_payment_id(user_id)
        if existing_payment_id is None:
            self._ensure_payment_creation_allowed()
        payment_id, created = self._get_or_create_local_payment(user_id)
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            assert payment is not None
            has_provider_id = payment.provider_payment_id is not None
            recovery = get_creating_recovery(
                self._payment_state(payment),
                self._now(),
                self._provider_idempotency_window,
            )

        if has_provider_id:
            result = self.reconcile_payment(payment_id, user_id=user_id)
            return self._with_created(result, created)
        if not recovery.retry_same_key:
            return self._result_from_payment(
                payment,
                PaymentServiceReason.RECOVERY_REQUIRED,
                created=created,
            )
        if existing_payment_id is not None:
            self._ensure_payment_creation_allowed()
        return self._create_with_provider(payment_id, user_id, created)

    def _ensure_payment_creation_allowed(self) -> None:
        if isinstance(self._provider, PaymentCreationGuard):
            self._provider.ensure_payment_creation_allowed()

    def _get_active_payment_id(self, user_id: int) -> int | None:
        with self._session_factory() as session:
            active = self._find_active_payment(session, user_id)
            return active.id if active is not None else None

    def reconcile_payment(
        self,
        payment_id: int,
        *,
        user_id: int | None = None,
    ) -> PaymentServiceResult:
        """Refresh one local payment from the provider without trusting the client."""
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None:
                raise LookupError(PaymentServiceReason.PAYMENT_NOT_FOUND.value)
            if user_id is not None and payment.user_id != user_id:
                raise PermissionError(PaymentServiceReason.OWNERSHIP_MISMATCH.value)
            provider_payment_id = payment.provider_payment_id
            if provider_payment_id is None:
                return self._result_from_payment(
                    payment,
                    PaymentServiceReason.RECOVERY_REQUIRED,
                )

        try:
            provider_payment = self._provider.get_payment(provider_payment_id)
        except PaymentProviderError:
            return self._result_for_id(payment_id, PaymentServiceReason.PROVIDER_UNAVAILABLE)
        return self._reconcile_provider_payment(payment_id, provider_payment)

    def get_latest_payment(self, user_id: int) -> PaymentServiceResult | None:
        """Read the latest local payment for the configured product only."""
        with self._session_factory() as session:
            payment = session.scalar(
                select(SubscriptionPayment)
                .where(
                    SubscriptionPayment.user_id == user_id,
                    SubscriptionPayment.product_code == self._product.product_code,
                )
                .order_by(SubscriptionPayment.id.desc())
            )
            if payment is None:
                return None
            return self._result_from_payment(payment, PaymentServiceReason.NOT_SUCCEEDED)

    def _get_or_create_local_payment(self, user_id: int) -> tuple[int, bool]:
        try:
            with self._session_factory() as session:
                with session.begin():
                    active = self._find_active_payment(session, user_id)
                    if active is not None:
                        return active.id, False
                    payment = SubscriptionPayment(
                        user_id=user_id,
                        provider="yookassa",
                        idempotency_key=self._idempotency_key_factory(),
                        product_code=self._product.product_code,
                        amount_minor=self._product.amount_minor,
                        currency=self._product.currency,
                        period_days=self._product.period_days,
                        status=PaymentStatus.CREATING.value,
                        created_at=self._now(),
                        updated_at=self._now(),
                    )
                    session.add(payment)
                    session.flush()
                    return payment.id, True
        except IntegrityError:
            # SQLite's partial unique index is the durable guard against a
            # simultaneous second click.  Reload the winning active record.
            with self._session_factory() as session:
                active = self._find_active_payment(session, user_id)
                if active is not None:
                    return active.id, False
            raise

    def _create_with_provider(
        self,
        payment_id: int,
        user_id: int,
        created: bool,
    ) -> PaymentServiceResult:
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None or payment.user_id != user_id:
                raise LookupError(PaymentServiceReason.PAYMENT_NOT_FOUND.value)
            request = PaymentCreateRequest(
                local_payment_id=payment.id,
                spec=self._payment_spec(payment),
                metadata={
                    "local_payment_id": str(payment.id),
                    "product_code": payment.product_code,
                },
            )
            idempotency_key = payment.idempotency_key
        try:
            provider_payment = self._provider.create_payment(request, idempotency_key)
        except PaymentProviderError:
            self._mark_provider_unavailable(payment_id)
            return self._result_for_id(
                payment_id,
                PaymentServiceReason.PROVIDER_UNAVAILABLE,
                created=created,
            )
        return self._with_created(
            self._reconcile_provider_payment(payment_id, provider_payment), created
        )

    def _reconcile_provider_payment(
        self,
        payment_id: int,
        provider_payment: ProviderPayment,
    ) -> PaymentServiceResult:
        validation_reason = self._validate_provider_payment(payment_id, provider_payment)
        if validation_reason is not None:
            return self._result_for_id(payment_id, validation_reason)

        with self._session_factory() as session:
            with session.begin():
                payment = session.get(SubscriptionPayment, payment_id)
                assert payment is not None
                transition = transition_payment_status(
                    PaymentStatus(payment.status), provider_payment.status
                )
                if transition.accepted:
                    payment.status = transition.status.value
                    payment.provider_payment_id = provider_payment.provider_payment_id
                    payment.confirmation_url = provider_payment.confirmation_url
                    payment.provider_expires_at = provider_payment.expires_at
                    payment.cancellation_code = provider_payment.cancellation_code
                    payment.last_checked_at = self._now()
                    payment.updated_at = self._now()
                    if transition.status is PaymentStatus.SUCCEEDED:
                        payment.confirmed_at = self._now()
                elif PaymentStatus(payment.status) is not PaymentStatus.SUCCEEDED:
                    return self._result_from_payment(
                        payment, PaymentServiceReason.STATUS_REJECTED
                    )

                if PaymentStatus(payment.status) is PaymentStatus.SUCCEEDED:
                    return self._apply_access_in_session(session, payment)
                return self._result_from_payment(payment, PaymentServiceReason.NOT_SUCCEEDED)

    def _validate_provider_payment(
        self,
        payment_id: int,
        provider_payment: ProviderPayment,
    ) -> PaymentServiceReason | None:
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None:
                raise LookupError(PaymentServiceReason.PAYMENT_NOT_FOUND.value)
            if self._require_test_mode and provider_payment.is_test is not True:
                return PaymentServiceReason.TEST_MODE_REJECTED
            if (
                payment.provider_payment_id is not None
                and payment.provider_payment_id != provider_payment.provider_payment_id
            ):
                return PaymentServiceReason.PROVIDER_ID_MISMATCH
            existing_provider_payment = session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.provider == payment.provider,
                    SubscriptionPayment.provider_payment_id
                    == provider_payment.provider_payment_id,
                )
            )
            if (
                existing_provider_payment is not None
                and existing_provider_payment.id != payment.id
            ):
                return PaymentServiceReason.PROVIDER_ID_MISMATCH
            if provider_payment.amount_minor != payment.amount_minor:
                return PaymentServiceReason.AMOUNT_MISMATCH
            if provider_payment.currency != payment.currency:
                return PaymentServiceReason.CURRENCY_MISMATCH
            metadata = provider_payment.metadata
            if (
                metadata.get("local_payment_id") != str(payment.id)
                or metadata.get("product_code") != payment.product_code
            ):
                return PaymentServiceReason.METADATA_MISMATCH
        return None

    def _apply_access_in_session(
        self,
        session: Session,
        payment: SubscriptionPayment,
    ) -> PaymentServiceResult:
        eligibility = get_access_eligibility(self._payment_state(payment))
        if not eligibility.can_apply:
            return self._result_from_payment(
                payment,
                PaymentServiceReason.ACCESS_ALREADY_APPLIED
                if eligibility.reason is PaymentReasonCode.ACCESS_ALREADY_APPLIED
                else PaymentServiceReason.NOT_SUCCEEDED,
            )

        now = self._now()
        access = session.get(UserAccess, payment.user_id)
        if access is None:
            raise RuntimeError("Access record does not exist for payment user.")
        grant = calculate_subscription_grant(
            now=now,
            period_days=payment.period_days,
            trial_ends_at=access.trial_ends_at,
            subscription_ends_at=access.subscription_ends_at,
        )
        claimed = session.execute(
            update(SubscriptionPayment)
            .where(
                SubscriptionPayment.id == payment.id,
                SubscriptionPayment.status == PaymentStatus.SUCCEEDED.value,
                SubscriptionPayment.access_applied_at.is_(None),
            )
            .values(
                access_applied_at=now,
                grant_started_at=grant.started_at,
                grant_ends_at=grant.ends_at,
                updated_at=now,
            )
        )
        if claimed.rowcount != 1:
            session.refresh(payment)
            return self._result_from_payment(
                payment, PaymentServiceReason.ACCESS_ALREADY_APPLIED
            )

        if self._before_access_apply is not None:
            self._before_access_apply()
        if access.subscription_ends_at is None or access.subscription_ends_at <= now:
            access.subscription_started_at = grant.started_at
        access.subscription_ends_at = grant.ends_at
        access.updated_at = now
        payment.access_applied_at = now
        payment.grant_started_at = grant.started_at
        payment.grant_ends_at = grant.ends_at
        return self._result_from_payment(
            payment,
            PaymentServiceReason.ACCESS_APPLIED,
            access_applied=True,
        )

    def _mark_provider_unavailable(self, payment_id: int) -> None:
        with self._session_factory() as session:
            with session.begin():
                payment = session.get(SubscriptionPayment, payment_id)
                if payment is not None and payment.status == PaymentStatus.CREATING.value:
                    payment.failure_code = "provider_unavailable"
                    payment.updated_at = self._now()

    def _find_active_payment(
        self, session: Session, user_id: int
    ) -> SubscriptionPayment | None:
        return session.scalar(
            select(SubscriptionPayment)
            .where(
                SubscriptionPayment.user_id == user_id,
                SubscriptionPayment.product_code == self._product.product_code,
                SubscriptionPayment.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(SubscriptionPayment.id.desc())
        )

    @staticmethod
    def _payment_spec(payment: SubscriptionPayment) -> PaymentSpec:
        return PaymentSpec(
            payment.product_code,
            payment.amount_minor,
            payment.currency,
            payment.period_days,
        )

    def _payment_state(self, payment: SubscriptionPayment) -> PaymentState:
        return PaymentState(
            status=PaymentStatus(payment.status),
            spec=self._payment_spec(payment),
            created_at=payment.created_at,
            access_applied_at=payment.access_applied_at,
        )

    def _result_for_id(
        self,
        payment_id: int,
        reason: PaymentServiceReason,
        *,
        created: bool = False,
    ) -> PaymentServiceResult:
        with self._session_factory() as session:
            payment = session.get(SubscriptionPayment, payment_id)
            if payment is None:
                raise LookupError(PaymentServiceReason.PAYMENT_NOT_FOUND.value)
            return self._result_from_payment(payment, reason, created=created)

    @staticmethod
    def _result_from_payment(
        payment: SubscriptionPayment,
        reason: PaymentServiceReason,
        *,
        created: bool = False,
        access_applied: bool = False,
    ) -> PaymentServiceResult:
        return PaymentServiceResult(
            payment_id=payment.id,
            status=PaymentStatus(payment.status),
            confirmation_url=payment.confirmation_url,
            reason=reason,
            created=created,
            access_applied=access_applied,
            grant_started_at=payment.grant_started_at,
            grant_ends_at=payment.grant_ends_at,
        )

    @staticmethod
    def _with_created(
        result: PaymentServiceResult, created: bool
    ) -> PaymentServiceResult:
        return PaymentServiceResult(
            **{**result.__dict__, "created": created}
        )

    def _now(self) -> datetime:
        return as_utc_naive(self._now_factory())
