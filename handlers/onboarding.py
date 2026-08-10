"""Fitness onboarding handlers."""

from html import escape

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from db import User
from handlers.markups import (
    onboarding_confirmation_mkp,
    onboarding_experience_mkp,
    onboarding_goal_mkp,
    onboarding_limitations_mkp,
    onboarding_sex_mkp,
    start_mkp,
)
from services.onboarding import (
    EXPERIENCE_LABELS,
    GOAL_LABELS,
    SEX_LABELS,
    OnboardingData,
    OnboardingPersistenceError,
    OnboardingValidationError,
    normalize_limitations,
    parse_age,
    parse_height_cm,
    parse_session_duration_minutes,
    parse_weight_kg,
    parse_workouts_per_week,
    save_profile_and_trial,
    update_existing_profile,
    validate_choice,
)
from storage.config import dp
from storage.states import Onboarding


async def start_onboarding(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
    editing_profile: bool = False,
) -> None:
    await state.clear()
    await state.update_data(editing_profile=editing_profile)
    await state.set_state(Onboarding.age)
    text = (
        "Давайте составим ваш фитнес-профиль.\n\n"
        "Сколько вам полных лет?"
    )
    if edit:
        await message.edit_text(text)
    else:
        await message.answer(text)


@dp.message(Onboarding.age)
async def onboarding_age(message: types.Message, state: FSMContext):
    try:
        age = parse_age(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(age=age)
    await state.set_state(Onboarding.sex)
    await message.answer("Укажите пол.", reply_markup=onboarding_sex_mkp())


@dp.callback_query(
    StateFilter(Onboarding.sex),
    F.data.startswith("onboarding:sex:"),
)
async def onboarding_sex(call: types.CallbackQuery, state: FSMContext):
    value = call.data.rsplit(":", 1)[-1]
    try:
        sex = validate_choice(value, SEX_LABELS, "вариант")
    except OnboardingValidationError as error:
        await call.answer(str(error), show_alert=True)
        return

    await state.update_data(sex=sex)
    await state.set_state(Onboarding.height_cm)
    await call.message.edit_text("Введите рост в сантиметрах.")
    await call.answer()


@dp.message(Onboarding.height_cm)
async def onboarding_height(message: types.Message, state: FSMContext):
    try:
        height_cm = parse_height_cm(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(height_cm=height_cm)
    await state.set_state(Onboarding.weight_kg)
    await message.answer("Введите вес в килограммах, например 72,5.")


@dp.message(Onboarding.weight_kg)
async def onboarding_weight(message: types.Message, state: FSMContext):
    try:
        weight_kg = parse_weight_kg(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(weight_kg=weight_kg)
    await state.set_state(Onboarding.goal)
    await message.answer("Какая у вас основная цель?", reply_markup=onboarding_goal_mkp())


@dp.callback_query(
    StateFilter(Onboarding.goal),
    F.data.startswith("onboarding:goal:"),
)
async def onboarding_goal(call: types.CallbackQuery, state: FSMContext):
    value = call.data.rsplit(":", 1)[-1]
    try:
        goal = validate_choice(value, GOAL_LABELS, "цель")
    except OnboardingValidationError as error:
        await call.answer(str(error), show_alert=True)
        return

    await state.update_data(goal=goal)
    await state.set_state(Onboarding.experience_level)
    await call.message.edit_text(
        "Какой у вас опыт тренировок?",
        reply_markup=onboarding_experience_mkp(),
    )
    await call.answer()


@dp.callback_query(
    StateFilter(Onboarding.experience_level),
    F.data.startswith("onboarding:experience:"),
)
async def onboarding_experience(call: types.CallbackQuery, state: FSMContext):
    value = call.data.rsplit(":", 1)[-1]
    try:
        experience = validate_choice(
            value,
            EXPERIENCE_LABELS,
            "уровень подготовки",
        )
    except OnboardingValidationError as error:
        await call.answer(str(error), show_alert=True)
        return

    await state.update_data(experience_level=experience)
    await state.set_state(Onboarding.workouts_per_week)
    await call.message.edit_text("Сколько тренировок в неделю вы планируете?")
    await call.answer()


@dp.message(Onboarding.workouts_per_week)
async def onboarding_workouts(message: types.Message, state: FSMContext):
    try:
        workouts = parse_workouts_per_week(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(workouts_per_week=workouts)
    await state.set_state(Onboarding.session_duration_minutes)
    await message.answer("Сколько минут обычно можете уделить тренировке?")


@dp.message(Onboarding.session_duration_minutes)
async def onboarding_duration(message: types.Message, state: FSMContext):
    try:
        duration = parse_session_duration_minutes(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(session_duration_minutes=duration)
    await state.set_state(Onboarding.limitations)
    await message.answer(
        "Травмы или ограничения (необязательно). "
        "Кратко опишите их или выберите «Нет ограничений».\n\n"
        "Бот не ставит диагнозы. При сомнениях обсудите нагрузку со специалистом.",
        reply_markup=onboarding_limitations_mkp(),
    )


def _summary(data: dict) -> str:
    limitations = data["limitations"] or "Нет"
    return (
        "Проверьте анкету:\n\n"
        f"Возраст: {data['age']}\n"
        f"Пол: {SEX_LABELS[data['sex']]}\n"
        f"Рост: {data['height_cm']} см\n"
        f"Вес: {data['weight_kg']:g} кг\n"
        f"Цель: {GOAL_LABELS[data['goal']]}\n"
        f"Опыт: {EXPERIENCE_LABELS[data['experience_level']]}\n"
        f"Тренировок в неделю: {data['workouts_per_week']}\n"
        f"Длительность: {data['session_duration_minutes']} мин\n"
        f"Ограничения: {escape(limitations)}\n\n"
        "После подтверждения данные будут сохранены."
    )


@dp.message(Onboarding.limitations)
async def onboarding_limitations(message: types.Message, state: FSMContext):
    try:
        limitations = normalize_limitations(message.text or "")
    except OnboardingValidationError as error:
        await message.answer(str(error))
        return

    await state.update_data(limitations=limitations)
    data = await state.get_data()
    await state.set_state(Onboarding.confirmation)
    await message.answer(_summary(data), reply_markup=onboarding_confirmation_mkp())


@dp.callback_query(
    StateFilter(Onboarding.limitations),
    F.data == "onboarding:limitations:none",
)
async def onboarding_no_limitations(
    call: types.CallbackQuery,
    state: FSMContext,
):
    await state.update_data(limitations=normalize_limitations("нет"))
    data = await state.get_data()
    await state.set_state(Onboarding.confirmation)
    await call.message.edit_text(
        _summary(data),
        reply_markup=onboarding_confirmation_mkp(),
    )
    await call.answer()


@dp.callback_query(
    StateFilter(Onboarding.confirmation),
    F.data == "onboarding:confirm",
)
async def onboarding_confirm(call: types.CallbackQuery, state: FSMContext):
    state_data = await state.get_data()
    editing_profile = bool(state_data.pop("editing_profile", False))
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await state.clear()
        await call.message.edit_text("Не удалось найти пользователя. Отправьте /start.")
        await call.answer()
        return

    try:
        data = OnboardingData(**state_data)
        if editing_profile:
            update_existing_profile(user.id, data)
            text = "Профиль обновлён. Пробный период сохранён."
        else:
            result = save_profile_and_trial(user.id, data)
            if result.created:
                text = "Анкета сохранена. Пробный период на 3 дня начался."
            else:
                text = "Анкета уже была сохранена. Пробный период не перезапущен."
    except (TypeError, OnboardingPersistenceError):
        await call.answer("Не удалось сохранить анкету. Отправьте /start.", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(text, reply_markup=start_mkp())
    await call.answer()


@dp.callback_query(F.data == "onboarding:restart")
async def onboarding_restart(call: types.CallbackQuery, state: FSMContext):
    state_data = await state.get_data()
    await start_onboarding(
        call.message,
        state,
        edit=True,
        editing_profile=bool(state_data.get("editing_profile", False)),
    )
    await call.answer()
