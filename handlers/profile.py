from html import escape

from aiogram.utils.deep_linking import create_start_link
from aiogram.fsm.context import FSMContext
from aiogram import F, types

from db import User
from handlers.markups import profile_mpk, receipt_email_mpk, to_menu_mpk
from handlers.onboarding import start_onboarding
from services.access import (
    AccessEntitlement,
    AccessStatus,
    evaluate_access,
    get_user_access,
)
from services.onboarding import (
    EXPERIENCE_LABELS,
    GOAL_LABELS,
    SEX_LABELS,
    TRAINING_ENVIRONMENT_LABELS,
    get_fitness_profile,
)
from services.receipt_email import (
    RECEIPT_EMAIL_ERROR_MESSAGE,
    ReceiptEmailProfileUnavailable,
    ReceiptEmailValidationError,
    get_receipt_email,
    save_receipt_email,
)
from storage.config import bot, dp
from storage.states import ContactWithDevs, ReceiptEmail, tryFinish


@dp.callback_query(F.data == "profile")
async def profileCall(call: types.CallbackQuery, state: FSMContext):
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы создать профиль.")
        await call.answer()
        return

    profile = get_fitness_profile(user.id)
    if profile is None:
        await start_onboarding(call.message, state, edit=True)
        await call.answer()
        return

    access = get_user_access(user.id)
    decision = evaluate_access(access)
    access_labels = {
        AccessStatus.ACTIVE: "Активен",
        AccessStatus.TRIAL: "Пробный период",
        AccessStatus.TRIAL_AVAILABLE: "Пробный период доступен",
        AccessStatus.EXPIRED: "Истёк",
    }
    if decision.ends_at is not None:
        access_text = (
            f"{access_labels[decision.status]} до "
            f"{decision.ends_at.strftime('%d.%m.%Y %H:%M')} UTC"
        )
    else:
        access_text = access_labels[decision.status]
        if decision.entitlement is AccessEntitlement.EXPIRED_TRIAL:
            access_text = "Пробный период завершён"

    link = await create_start_link(bot, str(call.from_user.id))
    receipt_email = get_receipt_email(user.id)
    receipt_email_text = escape(receipt_email) if receipt_email else "не указан"
    environment = (
        TRAINING_ENVIRONMENT_LABELS.get(profile.training_environment)
        if profile.training_environment is not None
        else "Не указано"
    )
    text = (
        f"Здравствуйте, {escape(call.from_user.first_name)}!\n\n"
        f"Возраст: {profile.age}\n"
        f"Пол: {SEX_LABELS.get(profile.sex, profile.sex)}\n"
        f"Рост: {profile.height_cm} см\n"
        f"Вес: {profile.weight_kg:g} кг\n"
        f"Цель: {GOAL_LABELS.get(profile.goal, profile.goal)}\n"
        f"Опыт: {EXPERIENCE_LABELS.get(profile.experience_level, profile.experience_level)}\n"
        f"Место: {environment}\n"
        f"Тренировок в неделю: {profile.workouts_per_week}\n"
        f"Длительность: {profile.session_duration_minutes} мин\n\n"
        f"Email для чеков: {receipt_email_text}\n\n"
        f"Доступ: {access_text}\n\n"
        f"Ваша реферальная ссылка:\n<code>{link}</code>"
    )
    await call.message.edit_text(
        text,
        reply_markup=profile_mpk(receipt_email_set=receipt_email is not None),
    )
    await call.answer()


@dp.callback_query(F.data == "profile:edit")
async def profile_edit_call(call: types.CallbackQuery, state: FSMContext):
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы создать профиль.")
        await call.answer()
        return

    profile = get_fitness_profile(user.id)
    await start_onboarding(
        call.message,
        state,
        edit=True,
        editing_profile=profile is not None,
    )
    await call.answer()


@dp.callback_query(F.data == "profile:receipt_email")
async def profile_receipt_email_call(
    call: types.CallbackQuery, state: FSMContext
) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы создать профиль.")
    elif get_fitness_profile(user.id) is None:
        await call.message.edit_text("Сначала заполните профиль.", reply_markup=to_menu_mpk())
    else:
        await call.message.edit_text(
            "📧 Email для чеков\n\n"
            "Укажите email. Мы будем использовать его только для отправки чеков после оплаты.",
            reply_markup=receipt_email_mpk(),
        )
        await state.set_state(ReceiptEmail.awaiting_profile_email)
    await call.answer()


@dp.message(ReceiptEmail.awaiting_profile_email)
async def profile_receipt_email_message(
    message: types.Message, state: FSMContext
) -> None:
    user = User.get(tg_id=message.from_user.id)
    if user is None:
        await message.answer("Отправьте /start, чтобы создать профиль.", reply_markup=to_menu_mpk())
        await tryFinish(state)
        return
    try:
        save_receipt_email(user.id, message.text or "")
    except ReceiptEmailValidationError:
        await message.answer(RECEIPT_EMAIL_ERROR_MESSAGE, reply_markup=receipt_email_mpk())
        return
    except (ReceiptEmailProfileUnavailable, LookupError):
        await message.answer("Сначала заполните профиль.", reply_markup=to_menu_mpk())
        await tryFinish(state)
        return
    await tryFinish(state)
    await message.answer(
        "Email для чеков сохранён.",
        reply_markup=receipt_email_mpk(),
    )

@dp.callback_query(F.data == "contact_with_devs")
async def contactWithDevsCall(call: types.CallbackQuery, state: FSMContext):
    await tryFinish(state)
    await call.message.edit_text(
        "Поддержка: kurtbachotto@gmail.com\n"
        "Укажите только вопрос о работе сервиса или заказе. "
        "Не присылайте диагнозы, сведения о здоровье или медицинские документы.",
        reply_markup=to_menu_mpk(),
    )

@dp.message(ContactWithDevs.Message)
async def contactWithDevsGoCall(message: types.Message, state: FSMContext):
    # Never forward unsolicited health text/media from legacy support states.
    await message.answer(
        "Сообщения из бота не пересылаются. Поддержка: kurtbachotto@gmail.com. "
        "Не направляйте сведения о здоровье.", reply_markup=to_menu_mpk(),
    )
    await tryFinish(state)
