import unittest

from aiogram.fsm.state import State as FSMState

from storage.states import ContactWithDevs, Onboarding, State


class FSMStateDefinitionTests(unittest.TestCase):
    def test_all_declared_states_are_aiogram_fsm_states(self) -> None:
        self.assertIsInstance(State.State, FSMState)
        self.assertIsInstance(ContactWithDevs.Message, FSMState)

        onboarding_states = (
            Onboarding.age,
            Onboarding.sex,
            Onboarding.height_cm,
            Onboarding.weight_kg,
            Onboarding.goal,
            Onboarding.experience_level,
            Onboarding.workouts_per_week,
            Onboarding.session_duration_minutes,
            Onboarding.limitations,
            Onboarding.confirmation,
        )
        for onboarding_state in onboarding_states:
            with self.subTest(state=onboarding_state):
                self.assertIsInstance(onboarding_state, FSMState)


if __name__ == "__main__":
    unittest.main()
