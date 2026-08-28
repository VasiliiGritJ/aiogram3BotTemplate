"""Minimal Telegram UI for the server-owned monthly subscription product."""

from datetime import datetime
from decimal import Decimal

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import subscription_mkp, to_menu_mpk
from services.access import (
    AccessDecision,
    AccessStatus,
    as_utc_naive,
    get_access_decision,
    utc_now,
)
from services.payment_domain import PaymentStatus
from services.payment_provider import PaymentProviderError
from services.payment_service import PaymentServiceReason, PaymentServiceResult
from storage.config import dp
from storage.payment_runtime import (
    PaymentRuntime,
    PaymentRuntimeConfigurationError,
    get_payment_runtime,
)
from storage.states import tryFinish


SUBSCRIPTION_CALLBACK = "subscription"
SUBSCRIPTION_PAY_CALLBACK = "subscription:pay"
SUBSCRIPTION_CHECK_PREFIX = "subscription:check:"


def _format_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value is not None else "—"


def _format_price(amount_minor: int, currency: str) -> str:
    amount = Decimal(amount_minor) / Decimal("100")
    if currency == "RUB":
        return f"{amount:.2f}".rstrip("0").rstrip(".") + " ₽"
    return f"{amount:.2f} {currency}"


def format_subscription_screen(
    runtime: PaymentRuntime,
    decision: AccessDecision,
    payment: PaymentServiceResult | None,
) -> str:
    lines = ["💳 Подписка", ""]
    if decision.status is AccessStatus.TRIAL_AVAILABLE:
        lines.append("Доступ: пробный период ещё не начат.")
    elif decision.status is AccessStatus.TRIAL:
        lines.append(f"Доступ: пробный период до {_format_date(decision.ends_at)}.")
    elif decision.status is AccessStatus.ACTIVE:
        lines.append(f"Доступ: подписка активна до {_format_date(decision.ends_at)}.")
    else:
        lines.append("Доступ: активной подписки нет.")
    lines.extend(
        (
            "",
            f"Подписка: {runtime.product.period_days} дней",
            f"Стоимость: {_format_price(runtime.product.amount_minor, runtime.product.currency)}",
        )
    )
    if _is_paid_reserved(payment):
        lines.extend(
            (
                "",
                "Подписка оплачена.",
                "Платный период: "
                f"{_format_date(payment.grant_started_at)} — "
                f"{_format_date(payment.grant_ends_at)}.",
            )
        )
    elif payment is not None and not _is_paid_active(decision):
        lines.extend(("", _payment_status_text(payment)))
    return "\n".join(lines)


def _payment_status_text(payment: PaymentServiceResult) -> str:
    messages = {
        PaymentStatus.CREATING: "Платёж создаётся. Попробуйте проверить оплату позже.",
        PaymentStatus.PENDING: "Оплата ожидает подтверждения.",
        PaymentStatus.WAITING_FOR_CAPTURE: "Платёж обрабатывается.",
        PaymentStatus.SUCCEEDED: (
            f"Подписка активирована до {_format_date(payment.grant_ends_at)}."
            if payment.grant_ends_at is not None
            else "Оплата подтверждена. Подписка активирована."
        ),
        PaymentStatus.CANCELED: "Платёж отменён.",
        PaymentStatus.EXPIRED: "Срок оплаты истёк.",
        PaymentStatus.FAILED: "Не удалось обработать платёж.",
    }
    return messages[payment.status]


def _safe_payment_id(data: str | None) -> int | None:
    if not isinstance(data, str) or not data.startswith(SUBSCRIPTION_CHECK_PREFIX):
        return None
    value = data.removeprefix(SUBSCRIPTION_CHECK_PREFIX)
    if not value.isdecimal():
        return None
    payment_id = int(value)
    return payment_id if payment_id > 0 else None


def _is_paid_active(decision: AccessDecision) -> bool:
    return decision.status is AccessStatus.ACTIVE


def _is_paid_reserved(payment: PaymentServiceResult | None) -> bool:
    """Return whether a confirmed paid period is queued after the current trial."""
    if (
        payment is None
        or payment.status is not PaymentStatus.SUCCEEDED
        or payment.grant_started_at is None
        or payment.grant_ends_at is None
    ):
        return False
    now = utc_now()
    return (
        now < as_utc_naive(payment.grant_started_at)
        and now < as_utc_naive(payment.grant_ends_at)
    )


