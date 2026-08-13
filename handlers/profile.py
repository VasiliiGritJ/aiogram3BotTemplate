import datetime
from html import escape

from aiogram.utils.deep_linking import create_start_link
from aiogram.fsm.context import FSMContext
from aiogram import F, types

from db import User
from handlers.markups import cancel_mpk, profile_mpk, to_menu_mpk
from handlers.onboarding import start_onboarding
from services.access import AccessStatus, evaluate_access, get_user_access
from services.onboarding import (
    EXPERIENCE_LABELS,
    GOAL_LABELS,
    SEX_LABELS,
    TRAINING_ENVIRONMENT_LABELS,
    get_fitness_profile,
)
from storage.config import admins, bot, dp
from storage.states import ContactWithDevs, tryFinish


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
        AccessStatus.EXPIRED: "Истёк",
    }
    if decision.ends_at is not None:
        access_text = (
            f"{access_labels[decision.status]} до "
            f"{decision.ends_at.strftime('%d.%m.%Y %H:%M')} UTC"
        )
    else:
        access_text = access_labels[decision.status]

    link = await create_start_link(bot, str(call.from_user.id))
    limitations = escape(profile.limitations or "Нет")
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
        f"Длительность: {profile.session_duration_minutes} мин\n"
        f"Ограничения: {limitations}\n\n"
        f"Доступ: {access_text}\n\n"
        f"Ваша реферальная ссылка:\n<code>{link}</code>"
    )
    await call.message.edit_text(text, reply_markup=profile_mpk())
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

@dp.callback_query(F.data == "contact_with_devs")
async def contactWithDevsCall(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text('Отправьте сообщение, фото, видео, стикер, все, что угодно - я передам его разработчикам', reply_markup=cancel_mpk())
    await state.set_state(ContactWithDevs.Message)

@dp.message(ContactWithDevs.Message)
async def contactWithDevsGoCall(message: types.Message, state: FSMContext):
    for adminTgId in admins:
        try:
            await bot.send_message(adminTgId, f"Новое сообщение от юзера {message.from_user.id} (@{message.from_user.username} | {message.from_user.full_name}) в {datetime.datetime.now().strftime("%d/%m/%Y, %H:%M:%S")}")
            await bot.copy_message(adminTgId, message.from_user.id, message.message_id)
        except:
            pass
    await message.answer(f'Ваше сообщение отправлено', reply_markup=to_menu_mpk())
    await tryFinish(state)
