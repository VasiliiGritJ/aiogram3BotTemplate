import unittest

from services.exercise_catalog import EXERCISE_DEFINITIONS, exercise_definition_by_code
from services.workout_plans import (
    HOME_PULL_LIMITATION_NOTICE,
    NormalizedProfile,
    WorkoutDurationUnsupportedError,
    estimate_generated_day_minutes,
    generate_program,
    program_archetype,
    supported_session_durations,
    validate_generated_program_quality,
)
from services.workout_progression import ProgressionStrategy


def profile(
    *,
    goal: str = "muscle_gain",
    experience: str = "beginner",
    environment: str = "gym",
    frequency: int = 3,
    duration: int = 60,
) -> NormalizedProfile:
    return NormalizedProfile(
        goal=goal,
        experience_level=experience,
        training_environment=environment,
        workouts_per_week=frequency,
        session_duration_minutes=duration,
        equipment=environment,
        has_limitations=False,
        fallback_notes=(
            (HOME_PULL_LIMITATION_NOTICE,)
            if environment == "home" and goal in {"muscle_gain", "strength"}
            else ()
        ),
    )


class ProgramArchitectureTests(unittest.TestCase):
    def definitions(self, program):
        return [
            exercise_definition_by_code(item.exercise_code)
            for day in program.days
            for item in day.exercises
        ]

    def test_muscle_gain_uses_canonical_archetypes_and_six_day_ppl_variation(self) -> None:
        expected = {
            2: ("Всё тело A", "Всё тело B"),
            3: ("Всё тело A", "Всё тело B", "Всё тело C"),
            4: ("Верх тела A", "Низ тела A", "Верх тела B", "Низ тела B"),
            5: ("Верх тела", "Низ тела", "Жимовой день", "Тяговый день", "Ноги"),
            6: ("Жимовой день A", "Тяговый день A", "Ноги A", "Жимовой день B", "Тяговый день B", "Ноги B"),
        }
        archetypes = {
            2: "Всё тело A/B",
            3: "Всё тело A/B/C",
            4: "Верх/низ тела A/B",
            5: "Верх/низ/жим/тяга/ноги",
            6: "Жим/тяга/ноги A/B",
        }
        for frequency, titles in expected.items():
            with self.subTest(frequency=frequency):
                generated = generate_program(profile(frequency=frequency, duration=30))
                self.assertEqual(archetypes[frequency], program_archetype(profile(frequency=frequency, duration=30)))
                self.assertEqual(titles, tuple(day.title for day in generated.days))
        six_day = generate_program(profile(frequency=6, duration=30))
        codes = [item.exercise_code for day in six_day.days for item in day.exercises]
        self.assertLess(max(codes.count(code) for code in codes), 6)

    def test_core_is_bounded_and_accessories_do_not_repeat_every_day(self) -> None:
        generated = generate_program(profile(frequency=6, duration=30))
        definitions = self.definitions(generated)
        self.assertLessEqual(sum(item.primary_muscle_group == "core" for item in definitions), 2)
        accessory_codes = [
            item.exercise_code
            for day in generated.days
            for item in day.exercises
            if exercise_definition_by_code(item.exercise_code).movement_pattern
            in {"isolation", "scapular_rear_delt"}
        ]
        self.assertTrue(accessory_codes)
        self.assertLess(max(accessory_codes.count(code) for code in accessory_codes), 6)
        relative_strength = generate_program(profile(
            goal="strength", environment="street", experience="advanced",
            frequency=6, duration=30,
        ))
        self.assertLessEqual(
            sum(
                exercise_definition_by_code(item.exercise_code).primary_muscle_group == "core"
                for day in relative_strength.days for item in day.exercises
            ),
            4,
        )

    def test_home_does_not_claim_scapular_work_as_true_pull(self) -> None:
        generated = generate_program(profile(environment="home", frequency=3, duration=60))
        patterns = {
            item.movement_pattern for item in self.definitions(generated)
        }
        self.assertNotIn("horizontal_pull", patterns)
        self.assertNotIn("vertical_pull", patterns)
        self.assertEqual(
            "scapular_rear_delt",
            exercise_definition_by_code("prone_y_raise").movement_pattern,
        )
        home_catalog = [item for item in EXERCISE_DEFINITIONS if "home" in item.environments]
        self.assertTrue(home_catalog)
        self.assertTrue(all(item.equipment == "bodyweight" for item in home_catalog))

    def test_street_uses_genuine_pull_patterns_when_they_are_available(self) -> None:
        generated = generate_program(profile(
            environment="street", experience="advanced", frequency=3, duration=60,
        ))
        patterns = {item.movement_pattern for item in self.definitions(generated)}
        self.assertIn("horizontal_pull", patterns)
        self.assertIn("vertical_pull", patterns)

    def test_strength_is_specific_in_gym_and_relative_at_home_and_street(self) -> None:
        gym = generate_program(profile(goal="strength", experience="advanced", frequency=3))
        gym_codes = {item.exercise_code for day in gym.days for item in day.exercises}
        self.assertTrue({
            "barbell_back_squat", "barbell_bench_press", "barbell_deadlift",
        }.issubset(gym_codes))
        for environment in ("home", "street"):
            with self.subTest(environment=environment):
                generated = generate_program(profile(
                    goal="strength", environment=environment, experience="advanced",
                ))
                self.assertEqual(
                    "Относительная сила с собственным весом",
                    program_archetype(profile(goal="strength", environment=environment)),
                )
                self.assertTrue(all(
                    item.progression_strategy == ProgressionStrategy.BODYWEIGHT_REPS
                    for day in generated.days for item in day.exercises
                ))
                self.assertTrue(all(
                    item.reps_max > 6
                    for day in generated.days for item in day.exercises
                ))

    def test_experience_changes_the_actual_prescription(self) -> None:
        programs = [
            generate_program(profile(experience=experience, environment="home"))
            for experience in ("beginner", "intermediate", "advanced")
        ]
        self.assertEqual(3, len({program.days for program in programs}))

    def test_duration_estimate_grows_materially_between_sixty_and_ninety_minutes(self) -> None:
        standard_profile = profile(duration=60, frequency=2)
        long_profile = profile(duration=90, frequency=2)
        standard = generate_program(standard_profile)
        long = generate_program(long_profile)
        standard_minutes = sum(
            estimate_generated_day_minutes(standard_profile, day) for day in standard.days
        )
        long_minutes = sum(
            estimate_generated_day_minutes(long_profile, day) for day in long.days
        )
        self.assertGreaterEqual(long_minutes, standard_minutes + 20)
        self.assertTrue(all(
            estimate_generated_day_minutes(long_profile, day) <= 100
            for day in long.days
        ))

    def test_taxonomy_corrects_lunge_scapular_semantics_and_beginner_dip_priority(self) -> None:
        self.assertEqual("lunge", exercise_definition_by_code("reverse_lunge").movement_pattern)
        self.assertEqual("scapular_rear_delt", exercise_definition_by_code("prone_y_raise").movement_pattern)
        self.assertEqual("scapular_rear_delt", exercise_definition_by_code("prone_reverse_snow_angel").movement_pattern)
        self.assertEqual(
            "Жим Паллафа в кроссовере",
            exercise_definition_by_code("pallof_press").name,
        )
        beginner_gym = generate_program(profile(frequency=3, duration=60))
        self.assertNotIn(
            "bench_dip",
            {item.exercise_code for day in beginner_gym.days for item in day.exercises},
        )

    def test_outputs_are_deterministic(self) -> None:
        current = profile(goal="fat_loss", environment="functional_gym", experience="intermediate", frequency=5, duration=30)
        self.assertEqual(generate_program(current), generate_program(current))

    def test_duration_support_is_truthful_and_long_home_session_is_rejected(self) -> None:
        home = profile(goal="strength", environment="home", experience="advanced", frequency=2)
        supported = supported_session_durations(
            goal=home.goal,
            experience_level=home.experience_level,
            training_environment=home.training_environment,
            workouts_per_week=home.workouts_per_week,
        )
        self.assertIn(60, supported)
        self.assertNotIn(90, supported)
        with self.assertRaises(WorkoutDurationUnsupportedError):
            generate_program(profile(
                goal="strength", environment="home", experience="advanced",
                frequency=2, duration=90,
            ))

    def test_strength_quality_gate_bounds_sbd_and_deadlift_fatigue(self) -> None:
        current = profile(
            goal="strength", environment="gym", experience="advanced",
            frequency=6, duration=30,
        )
        generated = generate_program(current)
        codes = [item.exercise_code for day in generated.days for item in day.exercises]
        self.assertLessEqual(codes.count("barbell_back_squat"), 2)
        self.assertLessEqual(codes.count("barbell_bench_press"), 3)
        self.assertLessEqual(codes.count("barbell_deadlift"), 1)
        for day in generated.days:
            for position, item in enumerate(day.exercises):
                if item.exercise_code == "barbell_deadlift":
                    self.assertEqual(0, position)
                    self.assertLessEqual(item.reps_max, 6)
        self.assertFalse(validate_generated_program_quality(current, generated))


if __name__ == "__main__":
    unittest.main()
