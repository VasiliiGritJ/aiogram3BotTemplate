"""Minimal Telegram UI for the server-owned monthly subscription product."""

from datetime import datetime
from decimal import Decimal

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import (
    receipt_email_mpk,
    subscription_info_mpk,
    subscription_mkp,
    to_menu_mpk,
)
from services.access import (
    AccessDecision,
    AccessEntitlement,
    AccessStatus,
    TrialActivationError,
    activate_trial_once,
    as_utc_naive,
    get_access_decision,
    utc_now,
)
from services.payment_domain import PaymentStatus
from services.payment_provider import PaymentProviderError
from services.payment_service import PaymentServiceReason, PaymentServiceResult
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from services.receipt_email import (
    RECEIPT_EMAIL_ERROR_MESSAGE,
    ReceiptEmailProfileUnavailable,
    ReceiptEmailRequired,
    ReceiptEmailValidationError,
    get_receipt_email,
    save_receipt_email,
)
from storage.config import dp
from storage.payment_runtime import (
    PaymentRuntime,
    PaymentRuntimeConfigurationError,
    get_payment_runtime,
)
from storage.states import ReceiptEmail, tryFinish


SUBSCRIPTION_CALLBACK = "subscription"
SUBSCRIPTION_PAY_CALLBACK = "subscription:pay"
SUBSCRIPTION_TRIAL_CALLBACK = "subscription:trial"
SUBSCRIPTION_CHECK_PREFIX = "subscription:check:"
SUBSCRIPTION_SUPPORT_CALLBACK = "subscription:support"
SUBSCRIPTION_DOCUMENTS_CALLBACK = "subscription:documents"
RECEIPT_EMAIL_PROMPT = (
    "📧 Куда отправлять чеки?\n\n"
    "Укажите email. Мы сохраним его в профиле и будем использовать для отправки чеков после оплаты."
)


def _format_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value is not None else "—"


def _format_price(amount_minor: int, currency: str) -> str:
    amount = Decimal(amount_minor) / Decimal("100")
    if currency == "RUB":
        return f"{amount:.2f}".rstrip("0").rstrip(".") + " ₽"
    return f"{amount:.2f} {currency}"


def format_subscription_screen(
    runtime: PaymentRuntime | None,
    decision: AccessDecision,
    payment: PaymentServiceResult | None,
    receipt_email: str | None = None,
) -> str:
    product = LAUNCH_COMMERCIAL_PRODUCT
    lines = ["💳 Подписка", ""]
    if decision.status is AccessStatus.TRIAL_AVAILABLE:
        lines.extend((
            f"Попробуйте бесплатно — {product.trial_days} дня",
            "Без карты и автопродления",
        ))
    elif decision.status is AccessStatus.TRIAL:
        lines.append(
            f"Доступ: пробный период до {_format_date(decision.ends_at)}"
        )
    elif decision.status is AccessStatus.ACTIVE:
        lines.append(f"Доступ: подписка активна до {_format_date(decision.ends_at)}")
    elif decision.entitlement is AccessEntitlement.EXPIRED_TRIAL:
        lines.extend((
            f"Пробные {product.trial_days} дня закончились",
            f"Продолжить — {_format_price(product.amount_minor, product.currency)} "
            f"на {product.period_days} дней",
        ))
    else:
        lines.append("Доступ: активной подписки нет.")
    lines.extend(
        (
            "",
            f"Стоимость: {_format_price(product.amount_minor, product.currency)}",
            f"Период: {product.period_days} дней",
            "Без автопродления",
            "Пробный период не превращается в платную подписку автоматически",
        )
    )
    if receipt_email is not None:
        lines.extend(("", f"Чек отправим на: {receipt_email}"))
    if runtime is not None and runtime.website_first:
        lines.extend(("", "Платный доступ оформляется на сайте TRAINER.OS"))
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
    runtime: PaymentRuntime | None = None,
):
    commercial = runtime.commercial_public if runtime is not None else None
    link_kwargs = {
        "public_site_url": None if commercial is None else commercial.public_site_url,
        "terms_url": None if commercial is None else commercial.terms_url,
        "privacy_url": None if commercial is None else commercial.privacy_url,
        "refund_url": None if commercial is None else commercial.refund_url,
        "show_support": commercial is not None and commercial.support_email is not None,
    }
    paid_active = _is_paid_active(decision)
    payment_already_committed = paid_active or _is_paid_reserved(payment)
    show_trial = (
        decision.status is AccessStatus.TRIAL_AVAILABLE
        and not payment_already_committed
    )
    show_product_entry = decision.has_access
    website_first = runtime is not None and runtime.website_first
    if payment is None:
        return subscription_mkp(
            show_pay=not website_first and not payment_already_committed,
            show_trial=show_trial,
            show_product_entry=show_product_entry,
            **link_kwargs,
        )
    is_active_payment = payment.status in {
        PaymentStatus.CREATING,
        PaymentStatus.PENDING,
        PaymentStatus.WAITING_FOR_CAPTURE,
    }
    return subscription_mkp(
        payment_id=payment.payment_id if is_active_payment else None,
        confirmation_url=(
            payment.confirmation_url
            if is_active_payment and not website_first
            else None
        ),
        show_pay=(
            not website_first
            and not payment_already_committed
            and not (is_active_payment and payment.confirmation_url)
        ),
        show_trial=show_trial,
        show_product_entry=show_product_entry,
        **link_kwargs,
    )


