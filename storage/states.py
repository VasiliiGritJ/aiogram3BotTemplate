from aiogram.fsm.state import State as FSMState, StatesGroup

async def tryFinish(state):
    try:
        await state.clear()
    except:
        pass

class State(StatesGroup):
    State = FSMState()

class ContactWithDevs(StatesGroup):
    Message = FSMState()


class Onboarding(StatesGroup):
    age = FSMState()
    sex = FSMState()
    height_cm = FSMState()
    weight_kg = FSMState()
    goal = FSMState()
    experience_level = FSMState()
    workouts_per_week = FSMState()
    session_duration_minutes = FSMState()
    limitations = FSMState()
    confirmation = FSMState()


class WorkoutExecution(StatesGroup):
    """Short-lived input state for a durable workout session."""

    awaiting_weight = FSMState()
    awaiting_reps = FSMState()
