from __future__ import annotations

import os
import tempfile
import unittest

from db.connection import get_db
import db.profiles as profiles_db
import db.score_runs as score_runs_db
from models import Apartment
from db.locations import insert


class ScoreRunHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = tmp.name

    async def asyncTearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_save_score_run_if_changed_skips_identical_immediate_recompute(self) -> None:
        async with get_db(self.db_path) as db:
            profile = await profiles_db.create_profile(db, "Test Profile")
            apartment = Apartment(name="A", address="Addr", lat=1.0, lon=1.0)
            await insert(db, apartment)

            first_id = await score_runs_db.save_score_run_if_changed(
                db,
                apartment_id=1,
                profile_id=profile["id"],
                score=88,
                tier="great_fit",
                confidence="high",
                eligibility_passed=True,
                breakdown_json={"affordability": 30},
                reasons_json=["Good value"],
                computed_at="2026-04-15T00:00:00",
            )
            second_id = await score_runs_db.save_score_run_if_changed(
                db,
                apartment_id=1,
                profile_id=profile["id"],
                score=88,
                tier="great_fit",
                confidence="high",
                eligibility_passed=True,
                breakdown_json={"affordability": 30},
                reasons_json=["Good value"],
                computed_at="2026-04-15T00:05:00",
            )

            history = await score_runs_db.list_score_history(db, 1, profile["id"])

        self.assertIsNotNone(first_id)
        self.assertIsNone(second_id)
        self.assertEqual(len(history), 1)

    def test_with_score_history_diffs_marks_changed_fields(self) -> None:
        runs = [
            {
                "id": 2,
                "score": 92,
                "tier": "great_fit",
                "confidence": "high",
                "eligibility_passed": True,
                "breakdown_json": {"affordability": 34},
                "reasons_json": ["Strong commute", "Great value"],
            },
            {
                "id": 1,
                "score": 88,
                "tier": "good_fit",
                "confidence": "medium",
                "eligibility_passed": True,
                "breakdown_json": {"affordability": 30},
                "reasons_json": ["Great value"],
            },
        ]

        annotated = score_runs_db.with_score_history_diffs(runs)

        self.assertEqual(annotated[0]["diff_from_previous"]["score_delta"], 4)
        self.assertTrue(annotated[0]["diff_from_previous"]["tier_changed"])
        self.assertIn("Strong commute", annotated[0]["diff_from_previous"]["added_reasons"])
        self.assertIsNone(annotated[1]["diff_from_previous"])


if __name__ == "__main__":
    unittest.main()
