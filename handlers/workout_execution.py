"""Telegram UI for the durable guided-workout service."""

from html import escape
import math
from decimal import Decimal

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import (
    start_mkp,
    to_menu_mpk,
    workout_cancel_confirmation_mkp,
    workout_cooldown_mkp,
    workout_current_mkp,
    workout_history_detail_mkp,
    workout_history_mkp,
    workout_input_mkp,
    workout_environment_choices_mkp,
    workout_environment_start_mkp,
    workout_preview_mkp,
    workout_format_mkp,
    workout_replacement_mkp,
    workout_technique_mkp,
)
from services.access import AccessStatus, get_access_decision
from services.workout_execution import (
    CurrentWorkoutStep,
    WorkoutAccessDeniedError,
    WorkoutExecutionError,
    WorkoutEnvironmentChangeBlockedError,
    WorkoutEnvironmentError,
    WorkoutEnvironmentIncompatibleError,
    WorkoutPlanRequiredError,
    WorkoutSessionView,
    cancel_workout,
    change_workout_environment,
    complete_workout,
    get_active_workout,
    get_completed_workout_detail,
    get_current_step,
    get_default_training_environment,
    get_or_start_workout,
    get_workout_exercise_technique,
    get_workout_history_page,
    record_set_result,
)
from services.workout_progression import (
    ProgressionReason,
    ProgressionRecommendation,
    ProgressionStrategy,
)
from services.exercise_catalog import exercise_definition_by_code
from services.onboarding import TRAINING_ENVIRONMENT_LABELS
from services.workout_progression_history import (
    ProgressionHistoryError,
    get_progression_recommendation,
)
from services.workout_replacements import (
    ReplacementNotAllowedError,
    ReplacementReason,
    apply_replacement,
    get_replacement_options,
)
from services.workout_warmup import build_session_warmup
from services.workout_formats import (
    WorkoutFormatError,
    finish_format_block,
    get_format_state,
    record_completed_round,
    record_emom_minute,
    start_format_block,
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
WORKOUT_HISTORY_PAGE_PREFIX = "workout:history:page:"
WORKOUT_HISTORY_DETAIL_PREFIX = "workout:history:detail:"
WORKOUT_FORMAT_PREFIX = "workout:format:"
WORKOUT_REPLACEMENT_PREFIX = "workout:replace:"
WORKOUT_TECHNIQUE_CALLBACK = "workout:technique"
WORKOUT_TECHNIQUE_BACK_CALLBACK = "workout:technique:back"
WORKOUT_ENVIRONMENT_PREFIX = "workout:environment:"
WORKOUT_PREVIEW_START_CALLBACK = "workout:preview:start"
WORKOUT_PREVIEW_BACK_CALLBACK = "workout:preview:back"
WORKOUT_COOLDOWN_CALLBACK = "workout:cooldown"

MAX_WEIGHT_INPUT_LENGTH = 32
MAX_REPS_INPUT_LENGTH = 9
HISTORY_PAGE_SIZE = 5
DETAIL_MESSAGE_LIMIT = 3500


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


def _format_step(
    workout: WorkoutSessionView,
    step: CurrentWorkoutStep,
    recommendation: ProgressionRecommendation | None = None,
) -> str:
    environment = TRAINING_ENVIRONMENT_LABELS.get(
        workout.effective_training_environment,
        "не указано",
    )
    environment_line = f"Сегодня тренируемся: {escape(environment)}\n"
    if step.ready_to_complete:
        return (
            f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n\n"
            f"{environment_line}\n"
            "✅ Все упражнения и подходы выполнены.\n\n"
            "Заминка — по желанию. Можно пропустить её и завершить тренировку."
        )

    if step.kind == "format_block" and step.format_block is not None:
        block = step.format_block
        exercises = "\n".join(
            f"{item.selected_station_order or index}. {escape(item.selected_exercise_name)} — "
            f"{item.selected_format_reps or item.selected_target_reps_min} повт."
            for index, item in enumerate(block.exercises, start=1)
        )
        details = []
        if block.duration_seconds:
            details.append(f"Время: {block.duration_seconds // 60} мин")
        if block.target_rounds:
            details.append(f"Цель: {block.target_rounds} круга")
        status = ""
        if block.started_at is not None:
            status = f"\nВыполнено кругов: {block.completed_rounds}"
            if block.workout_format == "emom":
                status = (
                    f"\nМинут выполнено: {block.completed_minutes}; "
                    f"пропущено: {block.missed_minutes}"
                )
        return (
            f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n\n"
            f"{environment_line}\n"
            f"<b>{escape(block.title)}</b>\n"
            f"Формат: {_format_name(block.workout_format)}\n"
            f"{' · '.join(details)}\n\n{exercises}{status}"
        )

    exercise = step.exercise
    assert exercise is not None and step.set_number is not None
    ramp = build_session_warmup(workout).ramp_for(exercise.id)
    ramp_text = ""
    if ramp is not None:
        ramp_lines = "\n".join(f"• {escape(item)}" for item in ramp.instructions)
        ramp_text = (
            "\n\n🔥 <b>Разминочные подходы</b>\n"
            f"{ramp_lines}\n"
            "Они не считаются рабочими подходами."
        )
    hint = (
        f"\n\n💡 {escape(exercise.selected_hint)}"
        if exercise.selected_hint
        else ""
    )
    progression_hint = (
        f"\n\n📈 {format_progression_recommendation(exercise, recommendation)}"
        if recommendation is not None
        else ""
    )
    return (
        f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n\n"
        f"{environment_line}\n"
        f"Упражнение {exercise.exercise_order} из {len(workout.exercises)}\n"
        f"<b>{escape(exercise.selected_exercise_name)}</b>\n\n"
        f"Группа мышц: {escape(exercise.selected_primary_muscle_group)}\n"
        f"<b>🏋️ Рабочие подходы</b>\n"
        f"Подход: {step.set_number} из {exercise.selected_target_sets}\n"
        f"Цель: {exercise.selected_target_reps_min}–{exercise.selected_target_reps_max} повторений\n"
        f"Отдых: {exercise.selected_rest_seconds} сек"
        f"{ramp_text}"
        f"{hint}"
        f"{progression_hint}"
    )


def _format_progression_weight(weight: Decimal) -> str:
    if weight == 0:
        return "собств. вес"
    value = format(weight.normalize(), "f")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return f"{value.replace('.', ',')} кг"


def _format_previous_weights(weights: tuple[Decimal, ...]) -> str:
    if len(set(weights)) == 1:
        return _format_progression_weight(weights[0])
    return "/".join(_format_progression_weight(weight) for weight in weights)


def _format_progression_reps(reps: tuple[int, ...]) -> str:
    return "/".join(str(value) for value in reps)


def format_progression_recommendation(
    exercise,
    recommendation: ProgressionRecommendation,
) -> str:
    """Render one compact, advisory progression hint from the pure service."""
    target_text = (
        f"{exercise.selected_target_reps_min}–{exercise.selected_target_reps_max}"
    )
    if recommendation.reason == ProgressionReason.NO_HISTORY:
        if recommendation.strategy == ProgressionStrategy.BODYWEIGHT_REPS:
            return (
                f"Цель: {target_text} повторений с собственным весом. "
                "Начните в комфортном темпе."
            )
        return f"Цель: {target_text} повторений. Выберите комфортный рабочий вес."
    if recommendation.reason == ProgressionReason.INSUFFICIENT_DATA:
        return f"Данных для подсказки пока недостаточно. Цель: {target_text}."
    if recommendation.reason == ProgressionReason.MIXED_WEIGHTS_HOLD:
        previous = _format_previous_weights(recommendation.previous_weights_kg)
        reps = _format_progression_reps(recommendation.previous_reps)
        return (
            f"Прошлый раз: {previous} — {reps}\n"
            f"Сегодня: не меняйте веса автоматически. Цель: {target_text}."
        )

    previous = _format_previous_weights(recommendation.previous_weights_kg)
    previous_reps = _format_progression_reps(recommendation.previous_reps)
    suggested_weight = recommendation.suggested_weight_kg
    suggested_reps = recommendation.suggested_reps
    assert suggested_weight is not None and suggested_reps is not None
    today_reps = _format_progression_reps(suggested_reps)
    today_weight = _format_progression_weight(suggested_weight)

    if recommendation.reason == ProgressionReason.INCREASE_WEIGHT:
        today = f"Сегодня: попробуй {today_weight}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.STRENGTH_INCREASE_WEIGHT:
        today = f"Сегодня: небольшой шаг до {today_weight}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.HOLD_ADD_REPS:
        today = f"Сегодня: оставь {today_weight} и попробуй {today_reps}."
    elif recommendation.reason == ProgressionReason.STRENGTH_HOLD_ADD_REPS:
        today = f"Сегодня: оставь {today_weight} и попробуй {today_reps}."
    elif recommendation.reason in {
        ProgressionReason.HOLD_RECOVER_RANGE,
        ProgressionReason.HOLD_NO_SAFE_WEIGHT_STEP,
        ProgressionReason.STRENGTH_HOLD_RECOVER_RANGE,
        ProgressionReason.STRENGTH_HOLD_NO_SAFE_WEIGHT_STEP,
    }:
        today = f"Сегодня: оставь {today_weight}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.DECREASE_WEIGHT:
        today = f"Сегодня: попробуй {today_weight}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.STRENGTH_DELOAD:
        today = f"Сегодня: небольшой шаг назад до {today_weight}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.BODYWEIGHT_ADVANCE_VARIATION:
        successor = exercise_definition_by_code(
            recommendation.suggested_exercise_code or ""
        )
        name = successor.name if successor is not None else "более сложный вариант"
        today = f"Сегодня: попробуй {escape(name)}, цель {today_reps}."
    elif recommendation.reason == ProgressionReason.BODYWEIGHT_ADD_REPS:
        today = f"Сегодня: собственный вес, попробуй {today_reps}."
    else:
        today = f"Сегодня: собственный вес, цель {today_reps}."
    return f"Прошлый раз: {previous} — {previous_reps}\n{today}"


def _workout_environment_can_change(workout: WorkoutSessionView) -> bool:
    return (
        workout.status == "in_progress"
        and not any(
            exercise.set_results
            for exercise in workout.exercises
        )
        and not any(
            block.started_at is not None or block.finished_at is not None
            for block in workout.blocks
        )
    )


def _rep_range(reps_min: int, reps_max: int) -> str:
    if reps_min == reps_max:
        return str(reps_min)
    return f"{reps_min}–{reps_max}"


def _format_block_prescription(block) -> str:
    details = [f"Формат: {_format_name(block.workout_format)}"]
    if block.duration_seconds:
        details.append(f"Длительность: {block.duration_seconds // 60} мин")
    if block.target_rounds:
        details.append(f"Цель: {block.target_rounds} круга")
    return " · ".join(details)


def format_workout_preview(workout: WorkoutSessionView) -> str:
    """Render every persisted snapshot exercise before guided execution begins."""
    environment = TRAINING_ENVIRONMENT_LABELS.get(
        workout.effective_training_environment,
        "не указано",
    )
    warmup = build_session_warmup(workout)
    lines = [
        f"🏋️ День {workout.day_number} — {escape(workout.day_title)}",
        "",
        f"Сегодня тренируемся: {escape(environment)}",
        "",
        f"🔥 <b>Разминка · ~{warmup.estimated_minutes} мин</b>",
    ]
    for position, action in enumerate(
        warmup.general_preparation + warmup.movement_preparation,
        start=1,
    ):
        lines.append(f"{position}. <b>{escape(action.title)}</b> — {escape(action.instruction)}")
    if warmup.ramp_up_sets:
        lines.append("Подводящие подходы:")
        for ramp in warmup.ramp_up_sets:
            lines.append(
                f"• {escape(ramp.exercise_name)} — {ramp.set_count} "
                f"лёгк. подх. ({escape(ramp.instructions[0])})"
            )

    lines.extend(("", "🏋️ <b>Основная тренировка</b>"))
    standard_exercises = [
        item for item in workout.exercises if item.session_block_id is None
    ]
    for position, exercise in enumerate(standard_exercises, start=1):
        lines.append(
            f"{position}. {escape(exercise.selected_exercise_name)} — "
            f"{exercise.selected_target_sets} × "
            f"{_rep_range(exercise.selected_target_reps_min, exercise.selected_target_reps_max)}"
        )

    for block in workout.blocks:
        lines.extend(("", f"<b>{escape(block.title)}</b>", _format_block_prescription(block)))
        for position, exercise in enumerate(block.exercises, start=1):
            repetitions = exercise.selected_format_reps or exercise.selected_target_reps_min
            lines.append(
                f"{position}. {escape(exercise.selected_exercise_name)} — {repetitions} повт."
            )
    lines.extend((
        "",
        "🧘 <b>Заминка — по желанию</b>",
        "3–5 мин спокойного дыхания, ходьбы или комфортных движений без боли.",
    ))
    return "\n".join(lines)


async def show_workout_preview(
    message: types.Message,
    user_id: int,
    *,
    edit: bool,
) -> bool:
    """Show the durable, adapted snapshot without advancing execution."""
    try:
        workout = get_active_workout(user_id)
        if workout is None or not _workout_environment_can_change(workout):
            raise WorkoutExecutionError("Workout preview is no longer available.")
    except (WorkoutExecutionError, SQLAlchemyError):
        text = "Нет тренировки, которую можно подготовить. Выберите действие в меню."
        markup = to_menu_mpk()
        available = False
    else:
        text = format_workout_preview(workout)
        markup = workout_preview_mkp()
        available = True

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)
    return available


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
        recommendation = None
        if (
            workout.adaptation_mode == "adaptive"
            and not step.ready_to_complete
            and step.exercise is not None
        ):
            try:
                recommendation = get_progression_recommendation(
                    user_id,
                    step.exercise.id,
                )
            except (ProgressionHistoryError, SQLAlchemyError, ValueError):
                recommendation = None
        text = _format_step(workout, step, recommendation)
        if step.kind == "format_block" and step.format_block is not None:
            state = get_format_state(user_id, step.format_block.id)
            markup = workout_format_mkp(
                state,
                show_replacements=(
                    state.started_at is None
                    and not (
                        workout.plan_source == "user_defined"
                        and workout.adaptation_mode == "strict"
                    )
                ),
                show_environment_change=_workout_environment_can_change(workout),
            )
        else:
            markup = workout_current_mkp(
                ready_to_complete=step.ready_to_complete,
                show_cooldown=step.ready_to_complete,
                show_replacement=(
                    not step.ready_to_complete
                    and step.exercise is not None
                    and step.exercise.planned_exercise_id
                    == step.exercise.selected_exercise_id
                    and not (
                        workout.plan_source == "user_defined"
                        and workout.adaptation_mode == "strict"
                    )
                ),
                show_environment_change=(
                    not step.ready_to_complete
                    and _workout_environment_can_change(workout)
                ),
            )

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)
    return not text.startswith("Нет активной")


