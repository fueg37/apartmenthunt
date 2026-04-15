from __future__ import annotations

import os
import tempfile
import unittest

from db.connection import get_db
import db.profiles as profiles_db
from web.server import (
    ProfileConstraintsPatch,
    ProfilePatchRequest,
    ProfileWeightsPatch,
)


class ProfilePatchRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = tmp.name

    async def asyncTearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_patch_constraints_partial_update_preserves_required_lists(self) -> None:
        async with get_db(self.db_path) as db:
            profile = await profiles_db.create_profile(db, "Regression Constraints")
            profile_id = profile["id"]

            await profiles_db.update_profile(
                db,
                profile_id=profile_id,
                constraints={
                    "max_true_monthly": 3000,
                    "max_expected_commute_mins": 45,
                    "min_bedrooms": 2,
                    "required_subtypes": ["townhome"],
                    "required_amenities": ["garage", "gym"],
                },
            )

            payload = ProfilePatchRequest(
                constraints=ProfileConstraintsPatch(max_true_monthly=3200)
            )
            await profiles_db.update_profile(
                db,
                profile_id=profile_id,
                constraints=payload.constraints.model_dump(
                    exclude_unset=True,
                    exclude_none=True,
                ),
            )

            updated = await profiles_db.get_profile_by_id(db, profile_id)

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated["constraints"]["max_true_monthly"], 3200)
        self.assertEqual(updated["constraints"]["required_subtypes"], ["townhome"])
        self.assertEqual(updated["constraints"]["required_amenities"], ["garage", "gym"])

    async def test_patch_weights_partial_update_preserves_other_weights(self) -> None:
        async with get_db(self.db_path) as db:
            profile = await profiles_db.create_profile(db, "Regression Weights")
            profile_id = profile["id"]

            baseline = await profiles_db.get_profile_by_id(db, profile_id)
            self.assertIsNotNone(baseline)
            assert baseline is not None
            baseline_weights = baseline["weights"].copy()

            payload = ProfilePatchRequest(weights=ProfileWeightsPatch(commute=0.42))
            await profiles_db.update_profile(
                db,
                profile_id=profile_id,
                weights=payload.weights.model_dump(
                    exclude_unset=True,
                    exclude_none=True,
                ),
            )

            updated = await profiles_db.get_profile_by_id(db, profile_id)

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated["weights"]["commute"], 0.42)
        for key, value in baseline_weights.items():
            if key == "commute":
                continue
            self.assertEqual(updated["weights"][key], value, msg=f"Weight '{key}' changed")


if __name__ == "__main__":
    unittest.main()
