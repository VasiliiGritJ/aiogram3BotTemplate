from aiogram.fsm.state import StatesGroup, State

async def tryFinish(state):
    try:
        await state.clear()
    except:
        pass

class State(StatesGroup):
    State = State()

class ContactWithDevs(StatesGroup):
    Message = State()


class Onboarding(StatesGroup):
    age = State()
    sex = State()
    height_cm = State()
    weight_kg = State()
    goal = State()
    experience_level = State()
    workouts_per_week = State()
    session_duration_minutes = State()
    limitations = State()
    confirmation = State()
