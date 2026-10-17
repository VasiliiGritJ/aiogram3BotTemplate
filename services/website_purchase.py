"""Server-side website purchase session bound to a validated Telegram identity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import secrets
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import User, UserAccess, WebsitePurchaseSession, dbSession
from services.access import as_utc_naive, utc_now
from services.payment_domain import PaymentStatus
from services.payment_refund import RefundStatus
from services.prodamus import ProdamusCallbackResult, ProdamusOrder, ProdamusService
from services.telegram_oidc import (
    TelegramIdentity, TelegramOidcClient, TelegramOidcError, TelegramOidcFailure,
    make_pkce_verifier,
)


class WebsitePurchaseError(RuntimeError):
    """Controlled public-flow failure without account or session disclosure."""


class WebsiteLoginFailure(StrEnum):
    COOKIE_MISSING = "cookie_missing"
    SESSION_UNKNOWN = "session_unknown"
    SESSION_EXPIRED = "session_expired"
    STATE_MISMATCH = "state_mismatch"
    REPLAY = "callback_replayed"
    OIDC = "oidc_rejected"
    CLAIM_LOST = "session_claim_lost"


class WebsiteLoginError(WebsitePurchaseError):
    def __init__(self, reason: WebsiteLoginFailure, *, stage: str = "session_before_exchange",
                 oidc_reason: TelegramOidcFailure = TelegramOidcFailure.UNKNOWN):
        super().__init__("Telegram login was rejected.")
        self.reason = reason
        self.stage = stage
        self.oidc_reason = oidc_reason


@dataclass(frozen=True)
class ControlledCheckoutGate:
    """Creation-only lease for one authenticated identity; no bearer capability."""
    telegram_user_id: int | None = field(default=None, repr=False)
    expires_at: datetime | None = None

    def __post_init__(self):
        if self.telegram_user_id is None and self.expires_at is None:
            return  # Closed, including when the runtime setting is absent.
        if type(self.telegram_user_id) is not int or not 0 < self.telegram_user_id <= 2**63 - 1 or not isinstance(self.expires_at, datetime):
            raise ValueError("Controlled checkout configuration is invalid.")

    def allows(self, telegram_user_id: int, now: datetime) -> bool:
        return (self.telegram_user_id is not None and telegram_user_id == self.telegram_user_id
                and self.expires_at is not None and now < as_utc_naive(self.expires_at))


@dataclass(frozen=True)
class PublicCheckoutGate:
    """Explicit public creation policy; identity still comes only from OIDC."""

    def allows(self, telegram_user_id: int, now: datetime) -> bool:
        return type(telegram_user_id) is int and 0 < telegram_user_id <= 2**63 - 1


@dataclass(frozen=True)
class WebsiteLoginStart:
    session_token: str
    authorization_url: str


class WebsitePurchaseService:
    def __init__(
        self,
        oidc: TelegramOidcClient,
        prodamus: ProdamusService,
        *,
        session_factory: Callable[[], Session] = dbSession,
        now_factory: Callable[[], datetime] = utc_now,
        session_ttl: timedelta = timedelta(minutes=10),
        checkout_gate: ControlledCheckoutGate | PublicCheckoutGate | None = None,
    ) -> None:
        self._oidc = oidc
        self._prodamus = prodamus
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._session_ttl = session_ttl
        self._checkout_gate = checkout_gate if checkout_gate is not None else ControlledCheckoutGate()
        if not isinstance(self._checkout_gate, (ControlledCheckoutGate, PublicCheckoutGate)):
            raise ValueError("Checkout configuration is invalid.")

    def start_login(self) -> WebsiteLoginStart:
        token = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = make_pkce_verifier()
        now = self._now()
        with self._session_factory() as session:
            with session.begin():
                session.add(WebsitePurchaseSession(
                    session_token_hash=_digest(token),
                    oauth_state_hash=_digest(state),
                    oidc_nonce=nonce,
                    pkce_verifier=verifier,
                    expires_at=now + self._session_ttl,
                    created_at=now,
                    updated_at=now,
                ))
        return WebsiteLoginStart(token, self._oidc.authorization_url(state=state, nonce=nonce, verifier=verifier))

    def complete_login(self, session_token: str, state: str, code: str) -> TelegramIdentity:
        now = self._now()
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, now)
            self._check_login_binding(record, state)
            verifier, nonce = record.pkce_verifier, record.oidc_nonce
        try:
            identity = self._oidc.exchange_code(code=code, verifier=verifier, expected_nonce=nonce)
        except ValueError as error:
            reason = error.reason if isinstance(error, TelegramOidcError) else TelegramOidcFailure.UNKNOWN
            raise WebsiteLoginError(WebsiteLoginFailure.OIDC, stage="exchange_or_identity", oidc_reason=reason) from None
        now = self._now()  # Token exchange may outlive the purchase-session lease.
        with self._session_factory() as session:
            with session.begin():
                try:
                    record = self._get_live_session(session, session_token, now)
                    self._check_login_binding(record, state)
                except WebsiteLoginError as error:
                    raise WebsiteLoginError(error.reason, stage="session_after_exchange") from None
                claimed = session.execute(
                    update(WebsitePurchaseSession)
                    .where(
                        WebsitePurchaseSession.session_token_hash == _digest(session_token),
                        WebsitePurchaseSession.consumed_at.is_(None),
                        WebsitePurchaseSession.expires_at > now,
                    )
                    .values(telegram_user_id=identity.telegram_user_id, consumed_at=now, updated_at=now)
                )
                if claimed.rowcount != 1:
                    raise WebsiteLoginError(WebsiteLoginFailure.CLAIM_LOST, stage="session_claim")
                self._ensure_user_and_access(session, identity, now)
        return identity

    @staticmethod
    def _check_login_binding(record: WebsitePurchaseSession, state: str) -> None:
        if record.oauth_state_hash != _digest(state):
            raise WebsiteLoginError(WebsiteLoginFailure.STATE_MISMATCH)
        if record.consumed_at is not None:
            raise WebsiteLoginError(WebsiteLoginFailure.REPLAY)

    def create_order(self, session_token: str, email: str) -> ProdamusOrder:
        now = self._now()
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, now)
            if record.telegram_user_id is None:
                raise WebsitePurchaseError("Telegram login is required.")
            if not self._checkout_gate.allows(record.telegram_user_id, now):
                raise WebsitePurchaseError("Checkout is temporarily unavailable.")
            bound_payment_id = record.payment_id
            deadline = record.expires_at
            if isinstance(self._checkout_gate, ControlledCheckoutGate):
                deadline = min(deadline, as_utc_naive(self._checkout_gate.expires_at))
            user = session.scalar(select(User).where(User.tg_id == record.telegram_user_id))
            if user is None:
                raise WebsitePurchaseError("Telegram account is unavailable.")
            user_id = user.id
        try:
            order = self._prodamus.create_order(
                user_id, email,
                single_order=isinstance(self._checkout_gate, ControlledCheckoutGate),
                creation_deadline=deadline,
                expected_payment_id=bound_payment_id,
                public_checkout=isinstance(self._checkout_gate, PublicCheckoutGate),
            )
        except (LookupError, PermissionError, ValueError) as error:
            raise WebsitePurchaseError("Payment order cannot be created.") from error
        with self._session_factory() as session:
            with session.begin():
                record = self._get_live_session(session, session_token, self._now())
                if record.payment_id is not None and record.payment_id != order.payment_id:
                    raise WebsitePurchaseError("Purchase session already has an order.")
                record.payment_id = order.payment_id
                record.updated_at = self._now()
        return order

    def has_paid_access(self, session_token: str) -> bool:
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, self._now())
            user = session.scalar(select(User).where(User.tg_id == record.telegram_user_id))
            access = session.get(UserAccess, user.id) if user is not None else None
            return bool(access is not None and access.subscription_ends_at is not None
                        and access.subscription_ends_at > self._now())

    def checkout_available(self, session_token: str) -> bool:
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, self._now())
            return self._checkout_gate.allows(record.telegram_user_id, self._now())

    def is_authenticated(self, session_token: str) -> bool:
        try:
            with self._session_factory() as session:
                record = self._get_live_session(session, session_token, self._now())
                return record.telegram_user_id is not None
        except WebsitePurchaseError:
            return False

    def inspect_order_status(self, session_token: str) -> PaymentStatus | RefundStatus | None:
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, self._now())
            if record.telegram_user_id is None or record.payment_id is None:
                return None
            user = session.scalar(select(User).where(User.tg_id == record.telegram_user_id))
            if user is None:
                return None
            return self._prodamus.inspect_order_status(record.payment_id, user_id=user.id)

    def process_prodamus_notification(
        self, payload: dict[str, object], signature: str | None
    ) -> ProdamusCallbackResult:
        """Invoke only after the HTTP boundary has received the raw form."""
        return self._prodamus.process_notification(payload, signature)

    def _get_live_session(
        self, session: Session, token: str, now: datetime
    ) -> WebsitePurchaseSession:
        if not isinstance(token, str) or not token:
            raise WebsiteLoginError(WebsiteLoginFailure.COOKIE_MISSING)
        record = session.get(WebsitePurchaseSession, _digest(token))
        if record is None:
            raise WebsiteLoginError(WebsiteLoginFailure.SESSION_UNKNOWN)
        if record.expires_at <= now:
            raise WebsiteLoginError(WebsiteLoginFailure.SESSION_EXPIRED)
        return record

    @staticmethod
    def _ensure_user_and_access(
        session: Session, identity: TelegramIdentity, now: datetime
    ) -> None:
        user = session.scalar(select(User).where(User.tg_id == identity.telegram_user_id))
        if user is None:
            user = User(
                tg_id=identity.telegram_user_id,
                fullname=identity.fullname or "Telegram user",
                username=identity.username or "",
                inviter_id=0,
            )
            session.add(user)
            session.flush()
        if session.get(UserAccess, user.id) is None:
            session.add(UserAccess(user_id=user.id, updated_at=now))

    def _now(self) -> datetime:
        return as_utc_naive(self._now_factory())


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()
