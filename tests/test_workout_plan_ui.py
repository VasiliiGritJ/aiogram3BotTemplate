import unittest

from handlers.markups import (
    onboarding_limitations_mkp,
    profile_mpk,
    start_mkp,
    workout_cancel_confirmation_mkp,
    workout_current_mkp,
    workout_plan_mkp,
)


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

    def test_workout_markups_offer_controlled_actions(self) -> None:
        self.assertEqual(
            ["workout:record_set", "workout:cancel"],
            self.callback_values(workout_current_mkp()),
        )
        self.assertEqual(
            ["workout:complete", "workout:cancel"],
            self.callback_values(workout_current_mkp(ready_to_complete=True)),
        )
        self.assertEqual(
            ["workout:cancel:confirm", "workout:cancel:resume"],
            self.callback_values(workout_cancel_confirmation_mkp()),
        )

    def test_profile_has_edit_action(self) -> None:
        self.assertIn("profile:edit", self.callback_values(profile_mpk()))

    def test_no_limitations_button_uses_normalized_value_flow(self) -> None:
        self.assertEqual(
            ["onboarding:limitations:none"],
            self.callback_values(onboarding_limitations_mkp()),
        )


if __name__ == "__main__":
    unittest.main()
