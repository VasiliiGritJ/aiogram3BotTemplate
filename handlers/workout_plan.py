"""Telegram view for the deterministic weekly workout plan."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import SQLAlchemyError

from db import User
from handlers.markups import to_menu_mpk, workout_plan_mkp
from handlers.onboarding import start_onboarding
from services.workout_plans import (
    FitnessProfileRequiredError,
    PlanSafetyReviewRequired,
    WorkoutPlanError,
    assign_workout_plan,
    format_workout_plan,
)
from storage.config import dp
from storage.states import tryFinish


@dp.callback_query(F.data == "workout_plan")
async def workout_plan_call(
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

    try:
        result = assign_workout_plan(user.id)
    except FitnessProfileRequiredError:
        await start_onboarding(call.message, state, edit=True)
        await call.answer()
        return
    except PlanSafetyReviewRequired as error:
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

    await call.message.edit_text(
        format_workout_plan(result.plan, result.fallback_notes),
        reply_markup=workout_plan_mkp(),
    )
    await call.answer()
