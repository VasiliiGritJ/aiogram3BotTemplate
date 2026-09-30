"""Server-side website purchase session bound to a validated Telegram identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import secrets
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import User, UserAccess, WebsitePurchaseSession, dbSession
from services.access import as_utc_naive, utc_now
from services.prodamus import ProdamusCallbackResult, ProdamusOrder, ProdamusService
from services.telegram_oidc import TelegramIdentity, TelegramOidcClient, make_pkce_verifier


class WebsitePurchaseError(RuntimeError):
    """Controlled public-flow failure without account or session disclosure."""


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
    ) -> None:
        self._oidc = oidc
        self._prodamus = prodamus
        self._session_factory = session_factory
        self._now_factory = now_factory
        self._session_ttl = session_ttl

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
            if record.oauth_state_hash != _digest(state) or record.consumed_at is not None:
                raise WebsitePurchaseError("Telegram login was rejected.")
            verifier, nonce = record.pkce_verifier, record.oidc_nonce
        try:
            identity = self._oidc.exchange_code(code=code, verifier=verifier, expected_nonce=nonce)
        except ValueError as error:
            raise WebsitePurchaseError("Telegram login was rejected.") from error
        with self._session_factory() as session:
            with session.begin():
                record = self._get_live_session(session, session_token, now)
                if record.oauth_state_hash != _digest(state) or record.consumed_at is not None:
                    raise WebsitePurchaseError("Telegram login was rejected.")
                record.telegram_user_id = identity.telegram_user_id
                record.consumed_at = now
                record.updated_at = now
                self._ensure_user_and_access(session, identity, now)
        return identity

    def create_order(self, session_token: str, email: str) -> ProdamusOrder:
        now = self._now()
        with self._session_factory() as session:
            record = self._get_live_session(session, session_token, now)
            if record.telegram_user_id is None:
                raise WebsitePurchaseError("Telegram login is required.")
            user = session.scalar(select(User).where(User.tg_id == record.telegram_user_id))
            if user is None:
                raise WebsitePurchaseError("Telegram account is unavailable.")
            user_id = user.id
        try:
            order = self._prodamus.create_order(user_id, email)
        except (LookupError, PermissionError, ValueError) as error:
            raise WebsitePurchaseError("Payment order cannot be created.") from error
        with self._session_factory() as session:
            with session.begin():
                record = self._get_live_session(session, session_token, now)
                record.payment_id = order.payment_id
                record.updated_at = now
        return order

    def is_authenticated(self, session_token: str) -> bool:
        try:
            with self._session_factory() as session:
                record = self._get_live_session(session, session_token, self._now())
                return record.telegram_user_id is not None
        except WebsitePurchaseError:
            return False

    def process_prodamus_notification(
        self, payload: dict[str, object], signature: str | None
    ) -> ProdamusCallbackResult:
        """Invoke only after the HTTP boundary has received the raw form."""
        return self._prodamus.process_notification(payload, signature)

    def _get_live_session(
        self, session: Session, token: str, now: datetime
    ) -> WebsitePurchaseSession:
        if not isinstance(token, str) or not token:
            raise WebsitePurchaseError("Website session is invalid.")
        record = session.get(WebsitePurchaseSession, _digest(token))
        if record is None or record.expires_at <= now:
            raise WebsitePurchaseError("Website session has expired.")
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
