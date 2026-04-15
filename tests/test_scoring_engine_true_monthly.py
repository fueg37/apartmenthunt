from __future__ import annotations

import unittest

from models import Apartment, Unit
from scoring.engine import score_apartment_profile_v1


class ScoringEngineTrueMonthlyTests(unittest.TestCase):
    def _apartment(self, cost_details: dict | None = None) -> Apartment:
        return Apartment(
            name="Test Apartment",
            address="123 Test St",
            lat=0.0,
            lon=0.0,
            extra={"cost_details": cost_details or {}},
        )

    def test_max_true_monthly_eligibility_uses_all_in_cost(self) -> None:
        apartment = self._apartment(
            {
                "parking_cost": 200,
                "utilities_estimate": 100,
            }
        )
        units = [Unit(floor_plan_name="1x1", bed=1, bath=1.0, price_min=2900)]
        profile = {"constraints": {"max_true_monthly": 3000}}

        result = score_apartment_profile_v1(apartment, units, nearby_locs=[], profile=profile)

        self.assertFalse(result.eligibility["passed"])
        self.assertIn("True monthly cost exceeds $3,000/mo", result.eligibility["failed_constraints"])
        self.assertEqual(result.components["affordability"], 0)
        self.assertEqual(result.eligibility["pricing"]["base_monthly"], 2900)
        self.assertEqual(result.eligibility["pricing"]["true_monthly"], 3200)

    def test_reasons_include_base_and_true_monthly_with_concession_amortization(self) -> None:
        apartment = self._apartment(
            {
                "parking_cost": 100,
                "utilities_estimate": 100,
                "concession_months": 2,
                "lease_term_months": 12,
            }
        )
        units = [Unit(floor_plan_name="2x2", bed=2, bath=2.0, price_min=3000)]
        profile = {"constraints": {"max_true_monthly": 3200}}

        result = score_apartment_profile_v1(apartment, units, nearby_locs=[], profile=profile)

        self.assertIn("Base rent $3,000/mo · True monthly $2,700/mo", result.reasons)
        self.assertEqual(result.components["affordability"], 20)

    def test_reasons_and_payload_include_base_and_true_monthly_without_extra_fees(self) -> None:
        apartment = self._apartment()
        units = [Unit(floor_plan_name="1x1", bed=1, bath=1.0, price_min=2400)]

        result = score_apartment_profile_v1(apartment, units, nearby_locs=[], profile={})

        self.assertIn("Base rent $2,400/mo · True monthly $2,400/mo", result.reasons)
        self.assertEqual(result.eligibility["pricing"]["base_monthly"], 2400)
        self.assertEqual(result.eligibility["pricing"]["true_monthly"], 2400)


if __name__ == "__main__":
    unittest.main()
