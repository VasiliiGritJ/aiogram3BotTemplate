"""Fail-closed composition for the future PayAnyWay production role."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from services.payanyway import PayAnyWayConfig, PayAnyWayService
from storage.payment_runtime import (
    PaymentMode,
    PaymentRuntimeConfigurationError,
    read_payment_mode,
)


@dataclass(frozen=True)
class PayAnyWayRuntime:
    service: PayAnyWayService
    mode: PaymentMode


def build_payanyway_runtime(
    read_required: Callable[[str], str],
) -> PayAnyWayRuntime:
    """Require every merchant value explicitly; credentials never select mode."""
    mode = read_payment_mode(read_required)
    try:
        config = PayAnyWayConfig(
            merchant_id=_read_required(read_required, "PAYANYWAY_MNT_ID"),
            integrity_code=_read_required(
                read_required, "PAYANYWAY_INTEGRITY_CODE"
            ),
            test_mode=mode is PaymentMode.TEST,
            success_url=_read_required(read_required, "PAYANYWAY_SUCCESS_URL"),
            fail_url=_read_required(read_required, "PAYANYWAY_FAIL_URL"),
            return_url=_read_required(read_required, "PAYANYWAY_RETURN_URL"),
        )
    except (ValueError, TypeError):
        raise PaymentRuntimeConfigurationError(
            "PayAnyWay configuration is invalid."
        ) from None
    return PayAnyWayRuntime(PayAnyWayService(config), mode)


def _read_required(read_required: Callable[[str], str], name: str) -> str:
    try:
        value = read_required(name)
    except Exception:
        raise PaymentRuntimeConfigurationError(
            "PayAnyWay configuration is incomplete."
        ) from None
    if not isinstance(value, str) or not value.strip():
        raise PaymentRuntimeConfigurationError(
            "PayAnyWay configuration is incomplete."
        )
    return value.strip()
