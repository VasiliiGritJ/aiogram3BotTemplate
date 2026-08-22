import types
import unittest

from services.workout_plans import NormalizedProfile, estimate_generated_day_minutes, generate_program
from services.workout_warmup import (
    WarmupExercise,
    build_generated_day_warmup,
    build_session_warmup,
    build_warmup_plan,
)


def profile(*, environment="gym", goal="muscle_gain", duration=60, experience="beginner"):
    return NormalizedProfile(
        goal=goal,
        experience_level=experience,
        training_environment=environment,
        workouts_per_week=3,
        session_duration_minutes=duration,
        equipment=environment,
        has_limitations=False,
        fallback_notes=(),
    )


def warmup_exercise(code, *, identity=None, reps=8, rest=90, strategy="hypertrophy_load_reps"):
    from services.exercise_catalog import exercise_definition_by_code

    definition = exercise_definition_by_code(code)
    return WarmupExercise(
        identity=identity or code,
        name=definition.name,
        code=code,
        primary_muscle_group=definition.primary_muscle_group,
        target_reps_min=reps,
        rest_seconds=rest,
        progression_strategy=strategy,
    )


class WorkoutWarmupTests(unittest.TestCase):
    def test_every_standard_generated_workout_gets_deterministic_environment_compatible_warmup(self):
        for environment in ("gym", "functional_gym", "street", "home"):
            for goal in ("muscle_gain", "strength", "fat_loss"):
                current = profile(environment=environment, goal=goal)
                for day in generate_program(current).days:
                    with self.subTest(environment=environment, goal=goal, day=day.day_number):
                        first = build_generated_day_warmup(current, day)
                        second = build_generated_day_warmup(current, day)
                        self.assertEqual(first, second)
                        self.assertTrue(first.general_preparation)
                        self.assertGreaterEqual(first.estimated_minutes, 3)
                        text = " ".join(
                            item.instruction for item in first.general_preparation
                        ).casefold()
                        if environment in {"home", "street"}:
                            self.assertNotIn("дорожка", text)
                            self.assertNotIn("гребля", text)

    def test_movement_preparation_tracks_session_patterns(self):
        plan = build_warmup_plan(
            environment="gym",
            exercises=(
                warmup_exercise("leg_press"),
                warmup_exercise("barbell_bench_press"),
                warmup_exercise("seated_row"),
            ),
            duration_minutes=60,
        )
        titles = {item.title for item in plan.movement_preparation}
        self.assertIn("Подготовка ног", titles)
        self.assertIn("Подготовка жима", titles)
        self.assertIn("Подготовка тяги", titles)

    def test_heavy_main_receives_more_ramp_preparation_than_isolation(self):
        plan = build_warmup_plan(
            environment="gym",
            exercises=(
                warmup_exercise(
                    "barbell_back_squat", rest=180, strategy="strength_load_reps"
                ),
                warmup_exercise("cable_curl"),
            ),
        )
        heavy = plan.ramp_for("barbell_back_squat")
        isolation = plan.ramp_for("cable_curl")
        self.assertIsNotNone(heavy)
        self.assertEqual(3, heavy.set_count)
        self.assertIsNone(isolation)

    def test_unknown_first_working_weight_never_invents_kilograms(self):
        plan = build_warmup_plan(
            environment="gym",
            exercises=(warmup_exercise("chest_press"),),
        )
        instructions = " ".join(plan.ramp_for("chest_press").instructions)
        self.assertIn("рабочий вес", instructions)
        self.assertNotIn("кг", instructions)

    def test_bodyweight_uses_rehearsal_without_external_weight(self):
        plan = build_warmup_plan(
            environment="street",
            exercises=(warmup_exercise("push_up", reps=10, strategy="bodyweight_reps"),),
        )
        ramp = plan.ramp_for("push_up")
        self.assertEqual("bodyweight_rehearsal", ramp.kind)
        self.assertIn("упрощённый вариант", " ".join(ramp.instructions))
        self.assertNotIn("весом", " ".join(ramp.instructions))

    def test_ramp_up_is_not_working_volume_or_progression_data(self):
        plan = build_warmup_plan(
            environment="gym",
            exercises=(warmup_exercise("barbell_back_squat", strategy="strength_load_reps"),),
        )
        ramp = plan.ramp_for("barbell_back_squat")
        self.assertFalse(hasattr(ramp, "actual_weight_kg"))
        self.assertFalse(hasattr(ramp, "actual_reps"))
        self.assertFalse(hasattr(ramp, "progression_strategy"))

    def test_session_snapshot_warmup_is_restart_safe(self):
        exercise = types.SimpleNamespace(
            id=71,
            selected_exercise_name="Жим ногами",
            selected_exercise_code="leg_press",
            selected_primary_muscle_group="quads",
            selected_target_reps_min=8,
            selected_rest_seconds=90,
            selected_progression_strategy="hypertrophy_load_reps",
            session_block_id=None,
        )
        workout = types.SimpleNamespace(
            effective_training_environment="gym",
            exercises=(exercise,),
            blocks=(),
        )
        self.assertEqual(build_session_warmup(workout), build_session_warmup(workout))

    def test_duration_estimates_include_required_warmup_and_use_longer_budget(self):
        estimates = {}
        for duration in (30, 60, 90):
            current = profile(duration=duration)
            day = generate_program(current).days[0]
            estimates[duration] = estimate_generated_day_minutes(current, day)
            self.assertGreaterEqual(estimates[duration], build_generated_day_warmup(current, day).estimated_minutes)
        self.assertLessEqual(estimates[30], 38)
        self.assertGreater(estimates[60], estimates[30])
        self.assertGreaterEqual(estimates[90], estimates[60] + 15)


if __name__ == "__main__":
    unittest.main()
