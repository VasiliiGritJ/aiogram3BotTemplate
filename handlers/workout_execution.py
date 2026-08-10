"""Telegram UI for the durable guided-workout service."""

from html import escape
import math

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import (
    start_mkp,
    to_menu_mpk,
    workout_cancel_confirmation_mkp,
    workout_current_mkp,
)
from services.access import AccessStatus, get_access_decision
from services.workout_execution import (
    CurrentWorkoutStep,
    WorkoutAccessDeniedError,
    WorkoutExecutionError,
    WorkoutPlanRequiredError,
    WorkoutSessionView,
    cancel_workout,
    complete_workout,
    get_active_workout,
    get_current_step,
    get_or_start_workout,
    get_workout_history,
    record_set_result,
)
from storage.config import dp
from storage.states import WorkoutExecution


WORKOUT_START_CALLBACK = "workout:start"
WORKOUT_RESUME_CALLBACK = "workout:resume"
WORKOUT_RECORD_SET_CALLBACK = "workout:record_set"
WORKOUT_COMPLETE_CALLBACK = "workout:complete"
WORKOUT_CANCEL_CALLBACK = "workout:cancel"
WORKOUT_CANCEL_CONFIRM_CALLBACK = "workout:cancel:confirm"
WORKOUT_CANCEL_RESUME_CALLBACK = "workout:cancel:resume"
WORKOUT_HISTORY_CALLBACK = "workout:history"

MAX_WEIGHT_INPUT_LENGTH = 32
MAX_REPS_INPUT_LENGTH = 9


def workout_entry_action(user_id: int) -> tuple[str, str] | None:
    """Return a menu action without changing access or workout state."""
    if get_active_workout(user_id) is not None:
        return "▶️ Продолжить тренировку", WORKOUT_RESUME_CALLBACK

    decision = get_access_decision(user_id)
    if decision.status == AccessStatus.TRIAL_AVAILABLE:
        return "🏋️ Начать первую тренировку бесплатно", WORKOUT_START_CALLBACK
    if decision.has_access:
        return "🏋️ Начать тренировку", WORKOUT_START_CALLBACK
    return None


def workout_menu_markup(user_id: int):
    """Build the completed-profile menu from durable access/workout state."""
    return start_mkp(workout_entry_action(user_id), show_history=True)


def parse_workout_weight(text: str) -> float:
    value = text.strip().replace(",", ".")
    if not value or len(value) > MAX_WEIGHT_INPUT_LENGTH:
        raise ValueError
    try:
        weight = float(value)
    except ValueError as error:
        raise ValueError from error
    if not math.isfinite(weight) or weight < 0:
        raise ValueError
    return weight


def parse_workout_reps(text: str) -> int:
    value = text.strip()
    if not value or len(value) > MAX_REPS_INPUT_LENGTH or not value.isdecimal():
        raise ValueError
    repetitions = int(value)
    if repetitions < 1:
        raise ValueError
    return repetitions


def _format_step(workout: WorkoutSessionView, step: CurrentWorkoutStep) -> str:
    if step.ready_to_complete:
        return (
            f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n\n"
            "✅ Все упражнения и подходы выполнены."
        )

    exercise = step.exercise
    assert exercise is not None and step.set_number is not None
    hint = (
        f"\n\n💡 {escape(exercise.selected_hint)}"
        if exercise.selected_hint
        else ""
    )
    return (
        f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n\n"
        f"Упражнение {exercise.exercise_order} из {len(workout.exercises)}\n"
        f"<b>{escape(exercise.selected_exercise_name)}</b>\n\n"
        f"Группа мышц: {escape(exercise.selected_primary_muscle_group)}\n"
        f"Подход: {step.set_number} из {exercise.selected_target_sets}\n"
        f"Цель: {exercise.selected_target_reps_min}–{exercise.selected_target_reps_max} повторений\n"
        f"Отдых: {exercise.selected_rest_seconds} сек"
        f"{hint}"
    )


