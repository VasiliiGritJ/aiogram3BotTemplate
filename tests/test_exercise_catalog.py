import tempfile
import unittest
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from db.migrations import run_migrations
from db.models import Exercise, SqliteSession
from services.exercise_catalog import (
    EQUIPMENT_CATEGORIES,
    EXERCISE_DEFINITIONS,
    EXPERIENCE_LEVELS,
    LEGACY_EXERCISE_CODES,
    MUSCLE_GROUP_LABELS,
    PROGRESSION_TYPES,
    TRAINING_ENVIRONMENTS,
    validate_exercise_definition,
)
from services.workout_plans import ensure_workout_catalog


EXPECTED_LEGACY_IDS = {
    "leg_press": 1,
    "seated_leg_curl": 2,
    "chest_press": 3,
    "lat_pulldown": 4,
    "seated_row": 5,
    "shoulder_press": 6,
    "cable_curl": 7,
    "triceps_pushdown": 8,
    "hip_abduction": 9,
    "calf_raise": 10,
    "back_extension": 11,
    "cable_crunch": 12,
    "barbell_bench_press": 13,
    "dumbbell_bench_press": 14,
    "barbell_back_squat": 15,
}


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


class ExerciseCatalogQualityTests(unittest.TestCase):
    def test_catalog_has_production_size_and_unique_stable_definitions(self) -> None:
        codes = [item.code for item in EXERCISE_DEFINITIONS]
        names = [item.name.strip().casefold() for item in EXERCISE_DEFINITIONS]

        self.assertGreaterEqual(len(EXERCISE_DEFINITIONS), 100)
        self.assertLessEqual(len(EXERCISE_DEFINITIONS), 120)
        self.assertEqual(113, len(EXERCISE_DEFINITIONS))
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(tuple(EXPECTED_LEGACY_IDS), LEGACY_EXERCISE_CODES)

    def test_every_definition_has_valid_consistent_taxonomy(self) -> None:
        for definition in EXERCISE_DEFINITIONS:
            with self.subTest(code=definition.code):
                validate_exercise_definition(definition)
                self.assertTrue(definition.name.strip())
                self.assertNotIn("_", definition.name)
                self.assertNotEqual(definition.code.casefold(), definition.name.casefold())
                self.assertTrue(definition.hint.strip())
                self.assertTrue(definition.technique.start_position.strip())
                self.assertTrue(definition.technique.action.strip())
                self.assertTrue(definition.technique.control.strip())

    def test_bird_dog_keeps_stable_code_with_clear_russian_name(self) -> None:
        definition = next(
            item for item in EXERCISE_DEFINITIONS if item.code == "bird_dog"
        )

        self.assertEqual("bird_dog", definition.code)
        self.assertEqual(
            "Вытягивание противоположных руки и ноги на четвереньках",
            definition.name,
        )
        self.assertIn("четвереньки", definition.technique.start_position)

    def test_required_taxonomy_dimensions_are_covered(self) -> None:
        muscles = {item.primary_muscle_group for item in EXERCISE_DEFINITIONS}
        equipment = {item.equipment for item in EXERCISE_DEFINITIONS}
        environments = {
            environment
            for item in EXERCISE_DEFINITIONS
            for environment in item.environments
        }
        levels = {
            level
            for item in EXERCISE_DEFINITIONS
            for level in item.experience_levels
        }
        progression = {item.progression_type for item in EXERCISE_DEFINITIONS}

        self.assertEqual(set(MUSCLE_GROUP_LABELS), muscles)
        self.assertEqual(EQUIPMENT_CATEGORIES, equipment)
        self.assertEqual(TRAINING_ENVIRONMENTS, environments)
        self.assertEqual(EXPERIENCE_LEVELS, levels)
        self.assertEqual(PROGRESSION_TYPES, progression)

    def test_beginner_pool_prioritizes_stable_movements(self) -> None:
        beginner = [
            item for item in EXERCISE_DEFINITIONS
            if "beginner" in item.experience_levels
        ]
        stable_count = sum(
            item.equipment in {"machine", "cable", "bodyweight"}
            for item in beginner
        )
        complex_barbell_count = sum(
            item.equipment == "barbell"
            and item.movement_pattern in {"squat", "hinge"}
            for item in beginner
        )

        self.assertGreaterEqual(len(beginner), 50)
        self.assertGreater(stable_count, len(beginner) // 2)
        self.assertEqual(0, complex_barbell_count)

    def test_advanced_pool_contains_free_weight_compounds(self) -> None:
        advanced_compounds = {
            item.code
            for item in EXERCISE_DEFINITIONS
            if "advanced" in item.experience_levels
            and item.equipment in {"barbell", "dumbbell"}
            and item.movement_pattern
            in {
                "horizontal_push", "vertical_push", "horizontal_pull",
                "squat", "hinge",
            }
        }

        self.assertTrue(
            {
                "barbell_bench_press",
                "barbell_back_squat",
                "barbell_deadlift",
                "barbell_overhead_press",
                "barbell_bent_over_row",
            }.issubset(advanced_compounds)
        )

    def test_equivalence_groups_have_compatible_primary_taxonomy(self) -> None:
        grouped: dict[str, list] = {}
        for definition in EXERCISE_DEFINITIONS:
            grouped.setdefault(definition.equivalence_group, []).append(definition)

        for group, definitions in grouped.items():
            movements = {item.movement_pattern for item in definitions}
            progression = {item.progression_type for item in definitions}
            with self.subTest(group=group):
                self.assertEqual(1, len(movements))
                self.assertLessEqual(len(progression), 2)

    def test_catalog_persists_taxonomy_and_preserves_legacy_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SqliteSession(sqlite_url(Path(directory) / "catalog.db"))
            try:
                run_migrations(database.engine)
                first = ensure_workout_catalog(database)
                repeated = ensure_workout_catalog(database)
                with database() as session:
                    exercises = session.scalars(
                        select(Exercise).order_by(Exercise.id)
                    ).all()
            finally:
                database.dispose()

        by_code = {item.code: item for item in exercises}
        self.assertEqual(first, repeated)
        self.assertEqual(len(EXERCISE_DEFINITIONS), first.exercises)
        self.assertEqual(EXPECTED_LEGACY_IDS, {
            code: by_code[code].id for code in EXPECTED_LEGACY_IDS
        })
        for definition in EXERCISE_DEFINITIONS:
            stored = by_code[definition.code]
            with self.subTest(code=definition.code):
                self.assertEqual(definition.muscle_group, stored.muscle_group)
                self.assertEqual(definition.equipment, stored.equipment)
                self.assertEqual(
                    ",".join(definition.environments),
                    stored.training_environments,
                )
                self.assertEqual(
                    ",".join(definition.experience_levels),
                    stored.experience_levels,
                )
                self.assertEqual(
                    definition.progression_type,
                    stored.progression_type,
                )

    def test_catalog_has_balanced_muscle_coverage(self) -> None:
        counts = Counter(
            item.primary_muscle_group for item in EXERCISE_DEFINITIONS
        )
        self.assertTrue(all(count >= 5 for count in counts.values()))


if __name__ == "__main__":
    unittest.main()