def _format_name(value: str) -> str:
    return {
        "amrap": "AMRAP",
        "emom": "EMOM",
        "for_time": "На время",
        "circuit_rounds": "Круговая тренировка",
    }.get(value, value)


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


def _environment_constraint_text(error: WorkoutEnvironmentIncompatibleError) -> str:
    """Expose only deliberate Russian planning constraints to the user."""
    message = str(error)
    if message.startswith("Для силовой тренировки дома"):
        return message
    return "Текущий день нельзя безопасно адаптировать к выбранному месту. Выберите другое место."


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

    if call.data == WORKOUT_START_CALLBACK:
        try:
            active = get_active_workout(user.id)
            if active is not None:
                await state.clear()
                if _workout_environment_can_change(active):
                    await show_workout_preview(call.message, user.id, edit=True)
                else:
                    await show_current_workout(call.message, user.id, edit=True)
                await call.answer()
                return
            environment = get_default_training_environment(user.id)
        except (WorkoutEnvironmentError, SQLAlchemyError):
            await state.clear()
            await call.message.edit_text(
                "Сначала укажите место тренировки в профиле.",
                reply_markup=workout_menu_markup(user.id),
            )
        else:
            await state.clear()
            await call.message.edit_text(
                "🏋️ Подготовка тренировки\n\n"
                f"Сегодня тренируемся: "
                f"{escape(TRAINING_ENVIRONMENT_LABELS[environment])}",
                reply_markup=workout_environment_start_mkp(environment),
            )
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
    except WorkoutEnvironmentIncompatibleError as error:
        await state.clear()
        await call.message.edit_text(
            _environment_constraint_text(error),
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
        active = get_active_workout(user.id)
        if active is not None and _workout_environment_can_change(active):
            await show_workout_preview(call.message, user.id, edit=True)
        else:
            await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data.startswith(WORKOUT_ENVIRONMENT_PREFIX))