async def show_current_workout(
    message: types.Message,
    user_id: int,
    *,
    edit: bool,
) -> bool:
    """Render the actual persisted step; FSM never supplies a cursor."""
    try:
        workout = get_active_workout(user_id)
        if workout is None:
            raise WorkoutExecutionError("No active workout.")
        step = get_current_step(user_id, workout.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        text = "Нет активной тренировки. Выберите нужное действие в меню."
        markup = to_menu_mpk()
    else:
        text = _format_step(workout, step)
        markup = workout_current_mkp(ready_to_complete=step.ready_to_complete)

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)
    return not text.startswith("Нет активной")


def _workout_summary(workout: WorkoutSessionView) -> str:
    set_count = sum(len(exercise.set_results) for exercise in workout.exercises)
    duration = ""
    if workout.finished_at is not None:
        minutes = max(
            0,
            int((workout.finished_at - workout.started_at).total_seconds() // 60),
        )
        duration = f"\nДлительность: {minutes} мин"
    return (
        "🎉 Тренировка завершена\n\n"
        f"День {workout.day_number} — {escape(workout.day_title)}\n"
        f"Упражнений: {len(workout.exercises)}\n"
        f"Сохранённых подходов: {set_count}"
        f"{duration}"
    )


@dp.callback_query(F.data.in_({WORKOUT_START_CALLBACK, WORKOUT_RESUME_CALLBACK}))
async def workout_start_or_resume(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await state.clear()
        await call.message.edit_text("Отправьте /start, чтобы начать.")
        await call.answer()
        return

    try:
        get_or_start_workout(user.id)
    except WorkoutAccessDeniedError:
        await state.clear()
        await call.message.edit_text(
            "Для начала новой тренировки нужен активный доступ.",
            reply_markup=workout_menu_markup(user.id),
        )
    except WorkoutPlanRequiredError:
        await state.clear()
        await call.message.edit_text(
            "Сначала откройте «Мой план», чтобы подготовить тренировку.",
            reply_markup=workout_menu_markup(user.id),
        )
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await call.message.edit_text(
            "Не удалось открыть тренировку. Попробуйте ещё раз.",
            reply_markup=workout_menu_markup(user.id),
        )
    else:
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data == WORKOUT_RECORD_SET_CALLBACK)
async def workout_record_set(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        step = get_current_step(user.id)
        if step.ready_to_complete:
            await state.clear()
            await show_current_workout(call.message, user.id, edit=True)
        else:
            await state.clear()
            await state.update_data(workout_session_id=step.workout_id)
            await state.set_state(WorkoutExecution.awaiting_weight)
            await call.message.edit_text(
                "Введите рабочий вес в кг.\n\n"
                "Например: 40 или 12.5\n"
                "Для упражнения с собственным весом можно указать 0.",
                reply_markup=workout_current_mkp(),
            )
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.message(StateFilter(WorkoutExecution.awaiting_weight))
async def workout_weight_input(message: types.Message, state: FSMContext) -> None:
    try:
        weight = parse_workout_weight(message.text or "")
    except ValueError:
        await message.answer(
            "Введите вес числом не меньше 0, например 40 или 12,5."
        )
        return

    data = await state.get_data()
    if data.get("workout_session_id") is None:
        await state.clear()
        await message.answer("Ввод подхода устарел. Продолжите тренировку из меню.")
        return
    await state.update_data(pending_weight=weight)
    await state.set_state(WorkoutExecution.awaiting_reps)
    await message.answer("Сколько повторений выполнено?")


@dp.message(StateFilter(WorkoutExecution.awaiting_reps))
async def workout_reps_input(message: types.Message, state: FSMContext) -> None:
    try:
        repetitions = parse_workout_reps(message.text or "")
    except ValueError:
        await message.answer("Введите количество повторений целым числом не меньше 1.")
        return

    user = User.get(tg_id=message.from_user.id)
    data = await state.get_data()
    workout_id = data.get("workout_session_id")
    weight = data.get("pending_weight")
    if user is None or workout_id is None or weight is None:
        await state.clear()
        await message.answer("Ввод подхода устарел. Продолжите тренировку из меню.")
        return

    try:
        step = get_current_step(user.id, workout_id)
        if step.ready_to_complete or step.exercise is None or step.set_number is None:
            raise WorkoutExecutionError("Workout is ready to complete.")
        record_set_result(
            user.id,
            workout_id,
            step.exercise.id,
            step.set_number,
            weight,
            repetitions,
        )
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await message.answer("Подход уже изменился. Показываю актуальное состояние.")
        await show_current_workout(message, user.id, edit=False)
        return

    await state.clear()
    await message.answer(f"✅ Подход сохранён: {weight:g} кг × {repetitions}")
    await show_current_workout(message, user.id, edit=False)


@dp.callback_query(F.data == WORKOUT_COMPLETE_CALLBACK)
async def workout_complete(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        active = get_active_workout(user.id)
        if active is None:
            await state.clear()
            await call.message.edit_text(
                "Нет активной тренировки.", reply_markup=to_menu_mpk()
            )
            await call.answer()
            return
        workout = complete_workout(user.id, active.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        await show_current_workout(call.message, user.id, edit=True)
    else:
        await state.clear()
        await call.message.edit_text(_workout_summary(workout), reply_markup=to_menu_mpk())
    await call.answer()


@dp.callback_query(F.data == WORKOUT_CANCEL_CALLBACK)
async def workout_cancel_request(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    try:
        active = None if user is None else get_active_workout(user.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        active = None
    if active is None:
        await state.clear()
        await call.message.edit_text("Нет активной тренировки.", reply_markup=to_menu_mpk())
    else:
        await call.message.edit_text(
            "Точно завершить тренировку без сохранения её как выполненной? "
            "Уже записанные подходы останутся в истории сессии.",
            reply_markup=workout_cancel_confirmation_mkp(),
        )
    await call.answer()


@dp.callback_query(F.data == WORKOUT_CANCEL_CONFIRM_CALLBACK)
async def workout_cancel_confirm(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    try:
        active = None if user is None else get_active_workout(user.id)
        if user is None or active is None:
            await state.clear()
            await call.message.edit_text(
                "Нет активной тренировки.", reply_markup=to_menu_mpk()
            )
        else:
            cancel_workout(user.id, active.id)
            await state.clear()
            await call.message.edit_text(
                "Тренировка отменена. Сохранённые подходы не удалены.",
                reply_markup=workout_menu_markup(user.id),
            )
    except (WorkoutExecutionError, SQLAlchemyError):
        if user is None:
            await call.message.edit_text("Отправьте /start, чтобы начать.")
        else:
            await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data == WORKOUT_CANCEL_RESUME_CALLBACK)
async def workout_cancel_resume(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    await state.clear()
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.")
    else:
        await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data == WORKOUT_HISTORY_CALLBACK)
async def workout_history(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    await state.clear()
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.")
        await call.answer()
        return
    try:
        history = get_workout_history(user.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        await call.message.edit_text("Не удалось загрузить историю.", reply_markup=to_menu_mpk())
        await call.answer()
        return
    if not history:
        text = "📊 История тренировок пока пуста."
    else:
        rows = []
        for workout in history[:5]:
            set_count = sum(len(exercise.set_results) for exercise in workout.exercises)
            completed_at = workout.finished_at or workout.started_at
            rows.append(
                f"{completed_at.strftime('%d.%m.%Y')} — День {workout.day_number} "
                f"— {escape(workout.day_title)}\n"
                f"Упражнений: {len(workout.exercises)}, подходов: {set_count}"
            )
        text = "📊 История тренировок\n\n" + "\n\n".join(rows)
    await call.message.edit_text(text, reply_markup=to_menu_mpk())
    await call.answer()
