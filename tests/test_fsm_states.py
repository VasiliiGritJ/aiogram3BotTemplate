import unittest

from aiogram.fsm.state import State as FSMState

from storage.states import ContactWithDevs, Onboarding, State, UserProgram, WorkoutExecution


class FSMStateDefinitionTests(unittest.TestCase):
    def test_all_declared_states_are_aiogram_fsm_states(self) -> None:
        self.assertIsInstance(State.State, FSMState)
        self.assertIsInstance(ContactWithDevs.Message, FSMState)

        onboarding_states = (
            Onboarding.goal,
            Onboarding.experience_level,
            Onboarding.training_environment,
            Onboarding.workouts_per_week,
            Onboarding.session_duration_minutes,
            Onboarding.age,
            Onboarding.sex,
            Onboarding.height_cm,
            Onboarding.weight_kg,
            Onboarding.limitations,
            Onboarding.confirmation,
        )
        for onboarding_state in onboarding_states:
            with self.subTest(state=onboarding_state):
                self.assertIsInstance(onboarding_state, FSMState)

        self.assertIsInstance(WorkoutExecution.awaiting_weight, FSMState)
        self.assertIsInstance(WorkoutExecution.awaiting_reps, FSMState)
        self.assertIsInstance(UserProgram.choosing_mode, FSMState)
        self.assertIsInstance(UserProgram.awaiting_text, FSMState)
        self.assertIsInstance(UserProgram.awaiting_confirmation, FSMState)


if __name__ == "__main__":
    unittest.main()
