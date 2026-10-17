"""Telegram OIDC Authorization Code + PKCE validation for the purchase site."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import json
import secrets
import zlib
from hashlib import sha256
from typing import Callable, Mapping
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_AUTHORIZATION_URL = f"{TELEGRAM_OIDC_ISSUER}/auth"
TELEGRAM_OIDC_TOKEN_URL = f"{TELEGRAM_OIDC_ISSUER}/token"
TELEGRAM_OIDC_JWKS_URL = f"{TELEGRAM_OIDC_ISSUER}/.well-known/jwks.json"
_MAX_OIDC_RESPONSE_BYTES = 262144


class TelegramOidcFailure(StrEnum):
    UNKNOWN = "unclassified"
    CODE_INVALID = "code_invalid"
    TOKEN_HTTP = "token_http_rejected"
    TOKEN_TRANSPORT = "token_transport_failed"
    TOKEN_RESPONSE = "token_response_invalid"
    TOKEN_REJECTED = "token_provider_rejected"
    TOKEN_MISSING = "id_token_missing"
    TOKEN_FORMAT = "id_token_format_invalid"
    TOKEN_ALGORITHM = "id_token_algorithm_invalid"
    ISSUER = "issuer_mismatch"
    AUDIENCE = "audience_mismatch"
    NONCE = "nonce_mismatch"
    EXPIRY = "id_token_expired"
    JWKS = "jwks_unavailable"
    SIGNING_KEY = "signing_key_invalid"
    SIGNATURE = "signature_invalid"
    IDENTITY = "identity_invalid"
    SUBJECT = "identity_sub_invalid"
    PROFILE_ID_MISSING = "identity_id_missing"
    PROFILE_ID_STRING = "identity_id_decimal_string"
    PROFILE_ID_TYPE = "identity_id_type_invalid"
    PROFILE_ID_RANGE = "identity_id_out_of_range"


class TelegramOidcError(ValueError):
    """OIDC input is invalid without exposing a token or provider response."""

    def __init__(self, message: str, *, reason: TelegramOidcFailure = TelegramOidcFailure.UNKNOWN):
        super().__init__(message)
        self.reason = reason if isinstance(reason, TelegramOidcFailure) else TelegramOidcFailure.UNKNOWN


@dataclass(frozen=True)
class TelegramOidcConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    proxy_url: str | None = field(default=None, repr=False)

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
        if self.proxy_url is not None:
            proxy = urlparse(self.proxy_url)
            try:
                port = proxy.port
            except ValueError:
                raise TelegramOidcError("Telegram OIDC proxy is invalid.") from None
            if (
                proxy.scheme != "http"
                or proxy.hostname != "127.0.0.1"
                or port is None
                or not 1 <= port <= 65535
                or proxy.netloc != f"127.0.0.1:{port}"
                or proxy.path
                or proxy.params
                or proxy.query
                or proxy.fragment
                or any(character.isspace() or ord(character) < 32 for character in self.proxy_url)
            ):
                raise TelegramOidcError("Telegram OIDC proxy is invalid.")


class _TelegramOnlyProxy(ProxyHandler):
    """Use the explicit tunnel even when ambient NO_PROXY would bypass it."""

    def proxy_open(self, request, proxy, protocol):
        if request.full_url not in {TELEGRAM_OIDC_TOKEN_URL, TELEGRAM_OIDC_JWKS_URL}:
            raise TelegramOidcError("Telegram OIDC destination is invalid.")
        if protocol != "https" or request.type != "https" or request.host != "oauth.telegram.org":
            raise TelegramOidcError("Telegram OIDC destination is invalid.")
        request.set_proxy(urlparse(proxy).netloc, "http")
        return None


class _NoOidcRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


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
        self._opener = build_opener(
            _TelegramOnlyProxy({"https": config.proxy_url})
            if config.proxy_url is not None else ProxyHandler({}),
            _NoOidcRedirects(),
        )
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
            raise TelegramOidcError("Authorization code is invalid.", reason=TelegramOidcFailure.CODE_INVALID)
        response = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "client_id": self._config.client_id,
                "code_verifier": verifier,
            }
        )
        if isinstance(response, Mapping) and response.get("error"):
            # Provider error payloads are not log data, even with HTTP 200.
            raise TelegramOidcError("Telegram rejected the authorization code.", reason=TelegramOidcFailure.TOKEN_REJECTED)
        token = response.get("id_token") if isinstance(response, Mapping) else None
        if not isinstance(token, str):
            raise TelegramOidcError("Telegram ID token is missing.", reason=TelegramOidcFailure.TOKEN_MISSING)
        return self._validate_id_token(token, expected_nonce)

    def _validate_id_token(self, token: str, expected_nonce: str) -> TelegramIdentity:
        parts = token.split(".")
        if len(token) > 16384 or len(parts) != 3:
            raise TelegramOidcError("Telegram ID token is invalid.", reason=TelegramOidcFailure.TOKEN_FORMAT)
        header = _json_part(parts[0])
        claims = _json_part(parts[1])
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise TelegramOidcError("Telegram ID token is invalid.", reason=TelegramOidcFailure.TOKEN_ALGORITHM)
        if not isinstance(claims.get("iss"), str) or claims["iss"] != TELEGRAM_OIDC_ISSUER:
            raise TelegramOidcError("Telegram ID token issuer is invalid.", reason=TelegramOidcFailure.ISSUER)
        audience = claims.get("aud")
        valid_audience = audience == self._config.client_id or (
            isinstance(audience, list) and self._config.client_id in audience
        )
        if (
            not valid_audience
            or (isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self._config.client_id)
        ):
            raise TelegramOidcError("Telegram ID token binding is invalid.", reason=TelegramOidcFailure.AUDIENCE)
        if claims.get("nonce") != expected_nonce:
            raise TelegramOidcError("Telegram ID token binding is invalid.", reason=TelegramOidcFailure.NONCE)
        now = int(self._now_factory().timestamp())
        if type(claims.get("exp")) is not int or claims["exp"] <= now:
            raise TelegramOidcError("Telegram ID token has expired.", reason=TelegramOidcFailure.EXPIRY)
        self._verify_signature(parts, header["kid"])
        subject = claims.get("sub")
        telegram_user_id = claims.get("id")
        # OIDC subject is not the Telegram user ID. The requested profile scope
        # supplies the separately signed numeric `id` used by Bot API identities.
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or len(subject) > 255
        ):
            raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.SUBJECT)
        # Normalize only the separately signed profile id, after every JWT check.
        # Production Telegram can serialize this numeric identifier as a decimal
        # string. Accept that exact, lossless representation, never sub or float.
        if telegram_user_id is None:
            raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.PROFILE_ID_MISSING)
        if isinstance(telegram_user_id, str) and telegram_user_id.isascii() and telegram_user_id.isdecimal():
            if len(telegram_user_id) > 19:
                raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.PROFILE_ID_RANGE)
            if len(telegram_user_id) > 1 and telegram_user_id.startswith("0"):
                raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.PROFILE_ID_TYPE)
            telegram_user_id = int(telegram_user_id)
        if type(telegram_user_id) is not int:
            raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.PROFILE_ID_TYPE)
        if not 0 < telegram_user_id <= 2**63 - 1:
            raise TelegramOidcError("Telegram identity is invalid.", reason=TelegramOidcFailure.PROFILE_ID_RANGE)
        fullname = claims.get("name") if isinstance(claims.get("name"), str) else "Telegram user"
        username = claims.get("preferred_username")
        return TelegramIdentity(telegram_user_id, fullname[:255], username[:255] if isinstance(username, str) else "")

    def _verify_signature(self, parts: list[str], key_id: str) -> None:
        jwks = self._jwks_fetcher()
        keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
        if not isinstance(keys, list):
            raise TelegramOidcError("Telegram signing keys are unavailable.", reason=TelegramOidcFailure.JWKS)
        key = next((item for item in keys if isinstance(item, Mapping) and item.get("kid") == key_id), None)
        if key is None or key.get("kty") != "RSA" or not isinstance(key.get("n"), str) or not isinstance(key.get("e"), str):
            raise TelegramOidcError("Telegram signing key is invalid.", reason=TelegramOidcFailure.SIGNING_KEY)
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
        except Exception:
            raise TelegramOidcError("Telegram ID token signature is invalid.", reason=TelegramOidcFailure.SIGNATURE) from None

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
            with self._open_oidc(request) as response:
                value = _read_oidc_json(response)
        except HTTPError:
            raise TelegramOidcError("Telegram token exchange failed.", reason=TelegramOidcFailure.TOKEN_HTTP) from None
        except (ValueError, UnicodeError, zlib.error):
            raise TelegramOidcError("Telegram token response is invalid.", reason=TelegramOidcFailure.TOKEN_RESPONSE) from None
        except Exception:
            raise TelegramOidcError("Telegram token exchange failed.", reason=TelegramOidcFailure.TOKEN_TRANSPORT) from None
        if not isinstance(value, Mapping):
            raise TelegramOidcError("Telegram token response is invalid.", reason=TelegramOidcFailure.TOKEN_RESPONSE)
        return value

    def _fetch_jwks(self) -> Mapping[str, object]:
        try:
            with self._open_oidc(TELEGRAM_OIDC_JWKS_URL) as response:
                value = _read_oidc_json(response)
        except Exception:
            raise TelegramOidcError("Telegram signing keys are unavailable.", reason=TelegramOidcFailure.JWKS) from None
        if not isinstance(value, Mapping):
            raise TelegramOidcError("Telegram signing keys are invalid.", reason=TelegramOidcFailure.SIGNING_KEY)
        return value

    def _open_oidc(self, request):
        url = request.full_url if isinstance(request, Request) else request
        if url not in {TELEGRAM_OIDC_TOKEN_URL, TELEGRAM_OIDC_JWKS_URL}:
            raise TelegramOidcError("Telegram OIDC destination is invalid.")
        return self._opener.open(request, timeout=10)


def _read_oidc_json(response):
    """Telegram may return gzip even when the client requested identity."""
    data = response.read(_MAX_OIDC_RESPONSE_BYTES + 1)
    if len(data) > _MAX_OIDC_RESPONSE_BYTES:
        raise TelegramOidcError("Telegram OIDC response is too large.")
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(data, _MAX_OIDC_RESPONSE_BYTES + 1)
        if len(data) > _MAX_OIDC_RESPONSE_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise TelegramOidcError("Telegram OIDC compressed response is invalid.")
    elif encoding not in {"", "identity"}:
        raise TelegramOidcError("Telegram OIDC response encoding is invalid.")
    return json.loads(data.decode("utf-8"))


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_part(value: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(_b64url(value).decode())
    except Exception:
        raise TelegramOidcError("Telegram ID token is invalid.", reason=TelegramOidcFailure.TOKEN_FORMAT) from None
    if not isinstance(decoded, Mapping):
        raise TelegramOidcError("Telegram ID token is invalid.", reason=TelegramOidcFailure.TOKEN_FORMAT)
    return decoded
