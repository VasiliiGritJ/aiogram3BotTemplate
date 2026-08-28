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
    goal = FSMState()
    experience_level = FSMState()
    training_environment = FSMState()
    workouts_per_week = FSMState()
    session_duration_minutes = FSMState()
    age = FSMState()
    sex = FSMState()
    height_cm = FSMState()
    weight_kg = FSMState()
    limitations = FSMState()
    confirmation = FSMState()


class WorkoutExecution(StatesGroup):
    """Short-lived input state for a durable workout session."""

    awaiting_weight = FSMState()
    awaiting_reps = FSMState()


class UserProgram(StatesGroup):
    choosing_mode = FSMState()
    awaiting_text = FSMState()
    awaiting_confirmation = FSMState()