async def workout_environment_action(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    parts = (call.data or "").split(":")
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return

    if parts == ["workout", "environment", "choose", "start"]:
        await state.clear()
        await call.message.edit_text(
            "Где тренируемся сегодня?\n\n"
            "Выбор действует только для этой тренировки.",
            reply_markup=workout_environment_choices_mkp("start"),
        )
        await call.answer()
        return
    if parts == ["workout", "environment", "choose", "session"]:
        try:
            workout = get_active_workout(user.id)
            if workout is None or not _workout_environment_can_change(workout):
                raise WorkoutEnvironmentChangeBlockedError("Workout already started.")
        except (WorkoutExecutionError, SQLAlchemyError):
            await call.answer(
                "Место можно сменить только до первого выполненного подхода.",
                show_alert=True,
            )
            return
        await state.clear()
        await call.message.edit_text(
            "Где тренируемся сегодня?\n\n"
            "Изменится только текущая тренировка.",
            reply_markup=workout_environment_choices_mkp("session"),
        )
        await call.answer()
        return
    if (
        len(parts) == 5
        and parts[:3] == ["workout", "environment", "set"]
        and parts[3] in {"start", "session"}
        and parts[4] in TRAINING_ENVIRONMENT_LABELS
    ):
        context, environment = parts[3], parts[4]
        if context == "start":
            await state.clear()
            await call.message.edit_text(
                "🏋️ Подготовка тренировки\n\n"
                f"Сегодня тренируемся: "
                f"{escape(TRAINING_ENVIRONMENT_LABELS[environment])}",
                reply_markup=workout_environment_start_mkp(environment),
            )
            await call.answer()
            return
        try:
            workout = get_active_workout(user.id)
            if workout is None:
                raise WorkoutExecutionError("No active workout.")
            change_workout_environment(user.id, workout.id, environment)
        except WorkoutEnvironmentChangeBlockedError:
            await call.answer(
                "Место можно сменить только до первого выполненного подхода.",
                show_alert=True,
            )
            return
        except WorkoutEnvironmentIncompatibleError as error:
            await call.answer(
                _environment_constraint_text(error),
                show_alert=True,
            )
            return
        except (WorkoutExecutionError, SQLAlchemyError):
            await call.answer("Не удалось сменить место тренировки.", show_alert=True)
            return
        await state.clear()
        await show_workout_preview(call.message, user.id, edit=True)
        await call.answer("Место для этой тренировки изменено.")
        return
    if (
        len(parts) == 4
        and parts[:3] == ["workout", "environment", "start"]
        and parts[3] in TRAINING_ENVIRONMENT_LABELS
    ):
        try:
            get_or_start_workout(user.id, training_environment=parts[3])
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
        except WorkoutEnvironmentIncompatibleError as error:
            await state.clear()
            await call.message.edit_text(
                _environment_constraint_text(error),
                reply_markup=workout_environment_choices_mkp("start"),
            )
        except (WorkoutExecutionError, SQLAlchemyError):
            await state.clear()
            await call.message.edit_text(
                "Не удалось открыть тренировку. Попробуйте ещё раз.",
                reply_markup=workout_menu_markup(user.id),
            )
        else:
            await state.clear()
            await show_workout_preview(call.message, user.id, edit=True)
        await call.answer()
        return

    await call.answer("Это действие устарело.", show_alert=True)


@dp.callback_query(F.data == WORKOUT_PREVIEW_START_CALLBACK)
async def workout_preview_start(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        workout = get_active_workout(user.id)
        if workout is None or not _workout_environment_can_change(workout):
            raise WorkoutExecutionError("Workout preview is unavailable.")
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await call.answer("Этот preview уже устарел. Откройте тренировку снова.", show_alert=True)
        return
    await state.clear()
    await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data == WORKOUT_PREVIEW_BACK_CALLBACK)
async def workout_preview_back(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        workout = get_active_workout(user.id)
        if workout is None or not _workout_environment_can_change(workout):
            raise WorkoutExecutionError("Workout preview is unavailable.")
    except (WorkoutExecutionError, SQLAlchemyError):
        await call.answer("Этот preview уже устарел. Откройте тренировку снова.", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text(
        "Где тренируемся сегодня?\n\nИзменится только текущая тренировка.",
        reply_markup=workout_environment_choices_mkp("session"),
    )
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
                reply_markup=workout_input_mkp(),
            )
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


@dp.callback_query(F.data == WORKOUT_TECHNIQUE_CALLBACK)
async def workout_technique(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы продолжить.", show_alert=True)
        return
    try:
        step = get_current_step(user.id)
        if step.ready_to_complete or step.exercise is None:
            raise WorkoutExecutionError("No current standard exercise.")
        technique = get_workout_exercise_technique(user.id, step.exercise.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
    else:
        await call.message.edit_text(
            f"ℹ️ <b>{escape(step.exercise.selected_exercise_name)}</b>\n\n"
            f"Исходное положение: {escape(technique.start_position)}\n\n"
            f"Движение: {escape(technique.action)}\n\n"
            f"Контроль: {escape(technique.control)}",
            reply_markup=workout_technique_mkp(),
        )
    await call.answer()


@dp.callback_query(F.data == WORKOUT_TECHNIQUE_BACK_CALLBACK)
async def workout_technique_back(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы продолжить.", show_alert=True)
        return
    await state.clear()
    await show_current_workout(call.message, user.id, edit=True)
    await call.answer()


def _replacement_feedback(reason: ReplacementReason) -> str:
    return {
        ReplacementReason.STRICT_MODE: (
            "Эта программа настроена на строгое следование без замен."
        ),
        ReplacementReason.EXERCISE_STARTED: (
            "Упражнение уже начато — заменить его можно на следующей тренировке."
        ),
        ReplacementReason.TIMED_BLOCK_STARTED: (
            "Блок уже начат — замену можно сделать в следующей тренировке."
        ),
        ReplacementReason.UNAVAILABLE: (
            "Сейчас нет безопасной замены для этого упражнения."
        ),
        ReplacementReason.ALREADY_REPLACED: (
            "Для этого упражнения замена уже выбрана."
        ),
    }.get(reason, "Действие устарело. Показываю актуальное состояние.")


async def _replacement_options(
    call: types.CallbackQuery,
    state: FSMContext,
    user_id: int,
    session_exercise_id: int,
) -> None:
    try:
        options = get_replacement_options(user_id, session_exercise_id)
    except ReplacementNotAllowedError as error:
        await state.clear()
        await call.answer(_replacement_feedback(error.reason), show_alert=True)
        return
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await call.answer("Действие устарело. Показываю актуальное состояние.", show_alert=True)
        await show_current_workout(call.message, user_id, edit=True)
        return

    if not options.candidates:
        await state.clear()
        await call.answer("Сейчас нет безопасной замены для этого упражнения.", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(
        f"Чем заменить «{escape(options.current_exercise_name)}»?",
        reply_markup=workout_replacement_mkp(options),
    )
    await call.answer()


@dp.callback_query(F.data == "workout:replace:current")
async def workout_replace_current(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        step = get_current_step(user.id)
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await call.answer("Действие устарело. Показываю актуальное состояние.", show_alert=True)
        await show_current_workout(call.message, user.id, edit=True)
        return
    if step.ready_to_complete or step.exercise is None:
        await state.clear()
        await call.answer("Действие устарело. Показываю актуальное состояние.", show_alert=True)
        await show_current_workout(call.message, user.id, edit=True)
        return
    await _replacement_options(call, state, user.id, step.exercise.id)


@dp.callback_query(F.data.startswith(WORKOUT_REPLACEMENT_PREFIX))
async def workout_replace_action(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    """Use DB state for replacement callbacks; callback data carries only ids."""
    user = User.get(tg_id=call.from_user.id)
    parts = (call.data or "").split(":")
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    if parts == ["workout", "replace", "cancel"]:
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
        await call.answer()
        return
    if (
        len(parts) == 5
        and parts[:3] == ["workout", "replace", "choose"]
        and parts[3].isdecimal()
        and parts[4].isdecimal()
    ):
        try:
            apply_replacement(user.id, int(parts[3]), int(parts[4]))
        except ReplacementNotAllowedError as error:
            await state.clear()
            await call.answer(_replacement_feedback(error.reason), show_alert=True)
            await show_current_workout(call.message, user.id, edit=True)
            return
        except (WorkoutExecutionError, SQLAlchemyError):
            await state.clear()
            await call.answer("Действие устарело. Показываю актуальное состояние.", show_alert=True)
            await show_current_workout(call.message, user.id, edit=True)
            return
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
        await call.answer("Упражнение заменено.")
        return

    if len(parts) == 3 and parts[2].isdecimal():
        await _replacement_options(call, state, user.id, int(parts[2]))
        return

    await state.clear()
    await call.answer("Действие устарело. Показываю актуальное состояние.", show_alert=True)
    await show_current_workout(call.message, user.id, edit=True)


@dp.callback_query(F.data.startswith(WORKOUT_FORMAT_PREFIX))
async def workout_format_action(call: types.CallbackQuery, state: FSMContext) -> None:
    """Apply one ownership-checked, idempotent format action from DB state."""
    user = User.get(tg_id=call.from_user.id)
    parts = (call.data or "").split(":")
    if user is None or len(parts) < 4 or not parts[3].isdecimal():
        await call.answer("Это действие устарело.", show_alert=True)
        return
    action = parts[2]
    block_id = int(parts[3])
    try:
        if action == "start":
            start_format_block(user.id, block_id)
        elif action == "round" and len(parts) == 5 and parts[4].isdecimal():
            record_completed_round(user.id, block_id, int(parts[4]))
        elif action == "emom" and len(parts) == 6 and parts[4].isdecimal() and parts[5] in {"0", "1"}:
            record_emom_minute(user.id, block_id, int(parts[4]), parts[5] == "1")
        elif action == "finish":
            finish_format_block(user.id, block_id)
        else:
            raise WorkoutFormatError("Malformed format action.")
    except (WorkoutExecutionError, WorkoutFormatError, SQLAlchemyError):
        await call.answer("Действие уже изменилось. Показываю актуальное состояние.")
        await state.clear()
        await show_current_workout(call.message, user.id, edit=True)
        return
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
    await message.answer(
        "Сколько повторений выполнено?",
        reply_markup=workout_input_mkp(),
    )


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


@dp.callback_query(F.data == WORKOUT_COOLDOWN_CALLBACK)
async def workout_cooldown(call: types.CallbackQuery, state: FSMContext) -> None:
    """Offer optional calm recovery guidance without changing workout state."""
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.answer("Отправьте /start, чтобы начать.", show_alert=True)
        return
    try:
        active = get_active_workout(user.id)
        if active is None or not get_current_step(user.id, active.id).ready_to_complete:
            raise WorkoutExecutionError("Cooldown is only available after the workout.")
    except (WorkoutExecutionError, SQLAlchemyError):
        await state.clear()
        await call.answer("Это действие устарело. Показываю актуальное состояние.", show_alert=True)
        await show_current_workout(call.message, user.id, edit=True)
        return
    await state.clear()
    await call.message.edit_text(
        "🧘 <b>Заминка — по желанию</b>\n\n"
        "3–5 минут спокойно походите, восстановите дыхание или сделайте "
        "комфортные движения в доступной амплитуде.\n\n"
        "Она не обязательна: можно завершить тренировку сразу.",
        reply_markup=workout_cooldown_mkp(),
    )
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


def _duration_minutes(workout: WorkoutSessionView) -> int | None:
    if workout.finished_at is None:
        return None
    return max(
        0,
        int((workout.finished_at - workout.started_at).total_seconds() // 60),
    )


def _format_history_page(page) -> tuple[str, list[tuple[str, int]]]:
    if not page.workouts:
        return "📊 История тренировок\n\nЗавершённых тренировок пока нет.", []

    rows = []
    buttons = []
    for workout in page.workouts:
        completed_at = workout.finished_at or workout.started_at
        set_count = sum(len(exercise.set_results) for exercise in workout.exercises)
        duration = _duration_minutes(workout)
        duration_text = f" · {duration} мин" if duration is not None else ""
        format_text = ""
        completed_blocks = [block for block in workout.blocks if block.finished_at is not None]
        if completed_blocks:
            format_text = " · " + ", ".join(
                f"{_format_name(block.workout_format)}: "
                + (
                    f"{block.elapsed_seconds} сек"
                    if block.workout_format == "for_time" and block.elapsed_seconds is not None
                    else f"счёт {block.final_score or 0}"
                )
                for block in completed_blocks
            )
        rows.append(
            f"{completed_at.strftime('%d.%m.%Y')}\n"
            f"День {workout.day_number} — {escape(workout.day_title)}\n"
            f"{len(workout.exercises)} упражнений · {set_count} подходов{duration_text}{format_text}"
        )
        buttons.append(
            (f"🔎 {completed_at.strftime('%d.%m')} · День {workout.day_number}", workout.id)
        )
    return "📊 История тренировок\n\n" + "\n\n".join(rows), buttons


async def _show_history_page(
    message: types.Message,
    user_id: int,
    *,
    offset: int,
) -> bool:
    try:
        page = get_workout_history_page(
            user_id,
            offset=offset,
            page_size=HISTORY_PAGE_SIZE,
        )
    except (WorkoutExecutionError, SQLAlchemyError):
        await message.edit_text("Не удалось загрузить историю.", reply_markup=to_menu_mpk())
        return False
    text, buttons = _format_history_page(page)
    await message.edit_text(
        text,
        reply_markup=workout_history_mkp(
            buttons,
            offset=page.offset,
            page_size=page.page_size,
            has_newer=page.has_newer,
            has_older=page.has_older,
        ),
    )
    return True


def _format_weight(weight: float) -> str:
    if weight == 0:
        return "собств. вес"
    return f"{weight:.10f}".rstrip("0").rstrip(".").replace(".", ",")


def format_workout_detail_messages(
    workout: WorkoutSessionView,
    *,
    message_limit: int = DETAIL_MESSAGE_LIMIT,
) -> tuple[str, ...]:
    """Render snapshot results compactly, splitting only between exercises."""
    completed_at = workout.finished_at or workout.started_at
    duration = _duration_minutes(workout)
    duration_text = f" · {duration} мин" if duration is not None else ""
    header = (
        f"🏋️ День {workout.day_number} — {escape(workout.day_title)}\n"
        f"{completed_at.strftime('%d.%m.%Y')}{duration_text}\n\n"
        "Вес × повторы:\n"
    )
    exercise_rows = []
    for exercise in sorted(workout.exercises, key=lambda item: item.exercise_order):
        results = " · ".join(
            f"{_format_weight(result.actual_weight_kg)}×{result.actual_reps}"
            for result in sorted(exercise.set_results, key=lambda item: item.set_number)
        )
        exercise_rows.append(
            f"{escape(exercise.selected_exercise_name)} — {results or 'нет сохранённых подходов'}"
        )
    for block in workout.blocks:
        if block.finished_at is None:
            continue
        if block.workout_format == "emom":
            result = f"{block.completed_minutes} выполнено · {block.missed_minutes} пропущено"
        elif block.workout_format == "for_time":
            result = (
                f"{block.completed_rounds}/{block.target_rounds or block.completed_rounds} кругов"
                f" · {block.elapsed_seconds or 0} сек"
            )
        else:
            result = f"{block.completed_rounds} кругов · счёт {block.final_score or 0}"
        exercise_rows.append(f"{escape(block.title)} ({_format_name(block.workout_format)}) — {result}")

    messages = []
    current = header
    for row in exercise_rows:
        candidate = f"{current}\n{row}" if current else row
        if current != header and len(candidate) > message_limit:
            messages.append(current.rstrip())
            current = row
        else:
            current = candidate
    messages.append(current.rstrip())
    return tuple(messages)


def _parse_history_offset(data: str) -> int | None:
    value = data.removeprefix(WORKOUT_HISTORY_PAGE_PREFIX)
    return int(value) if value.isdecimal() else None


def _parse_history_detail(data: str) -> tuple[int, int] | None:
    values = data.removeprefix(WORKOUT_HISTORY_DETAIL_PREFIX).split(":")
    if len(values) != 2 or not all(value.isdecimal() for value in values):
        return None
    return int(values[0]), int(values[1])


@dp.callback_query(F.data == WORKOUT_HISTORY_CALLBACK)
async def workout_history(call: types.CallbackQuery, state: FSMContext) -> None:
    user = User.get(tg_id=call.from_user.id)
    await state.clear()
    if user is None:
        await call.message.edit_text("Отправьте /start, чтобы начать.")
    else:
        await _show_history_page(call.message, user.id, offset=0)
    await call.answer()


@dp.callback_query(F.data.startswith(WORKOUT_HISTORY_PAGE_PREFIX))
async def workout_history_page(call: types.CallbackQuery, state: FSMContext) -> None:
    offset = _parse_history_offset(call.data or "")
    user = User.get(tg_id=call.from_user.id)
    await state.clear()
    if offset is None or user is None:
        await call.answer("Эта ссылка на историю устарела.", show_alert=True)
        return
    await _show_history_page(call.message, user.id, offset=offset)
    await call.answer()


@dp.callback_query(F.data.startswith(WORKOUT_HISTORY_DETAIL_PREFIX))
async def workout_history_detail(call: types.CallbackQuery, state: FSMContext) -> None:
    parsed = _parse_history_detail(call.data or "")
    user = User.get(tg_id=call.from_user.id)
    await state.clear()
    if parsed is None or user is None:
        await call.answer("Эта тренировка недоступна.", show_alert=True)
        return
    workout_id, offset = parsed
    try:
        workout = get_completed_workout_detail(user.id, workout_id)
    except (WorkoutExecutionError, SQLAlchemyError):
        await call.answer("Эта тренировка недоступна.", show_alert=True)
        return
    messages = format_workout_detail_messages(workout)
    for index, text in enumerate(messages):
        markup = workout_history_detail_mkp(offset) if index == len(messages) - 1 else None
        if index == 0:
            await call.message.edit_text(text, reply_markup=markup)
        else:
            await call.message.answer(text, reply_markup=markup)
    await call.answer()
