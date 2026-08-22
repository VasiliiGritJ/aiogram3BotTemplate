import tempfile
import unittest
from pathlib import Path

from services.generated_program_review import (
    CSV_COLUMNS,
    build_generated_program_review,
    format_review_summary,
    write_generated_program_review,
)


class GeneratedProgramReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.review = build_generated_program_review()

    def test_all_requested_profiles_are_exported_deterministically(self) -> None:
        repeated = build_generated_program_review()
        self.assertEqual(540, self.review.combinations_count)
        self.assertEqual(self.review.rows, repeated.rows)
        self.assertEqual(self.review.validation_failures, repeated.validation_failures)
        self.assertFalse(self.review.validation_failures)
        self.assertTrue(self.review.audit_metrics["deterministic_generation"])
        self.assertEqual(0, self.review.audit_metrics["environment_violations"])
        self.assertEqual(0, self.review.audit_metrics["fake_pull_claims"])
        self.assertEqual(0, self.review.audit_metrics["identical_cross_level_programs"])
        self.assertIsInstance(
            self.review.audit_metrics["profiles_with_exact_repeat_all_six_days"], int,
        )
        arms = self.review.audit_metrics["direct_arm_coverage_profiles"]
        self.assertGreater(arms["street:muscle_gain:biceps"], 0)
        self.assertEqual(0, arms["home:muscle_gain:biceps"])

    def test_rows_contain_only_environment_compatible_beginner_safe_snapshots(self) -> None:
        self.assertTrue(self.review.rows)
        for row in self.review.rows:
            self.assertEqual(set(CSV_COLUMNS), set(row))
            self.assertTrue(row["stable_exercise_id"])
            self.assertTrue(row["display_name"])
            if row["environment"] == "home":
                self.assertEqual("bodyweight", row["equipment"])

    def test_writer_creates_csv_and_summary_only_in_requested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path, summary_path, review = write_generated_program_review(Path(directory))
            self.assertTrue(csv_path.is_file())
            self.assertTrue(summary_path.is_file())
            self.assertTrue((Path(directory) / "stage7_gym_program_review.csv").is_file())
            self.assertTrue((Path(directory) / "stage7_gym_program_review_summary.md").is_file())
            self.assertEqual(540, review.combinations_count)
            self.assertIn("Profile combinations: 540", summary_path.read_text(encoding="utf-8"))
            self.assertIn("Validation failures: 0", format_review_summary(review))


if __name__ == "__main__":
    unittest.main()
