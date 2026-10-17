"""Telegram view for the deterministic weekly workout plan."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import (
    access_paywall_mkp,
    to_menu_mpk,
    workout_plan_mkp,
    workout_plan_source_mkp,
)
from handlers.onboarding import start_onboarding
from handlers.workout_execution import workout_entry_action
from services.workout_plans import (
    FitnessProfileRequiredError,
    WorkoutPlanError,
    WorkoutPlanNotReadyError,
    assign_workout_plan,
    format_workout_plan,
)
from services.access import AccessStatus, get_access_decision
from services.commercial_product import LAUNCH_COMMERCIAL_PRODUCT
from storage.config import dp
from storage.states import tryFinish


@dp.callback_query(F.data == "workout_plan")
async def workout_plan_call(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    decision = None if user is None else get_access_decision(user.id)
    if decision is not None and not decision.has_access:
        trial_available = decision.status is AccessStatus.TRIAL_AVAILABLE
        product = LAUNCH_COMMERCIAL_PRODUCT
        access_text = (
            f"Начните с {product.trial_days} дней бесплатно — "
            "без карты и автопродления."
            if trial_available
            else (
                "Пробный период завершён. Продолжить можно за "
                f"{product.amount_minor // 100} ₽ на {product.period_days} дней."
            )
        )
        await call.message.edit_text(
            "Для программ и тренировок нужен активный доступ.\n\n" + access_text,
            reply_markup=access_paywall_mkp(
                trial_available=trial_available
            ),
        )
        await call.answer()
        return
    await call.message.edit_text(
        "Как подготовить программу тренировок?",
        reply_markup=workout_plan_source_mkp(),
    )
    await call.answer()


@dp.callback_query(F.data == "workout_plan:generate")
async def generated_workout_plan_call(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await tryFinish(state)
    user = User.get(tg_id=call.from_user.id)
    if user is None:
        await call.message.edit_text(
            "Отправьте /start, чтобы создать профиль.",
            reply_markup=to_menu_mpk(),
        )
        await call.answer()
        return

    decision = get_access_decision(user.id)
    if not decision.has_access:
        await call.message.edit_text(
            "Для составления программы нужен активный доступ.",
            reply_markup=access_paywall_mkp(
                trial_available=decision.status is AccessStatus.TRIAL_AVAILABLE
            ),
        )
        await call.answer()
        return

    try:
        result = assign_workout_plan(user.id)
    except FitnessProfileRequiredError:
        await start_onboarding(call.message, state, edit=True)
        await call.answer()
        return
    except WorkoutPlanNotReadyError as error:
        await call.message.edit_text(
            str(error),
            reply_markup=workout_plan_mkp(),
        )
        await call.answer()
        return
    except (WorkoutPlanError, SQLAlchemyError):
        await call.message.edit_text(
            "Не удалось подготовить план. Попробуйте позже.",
            reply_markup=workout_plan_mkp(),
        )
        await call.answer()
        return

    text = format_workout_plan(result.plan, result.fallback_notes)
    await call.message.edit_text(
        text,
        reply_markup=workout_plan_mkp(workout_entry_action(user.id)),
    )
    await call.answer()
