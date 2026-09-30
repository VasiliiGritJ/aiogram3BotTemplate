"""Telegram OIDC Authorization Code + PKCE validation for the purchase site."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets
from hashlib import sha256
from typing import Callable, Mapping
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_AUTHORIZATION_URL = f"{TELEGRAM_OIDC_ISSUER}/auth"
TELEGRAM_OIDC_TOKEN_URL = f"{TELEGRAM_OIDC_ISSUER}/token"
TELEGRAM_OIDC_JWKS_URL = f"{TELEGRAM_OIDC_ISSUER}/.well-known/jwks.json"


class TelegramOidcError(ValueError):
    """OIDC input is invalid without exposing a token or provider response."""


@dataclass(frozen=True)
class TelegramOidcConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.client_secret.strip():
            raise TelegramOidcError("Telegram OIDC credentials are missing.")
        parsed = urlparse(self.redirect_uri)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise TelegramOidcError("Telegram OIDC redirect URI is invalid.")


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_user_id: int
    fullname: str
    username: str


def make_pkce_verifier() -> str:
    return secrets.token_urlsafe(48)


def pkce_challenge(verifier: str) -> str:
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        raise TelegramOidcError("PKCE verifier is invalid.")
    return base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class TelegramOidcClient:
    """Small synchronous client; callers run it outside aiohttp's event loop."""

    def __init__(
        self,
        config: TelegramOidcConfig,
        *,
        token_request: Callable[[Mapping[str, str]], Mapping[str, object]] | None = None,
        jwks_fetcher: Callable[[], Mapping[str, object]] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._token_request = token_request or self._request_tokens
        self._jwks_fetcher = jwks_fetcher or self._fetch_jwks
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))

    def authorization_url(self, *, state: str, nonce: str, verifier: str) -> str:
        if not state or not nonce:
            raise TelegramOidcError("OIDC state is invalid.")
        fields = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{TELEGRAM_OIDC_AUTHORIZATION_URL}?{urlencode(fields)}"

    def exchange_code(
        self, *, code: str, verifier: str, expected_nonce: str
    ) -> TelegramIdentity:
        if not isinstance(code, str) or not code or len(code) > 2048:
            raise TelegramOidcError("Authorization code is invalid.")
        response = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "client_id": self._config.client_id,
                "code_verifier": verifier,
            }
        )
        token = response.get("id_token") if isinstance(response, Mapping) else None
        if not isinstance(token, str):
            raise TelegramOidcError("Telegram ID token is missing.")
        return self._validate_id_token(token, expected_nonce)

    def _validate_id_token(self, token: str, expected_nonce: str) -> TelegramIdentity:
        parts = token.split(".")
        if len(parts) != 3:
            raise TelegramOidcError("Telegram ID token is invalid.")
        header = _json_part(parts[0])
        claims = _json_part(parts[1])
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise TelegramOidcError("Telegram ID token is invalid.")
        if not isinstance(claims.get("iss"), str) or claims["iss"] != TELEGRAM_OIDC_ISSUER:
            raise TelegramOidcError("Telegram ID token issuer is invalid.")
        audience = claims.get("aud")
        valid_audience = audience == self._config.client_id or (
            isinstance(audience, list) and self._config.client_id in audience
        )
        if not valid_audience or claims.get("nonce") != expected_nonce:
            raise TelegramOidcError("Telegram ID token binding is invalid.")
        now = int(self._now_factory().timestamp())
        if not isinstance(claims.get("exp"), int) or claims["exp"] < now:
            raise TelegramOidcError("Telegram ID token has expired.")
        self._verify_signature(parts, header["kid"])
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.isdecimal() or int(subject) <= 0:
            raise TelegramOidcError("Telegram identity is invalid.")
        fullname = claims.get("name") if isinstance(claims.get("name"), str) else "Telegram user"
        username = claims.get("preferred_username")
        return TelegramIdentity(int(subject), fullname[:255], username[:255] if isinstance(username, str) else "")

    def _verify_signature(self, parts: list[str], key_id: str) -> None:
        jwks = self._jwks_fetcher()
        keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
        if not isinstance(keys, list):
            raise TelegramOidcError("Telegram signing keys are unavailable.")
        key = next((item for item in keys if isinstance(item, Mapping) and item.get("kid") == key_id), None)
        if key is None or key.get("kty") != "RSA" or not isinstance(key.get("n"), str) or not isinstance(key.get("e"), str):
            raise TelegramOidcError("Telegram signing key is invalid.")
        try:
            public_key = rsa.RSAPublicNumbers(
                int.from_bytes(_b64url(key["e"]), "big"),
                int.from_bytes(_b64url(key["n"]), "big"),
            ).public_key()
            public_key.verify(
                _b64url(parts[2]),
                f"{parts[0]}.{parts[1]}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as error:
            raise TelegramOidcError("Telegram ID token signature is invalid.") from error

    def _request_tokens(self, form: Mapping[str, str]) -> Mapping[str, object]:
        credentials = base64.b64encode(
            f"{self._config.client_id}:{self._config.client_secret}".encode()
        ).decode()
        request = Request(
            TELEGRAM_OIDC_TOKEN_URL,
            data=urlencode(form).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                value = json.loads(response.read().decode())
        except Exception as error:
            raise TelegramOidcError("Telegram token exchange failed.") from error
        if not isinstance(value, Mapping):
            raise TelegramOidcError("Telegram token response is invalid.")
        return value

    @staticmethod
    def _fetch_jwks() -> Mapping[str, object]:
        try:
            with urlopen(TELEGRAM_OIDC_JWKS_URL, timeout=10) as response:
                value = json.loads(response.read().decode())
        except Exception as error:
            raise TelegramOidcError("Telegram signing keys are unavailable.") from error
        if not isinstance(value, Mapping):
            raise TelegramOidcError("Telegram signing keys are invalid.")
        return value


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_part(value: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(_b64url(value).decode())
    except Exception as error:
        raise TelegramOidcError("Telegram ID token is invalid.") from error
    if not isinstance(decoded, Mapping):
        raise TelegramOidcError("Telegram ID token is invalid.")
    return decoded
