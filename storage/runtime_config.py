"""Explicit, role-specific configuration loading without import-time I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from environs import Env


LOCAL_ENV_FILE = Path("storage/.env")


class RuntimeConfigurationError(RuntimeError):
    """Raised without echoing configuration values or secrets."""


class ApplicationEnvironment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class SettingsReader:
    _required: Callable[[str], str]
    _optional: Callable[[str], str | None]

    def __call__(self, name: str) -> str:
        try:
            value = self._required(name)
        except Exception:
            raise RuntimeConfigurationError("Required runtime configuration is missing.") from None
        if not isinstance(value, str) or not value.strip():
            raise RuntimeConfigurationError("Required runtime configuration is missing.")
        return value.strip()

    def optional(self, name: str) -> str | None:
        try:
            value = self._optional(name)
        except Exception:
            return None
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()


@dataclass(frozen=True)
class BotRoleConfig:
    environment: ApplicationEnvironment
    bot_token: str = field(repr=False)
    database_path: Path
    telegram_proxy_url: str | None
    admin_ids: tuple[int, ...]
    systemd_service_name: str
    debug_mode: bool


@dataclass(frozen=True)
class MaintenanceRoleConfig:
    database_path: Path


@dataclass(frozen=True)
class WebhookRoleConfig:
    environment: ApplicationEnvironment
    database_path: Path
    bind_host: str
    bind_port: int
    path: str
    public_url: str | None


def local_development_settings(
    env_file: str | Path = LOCAL_ENV_FILE,
) -> SettingsReader:
    """Explicit local-only loader used by the default developer entrypoint."""
    env = Env()
    env.read_env(str(env_file))
    return SettingsReader(env, lambda name: env.str(name, default=None))


def process_environment_settings() -> SettingsReader:
    """Production loader: process environment only, never a dotenv file."""
    env = Env()
    return SettingsReader(env, lambda name: env.str(name, default=None))


def build_bot_role_config(
    settings: SettingsReader,
    *,
    expected_environment: ApplicationEnvironment,
) -> BotRoleConfig:
    environment = _application_environment(settings, expected_environment)

    database_path = _database_path(settings, production=environment is ApplicationEnvironment.PRODUCTION)
    return BotRoleConfig(
        environment=environment,
        bot_token=settings("BOT_TOKEN"),
        database_path=database_path,
        telegram_proxy_url=settings.optional("TELEGRAM_PROXY_URL"),
        admin_ids=_admin_ids(settings.optional("ADMIN_IDS")),
        systemd_service_name=settings.optional("SYSTEMD_SERVICE_NAME") or "",
        debug_mode=_boolean(settings.optional("DEBUG_MODE"), default=False),
    )


def build_maintenance_role_config(settings: SettingsReader) -> MaintenanceRoleConfig:
    """Maintenance only needs the DB path, never Telegram/payment secrets."""
    return MaintenanceRoleConfig(database_path=_database_path(settings, production=False))


def build_webhook_role_config(
    settings: SettingsReader,
    *,
    expected_environment: ApplicationEnvironment,
) -> WebhookRoleConfig:
    environment = _application_environment(settings, expected_environment)
    production = environment is ApplicationEnvironment.PRODUCTION
    bind_host = settings.optional("PAYMENT_WEBHOOK_BIND_HOST")
    bind_port = settings.optional("PAYMENT_WEBHOOK_BIND_PORT")
    path = settings.optional("PAYMENT_WEBHOOK_PATH")
    public_url = settings.optional("PAYMENT_WEBHOOK_PUBLIC_URL")
    if production and (
        bind_host is None
        or bind_port is None
        or path is None
        or public_url is None
    ):
        raise RuntimeConfigurationError("Production webhook configuration is incomplete.")
    bind_host = bind_host or "127.0.0.1"
    bind_port = bind_port or "8080"
    path = path or "/webhooks/yookassa/payment"
    try:
        port = int(bind_port)
    except ValueError:
        raise RuntimeConfigurationError("Webhook port is invalid.") from None
    if not 1 <= port <= 65535:
        raise RuntimeConfigurationError("Webhook port is invalid.")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise RuntimeConfigurationError("Webhook path is invalid.")
    if public_url is not None:
        parsed_public_url = urlparse(public_url)
        if (
            parsed_public_url.scheme != "https"
            or not parsed_public_url.netloc
            or parsed_public_url.path != path
            or parsed_public_url.params
            or parsed_public_url.query
            or parsed_public_url.fragment
            or parsed_public_url.username is not None
            or parsed_public_url.password is not None
        ):
            raise RuntimeConfigurationError("Webhook public URL is invalid.")
    return WebhookRoleConfig(
        environment=environment,
        database_path=_database_path(settings, production=production),
        bind_host=bind_host,
        bind_port=port,
        path=path,
        public_url=public_url,
    )


def _database_path(settings: SettingsReader, *, production: bool) -> Path:
    value = settings.optional("DATABASE_PATH")
    if value is None:
        if production:
            raise RuntimeConfigurationError("Production database path is required.")
        value = "db.db"
    path = Path(value).expanduser()
    if production and not path.is_absolute():
        raise RuntimeConfigurationError("Production database path must be absolute.")
    return path.resolve()


def _application_environment(
    settings: SettingsReader,
    expected_environment: ApplicationEnvironment,
) -> ApplicationEnvironment:
    raw_environment = settings.optional("APP_ENV")
    if raw_environment is None:
        if expected_environment is ApplicationEnvironment.PRODUCTION:
            raise RuntimeConfigurationError("Production environment must be explicit.")
        return ApplicationEnvironment.DEVELOPMENT
    try:
        environment = ApplicationEnvironment(raw_environment)
    except ValueError:
        raise RuntimeConfigurationError("Application environment is invalid.") from None
    if environment is not expected_environment:
        raise RuntimeConfigurationError("Application environment does not match the bootstrap role.")
    return environment


def _admin_ids(value: str | None) -> tuple[int, ...]:
    if value is None:
        return ()
    result: list[int] = []
    for item in value.split(","):
        candidate = item.strip()
        if not candidate.isdecimal() or int(candidate) <= 0:
            raise RuntimeConfigurationError("Admin configuration is invalid.")
        result.append(int(candidate))
    return tuple(dict.fromkeys(result))


def _boolean(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise RuntimeConfigurationError("Boolean runtime configuration is invalid.")