async def _load_runtime_or_show_error(
    message: types.Message,
    *,
    reply_to_user: bool = False,
) -> PaymentRuntime | None:
    try:
        return get_payment_runtime()
    except PaymentRuntimeConfigurationError:
        text = "Подписка временно недоступна. Попробуйте позже."
        if reply_to_user:
            await message.answer(text, reply_markup=to_menu_mpk())
        else:
            await message.edit_text(text, reply_markup=to_menu_mpk())
        return None


async def _create_payment_after_receipt_email(
    message: types.Message,
    user_id: int,
    runtime: PaymentRuntime,
    receipt_email: str,
) -> None:
    """Create/reuse through the service only after the canonical email is stored."""
    try:
        payment = runtime.payment_service.get_or_create_payment(user_id)
    except ReceiptEmailRequired:
        await message.answer(
            RECEIPT_EMAIL_PROMPT,
            reply_markup=receipt_email_mpk(back_callback=SUBSCRIPTION_CALLBACK),
        )
        return
    except (PaymentProviderError, SQLAlchemyError, RuntimeError, ValueError):
        await message.answer(
            "Не удалось подготовить оплату. Попробуйте позже.",
            reply_markup=_payment_markup(
                None,
                AccessDecision(AccessStatus.EXPIRED, None),
                runtime,
            ),
        )
        return
    decision = get_access_decision(user_id)
    message_text = (
        "Не удалось подготовить оплату. Попробуйте позже."
        if payment.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE
        else format_subscription_screen(runtime, decision, payment, receipt_email)
    )
    await message.answer(message_text, reply_markup=_payment_markup(payment, decision, runtime))


