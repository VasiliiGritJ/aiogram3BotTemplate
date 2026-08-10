import unittest

from handlers.markups import profile_mpk, start_mkp, workout_plan_mkp


class WorkoutPlanMarkupTests(unittest.TestCase):
    @staticmethod
    def callback_values(markup) -> list[str | None]:
        return [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]

    def test_main_menu_has_workout_plan_action(self) -> None:
        self.assertIn("workout_plan", self.callback_values(start_mkp()))

    def test_workout_plan_view_can_return_to_menu(self) -> None:
        self.assertEqual(["start"], self.callback_values(workout_plan_mkp()))

    def test_profile_has_edit_action(self) -> None:
        self.assertIn("profile:edit", self.callback_values(profile_mpk()))


if __name__ == "__main__":
    unittest.main()
