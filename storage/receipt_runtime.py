"""Fail-closed configuration contract for future NPD receipt adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from storage.runtime_config import (
    ApplicationEnvironment,
    RuntimeConfigurationError,
    SettingsReader,
)


_PROVIDER_CODE = re.compile(r"^[a-z0-9_-]{1,64}$")


class ReceiptMode(StrEnum):
    DISABLED = "disabled"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ReceiptRuntimeConfig:
    mode: ReceiptMode
    provider_code: str | None


def build_receipt_runtime_config(
    settings: SettingsReader,
    *,
    environment: ApplicationEnvironment,
    provider_adapter_available: bool = False,
) -> ReceiptRuntimeConfig:
    raw_mode = settings.optional("RECEIPTS_MODE")
    if raw_mode is None:
        if environment is ApplicationEnvironment.PRODUCTION:
            raise RuntimeConfigurationError("Production receipt mode is required.")
        return ReceiptRuntimeConfig(ReceiptMode.DISABLED, None)
    try:
        mode = ReceiptMode(raw_mode)
    except ValueError:
        raise RuntimeConfigurationError("Receipt mode is invalid.") from None

    if environment is ApplicationEnvironment.PRODUCTION:
        if mode is not ReceiptMode.PRODUCTION:
            raise RuntimeConfigurationError("Production receipt delivery is required.")
    elif mode is ReceiptMode.PRODUCTION:
        raise RuntimeConfigurationError("Production receipts require production runtime.")

    if mode is ReceiptMode.DISABLED:
        return ReceiptRuntimeConfig(mode, None)
    provider_code = settings.optional("RECEIPT_PROVIDER")
    if provider_code is None or not _PROVIDER_CODE.fullmatch(provider_code):
        raise RuntimeConfigurationError("Receipt provider configuration is invalid.")
    if mode is ReceiptMode.PRODUCTION and not provider_adapter_available:
        raise RuntimeConfigurationError("Receipt provider adapter is not available.")
    return ReceiptRuntimeConfig(mode, provider_code)