@dp.callback_query(F.data == SUBSCRIPTION_CALLBACK)
async def subscription_screen(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.", reply_markup=to_menu_mpk())
    else:
        try:
            runtime = get_payment_runtime()
        except PaymentRuntimeConfigurationError:
            runtime = None
        payment = (
            None
            if runtime is None
            else runtime.payment_service.get_latest_payment(user.id)
        )
        decision = get_access_decision(user.id)
        receipt_email = get_receipt_email(user.id)
        await call.message.edit_text(
            format_subscription_screen(runtime, decision, payment, receipt_email),
            reply_markup=_payment_markup(payment, decision, runtime),
        )
    await call.answer()


@dp.callback_query(F.data == SUBSCRIPTION_TRIAL_CALLBACK)
async def subscription_trial(call: types.CallbackQuery, state: FSMContext) -> None:
    """Start the lifetime trial only after this explicit, server-owned action."""
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text(
            "Отправьте /start, чтобы начать.",
            reply_markup=to_menu_mpk(),
        )
        await call.answer()
        return
    try:
        activation = activate_trial_once(user.id)
    except (TrialActivationError, SQLAlchemyError):
        await call.message.edit_text(
            "Не удалось включить пробный период. Попробуйте позже.",
            reply_markup=to_menu_mpk(),
        )
        await call.answer()
        return

    decision = get_access_decision(user.id)
    try:
        runtime = get_payment_runtime()
    except PaymentRuntimeConfigurationError:
        runtime = None
    payment = (
        None
        if runtime is None
        else runtime.payment_service.get_latest_payment(user.id)
    )
    receipt_email = get_receipt_email(user.id)
    text = format_subscription_screen(runtime, decision, payment, receipt_email)
    if activation.activated:
        text = "✅ Пробный период включён\n\n" + text
    await call.message.edit_text(
        text,
        reply_markup=_payment_markup(payment, decision, runtime),
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
    if runtime.website_first:
        decision = get_access_decision(user.id)
        receipt_email = get_receipt_email(user.id)
        await call.message.edit_text(
            format_subscription_screen(runtime, decision, None, receipt_email),
            reply_markup=_payment_markup(None, decision, runtime),
        )
        await call.answer()
        return
    payment = runtime.payment_service.get_latest_payment(user.id)
    decision = get_access_decision(user.id)
    receipt_email = get_receipt_email(user.id)
    if _is_paid_active(decision) or _is_paid_reserved(payment):
        await call.message.edit_text(
            format_subscription_screen(runtime, decision, payment, receipt_email),
            reply_markup=_payment_markup(payment, decision, runtime),
        )
        await call.answer()
        return
    if receipt_email is None:
        await call.message.edit_text(
            RECEIPT_EMAIL_PROMPT,
            reply_markup=receipt_email_mpk(back_callback=SUBSCRIPTION_CALLBACK),
        )
        await state.set_state(ReceiptEmail.awaiting_payment_email)
        await call.answer()
        return
    try:
        payment = runtime.payment_service.get_or_create_payment(user.id)
    except ReceiptEmailRequired:
        await call.message.edit_text(
            RECEIPT_EMAIL_PROMPT,
            reply_markup=receipt_email_mpk(back_callback=SUBSCRIPTION_CALLBACK),
        )
        await state.set_state(ReceiptEmail.awaiting_payment_email)
        await call.answer()
        return
    except (PaymentProviderError, SQLAlchemyError, RuntimeError, ValueError):
        await call.message.edit_text(
            "Не удалось подготовить оплату. Попробуйте позже.",
            reply_markup=_payment_markup(payment, decision, runtime),
        )
        await call.answer()
        return
    decision = get_access_decision(user.id)
    if payment.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE:
        message = "Не удалось подготовить оплату. Попробуйте позже."
    else:
        message = format_subscription_screen(runtime, decision, payment, receipt_email)
    await call.message.edit_text(
        message,
        reply_markup=_payment_markup(payment, decision, runtime),
    )
    await call.answer()


@dp.message(ReceiptEmail.awaiting_payment_email)
async def subscription_receipt_email_message(
    message: types.Message, state: FSMContext
) -> None:
    user = User.get(tg_id=message.from_user.id)
    if user is None:
        await message.answer("Отправьте /start, чтобы начать.", reply_markup=to_menu_mpk())
        await tryFinish(state)
        return
    try:
        receipt_email = save_receipt_email(user.id, message.text or "")
    except ReceiptEmailValidationError:
        await message.answer(RECEIPT_EMAIL_ERROR_MESSAGE, reply_markup=receipt_email_mpk(
            back_callback=SUBSCRIPTION_CALLBACK
        ))
        return
    except (ReceiptEmailProfileUnavailable, LookupError):
        await message.answer("Сначала заполните профиль.", reply_markup=to_menu_mpk())
        await tryFinish(state)
        return
    await tryFinish(state)
    runtime = await _load_runtime_or_show_error(message, reply_to_user=True)
    if runtime is None:
        return
    await _create_payment_after_receipt_email(
        message,
        user.id,
        runtime,
        receipt_email,
    )


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
            reply_markup=_payment_markup(
                PaymentServiceResult(
                    payment_id=payment_id,
                    status=PaymentStatus.PENDING,
                    confirmation_url=None,
                    reason=PaymentServiceReason.NOT_SUCCEEDED,
                ),
                AccessDecision(AccessStatus.EXPIRED, None),
                runtime,
            ),
        )
        await call.answer()
        return
    decision = get_access_decision(user.id)
    receipt_email = get_receipt_email(user.id)
    if payment.reason is PaymentServiceReason.PROVIDER_UNAVAILABLE:
        message = "Сейчас не удалось проверить оплату. Попробуйте позже."
    else:
        message = format_subscription_screen(runtime, decision, payment, receipt_email)
    await call.message.edit_text(
        message,
        reply_markup=_payment_markup(payment, decision, runtime),
    )
    await call.answer()


def _commercial_markup(runtime: PaymentRuntime, *, back_callback: str):
    commercial = runtime.commercial_public
    return subscription_info_mpk(
        public_site_url=commercial.public_site_url,
        terms_url=commercial.terms_url,
        privacy_url=commercial.privacy_url,
        refund_url=commercial.refund_url,
        show_support=commercial.support_email is not None,
        back_callback=back_callback,
    )


@dp.callback_query(F.data == SUBSCRIPTION_DOCUMENTS_CALLBACK)
async def subscription_documents(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    runtime = await _load_runtime_or_show_error(call.message)
    if runtime is not None:
        await call.message.edit_text(
            "📄 Документы и поддержка\n\n"
            "Сервис предоставляет общие фитнес-рекомендации и не заменяет медицинскую помощь. "
            "Учитывайте личные ограничения и при необходимости обратитесь к квалифицированному специалисту.",
            reply_markup=_commercial_markup(runtime, back_callback="profile"),
        )
    await call.answer()


@dp.callback_query(F.data == SUBSCRIPTION_SUPPORT_CALLBACK)
async def subscription_support(call: types.CallbackQuery, state: FSMContext) -> None:
    await tryFinish(state)
    runtime = await _load_runtime_or_show_error(call.message)
    if runtime is not None:
        support_email = runtime.commercial_public.support_email
        text = (
            "💬 Поддержка\n\n"
            f"По вопросам оплаты, доступа или возврата напишите: {support_email}"
            if support_email is not None
            else "💬 Поддержка временно недоступна."
        )
        await call.message.edit_text(
            text,
            reply_markup=_commercial_markup(runtime, back_callback=SUBSCRIPTION_CALLBACK),
        )
    await call.answer()
