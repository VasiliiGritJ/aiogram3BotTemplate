import unittest

from handlers.markups import (
    onboarding_duration_mkp,
    onboarding_environment_mkp,
    onboarding_experience_mkp,
    onboarding_frequency_mkp,
    onboarding_goal_mkp,
    onboarding_limitations_mkp,
    profile_mpk,
    start_mkp,
    subscription_mkp,
    workout_cancel_confirmation_mkp,
    workout_current_mkp,
    workout_plan_mkp,
    workout_plan_source_mkp,
    user_program_mode_mkp,
    user_program_preview_mkp,
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
        self.assertIn("subscription", self.callback_values(start_mkp()))

    def test_subscription_markup_uses_only_local_payment_actions(self) -> None:
        markup = subscription_mkp(
            payment_id=12,
            confirmation_url="https://example.invalid/pay",
        )
        self.assertIn("subscription:pay", self.callback_values(markup))
        self.assertIn("subscription:check:12", self.callback_values(markup))
        urls = [
            button.url
            for row in markup.inline_keyboard
            for button in row
            if button.url is not None
        ]
        self.assertEqual(["https://example.invalid/pay"], urls)

    def test_workout_plan_view_can_return_to_menu(self) -> None:
        self.assertEqual(["start"], self.callback_values(workout_plan_mkp()))

    def test_user_can_choose_generated_or_own_program_and_confirm_preview(self) -> None:
        self.assertEqual(
            ["workout_plan:generate", "user_program:start", "start"],
            self.callback_values(workout_plan_source_mkp()),
        )
        self.assertEqual(
            [
                "user_program:mode:strict", "user_program:mode:replacements",
                "user_program:mode:adaptive", "workout_plan",
            ],
            self.callback_values(user_program_mode_mkp()),
        )
        self.assertEqual(
            ["user_program:confirm", "user_program:retry", "workout_plan"],
            self.callback_values(user_program_preview_mkp()),
        )

    def test_workout_markups_offer_controlled_actions(self) -> None:
        self.assertEqual(
            ["workout:technique", "workout:record_set", "workout:cancel"],
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

    def test_stage_seven_onboarding_choices_use_stable_domain_values(self) -> None:
        self.assertEqual(
            [
                "onboarding:goal:muscle_gain",
                "onboarding:goal:strength",
                "onboarding:goal:fat_loss",
            ],
            self.callback_values(onboarding_goal_mkp()),
        )
        self.assertEqual(
            [
                "onboarding:experience:beginner",
                "onboarding:experience:intermediate",
                "onboarding:experience:advanced",
            ],
            self.callback_values(onboarding_experience_mkp()),
        )
        self.assertEqual(
            [
                "onboarding:environment:gym",
                "onboarding:environment:functional_gym",
                "onboarding:environment:street",
                "onboarding:environment:home",
            ],
            self.callback_values(onboarding_environment_mkp()),
        )
        self.assertEqual(
            [f"onboarding:frequency:{value}" for value in (2, 3, 4, 5, 6)],
            self.callback_values(onboarding_frequency_mkp()),
        )
        self.assertEqual(
            [f"onboarding:duration:{value}" for value in (30, 45, 60, 90)],
            self.callback_values(onboarding_duration_mkp()),
        )

    def test_onboarding_can_show_only_truthful_duration_choices(self) -> None:
        self.assertEqual(
            ["onboarding:duration:30", "onboarding:duration:45"],
            self.callback_values(onboarding_duration_mkp((30, 45))),
        )


if __name__ == "__main__":
    unittest.main()
