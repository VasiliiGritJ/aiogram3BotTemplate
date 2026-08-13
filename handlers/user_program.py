"""Telegram confirmation flow for deterministic user-provided programs."""

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import (
    to_menu_mpk,
    user_program_mode_mkp,
    user_program_preview_mkp,
    workout_plan_mkp,
)
from handlers.workout_execution import workout_entry_action
from services.onboarding import get_fitness_profile
from services.user_programs import (
    AdaptationMode,
    DeterministicRussianProgramParser,
    UserProgramDraft,
    assign_user_program,
    format_parse_issues,
    format_user_program_preview,
)
from services.workout_plans import WorkoutPlanError, format_workout_plan
from storage.config import dp
from storage.states import UserProgram


PARSER = DeterministicRussianProgramParser()


@dp.callback_query(F.data == "user_program:start")
async def user_program_start(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UserProgram.choosing_mode)
    await call.message.edit_text(
        "Как бот должен работать с вашей программой?",
        reply_markup=user_program_mode_mkp(),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("user_program:mode:"))
async def user_program_mode(call: types.CallbackQuery, state: FSMContext) -> None:
    value = (call.data or "").removeprefix("user_program:mode:")
    try:
        mode = AdaptationMode(value)
    except ValueError:
        await call.answer("Этот режим недоступен.", show_alert=True)
        return
    await state.clear()
    await state.update_data(user_program_mode=mode.value)
    await state.set_state(UserProgram.awaiting_text)
    await call.message.edit_text(
        "Отправьте программу одним сообщением.\n\n"
        "Пример:\nДень 1 — грудь\nЖим штанги лёжа 4×8\n"
        "Жим гантелей под углом 3×10–12\n\n"
        "Пока поддерживаются обычные подходы и повторы.",
        reply_markup=workout_plan_mkp(),
    )
    await call.answer()


@dp.message(StateFilter(UserProgram.awaiting_text))
async def user_program_text(message: types.Message, state: FSMContext) -> None:
    user = User.get(tg_id=message.from_user.id)
    data = await state.get_data()
    if user is None or data.get("user_program_mode") is None:
        await state.clear()
        await message.answer("Начните заново из раздела «Мой план».", reply_markup=to_menu_mpk())
        return
    profile = get_fitness_profile(user.id)
    result = PARSER.parse(
        message.text or "",
        AdaptationMode(data["user_program_mode"]),
        training_environment=(None if profile is None else profile.training_environment),
    )
    if not result.valid:
        await message.answer(format_parse_issues(result.issues))
        return
    assert result.draft is not None
    await state.update_data(user_program_draft=result.draft.to_payload())
    await state.set_state(UserProgram.awaiting_confirmation)
    await message.answer(
        format_user_program_preview(result.draft),
        reply_markup=user_program_preview_mkp(),
    )


@dp.callback_query(F.data == "user_program:retry")
async def user_program_retry(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    mode = data.get("user_program_mode")
    await state.clear()
    if mode is None:
        await call.message.edit_text("Начните заново из раздела «Мой план».", reply_markup=to_menu_mpk())
    else:
        await state.update_data(user_program_mode=mode)
        await state.set_state(UserProgram.awaiting_text)
        await call.message.edit_text("Отправьте исправленный текст программы.")
    await call.answer()


@dp.callback_query(F.data == "user_program:confirm")
async def user_program_confirm(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    payload = (await state.get_data()).get("user_program_draft")
    if user is None or not isinstance(payload, dict):
        await state.clear()
        await call.answer("Черновик устарел. Введите программу заново.", show_alert=True)
        return
    try:
        result = assign_user_program(user.id, UserProgramDraft.from_payload(payload))
    except (WorkoutPlanError, SQLAlchemyError, KeyError, TypeError, ValueError):
        await call.answer("Не удалось сохранить программу. Попробуйте позже.", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text(
        "✅ Программа сохранена\n\n" + format_workout_plan(result.plan),
        reply_markup=workout_plan_mkp(workout_entry_action(user.id)),
    )
    await call.answer()