def _payment_markup(
    payment: PaymentServiceResult | None,
    decision: AccessDecision,
):
    paid_active = _is_paid_active(decision)
    payment_already_committed = paid_active or _is_paid_reserved(payment)
    if payment is None:
        return subscription_mkp(show_pay=not payment_already_committed)
    is_active_payment = payment.status in {
        PaymentStatus.CREATING,
        PaymentStatus.PENDING,
        PaymentStatus.WAITING_FOR_CAPTURE,
    }
    return subscription_mkp(
        payment_id=payment.payment_id if is_active_payment else None,
        confirmation_url=payment.confirmation_url if is_active_payment else None,
        show_pay=not payment_already_committed,
    )


async def _load_runtime_or_show_error(message: types.Message) -> PaymentRuntime | None:
    try:
        return get_payment_runtime()
    except PaymentRuntimeConfigurationError:
        await message.edit_text(
            "Подписка временно недоступна. Попробуйте позже.",
            reply_markup=to_menu_mpk(),
        )
        return None


@dp.callback_query(F.data == SUBSCRIPTION_CALLBACK)
async def subscription_screen(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.", reply_markup=to_menu_mpk())
    else:
        runtime = await _load_runtime_or_show_error(call.message)
        if runtime is not None:
            payment = runtime.payment_service.get_latest_payment(user.id)
            decision = get_access_decision(user.id)
            await call.message.edit_text(
                format_subscription_screen(runtime, decision, payment),
                reply_markup=_payment_markup(payment, decision),
            )
    await call.answer()


@dp.callback_query(F.data == SUBSCRIPTION_PAY_CALLBACK)
async def subscription_pay(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.", reply_markup=to_menu_mpk())
        await call.answer()
        return
    runtime = await _load_runtime_or_show_error(call.message)
    if runtime is None:
        await call.answer()
        return
    payment = runtime.payment_service.get_latest_payment(user.id)
    decision = get_access_decision(user.id)
    if _is_paid_active(decision) or _is_paid_reserved(payment):
        await call.message.edit_text(
            format_subscription_screen(runtime, decision, payment),
            reply_markup=_payment_markup(payment, decision),
        )
        await call.answer()
        return
    try:
        payment = runtime.payment_service.get_or_create_payment(user.id)
    except (PaymentProviderError, SQLAlchemyError, RuntimeError, ValueError):
        await call.message.edit_text(
            "Не удалось подготовить оплату. Попробуйте позже.",
            reply_markup=subscription_mkp(),
        )
        await call.answer()
        return
    decision = get_access_decision(user.id)
    if payment.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE:
        message = "Не удалось подготовить оплату. Попробуйте позже."
    else:
        message = format_subscription_screen(runtime, decision, payment)
    await call.message.edit_text(
        message,
        reply_markup=_payment_markup(payment, decision),
    )
    await call.answer()


@dp.callback_query(F.data.startswith(SUBSCRIPTION_CHECK_PREFIX))
async def subscription_check(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    payment_id = _safe_payment_id(call.data)
    user = User.get(tg_id=call.from_user.id)
    if user is None or payment_id is None:
        await call.answer("Платёж не найден.", show_alert=True)
        return
    runtime = await _load_runtime_or_show_error(call.message)
    if runtime is None:
        await call.answer()
        return
    try:
        payment = runtime.payment_service.reconcile_payment(payment_id, user_id=user.id)
    except (LookupError, PermissionError, ValueError):
        await call.answer("Платёж не найден.", show_alert=True)
        return
    except (PaymentProviderError, SQLAlchemyError, RuntimeError):
        await call.message.edit_text(
            "Сейчас не удалось проверить оплату. Попробуйте позже.",
            reply_markup=subscription_mkp(payment_id=payment_id),
        )
        await call.answer()
        return
    decision = get_access_decision(user.id)
    if payment.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE:
        message = "Сейчас не удалось проверить оплату. Попробуйте позже."
    else:
        message = format_subscription_screen(runtime, decision, payment)
    await call.message.edit_text(
        message,
        reply_markup=_payment_markup(payment, decision),
    )
    await call.answer()
