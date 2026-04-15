from __future__ import annotations

import math
from typing import Any

from models import Apartment, Gym, PointOfInterest
from scoring.contracts import ScoreResult


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _drive_mins(lat1: float, lon1: float, lat2: float, lon2: float, mph: float = 24.0) -> int:
    r = 6371.0
    la1, lo1 = math.radians(lat1), math.radians(lon1)
    la2, lo2 = math.radians(lat2), math.radians(lon2)
    dlat, dlon = la2 - la1, lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlon / 2) ** 2
    km = r * 2 * math.asin(math.sqrt(a))
    return round(km * 0.621371 / mph * 60)


def _tier(score: int) -> tuple[str, str]:
    if score >= 80:
        return "strong_fit", "Strong fit right now"
    if score >= 65:
        return "promising", "Promising — worth a visit"
    if score >= 50:
        return "watch", "Good but has trade-offs"
    return "speculative", "Needs caution — weak fit under current profile"


def _normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    cleaned = {k: max(0.0, float(v or 0.0)) for k, v in raw.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return {k: 0.0 for k in cleaned}
    return {k: v / total for k, v in cleaned.items()}


def _to_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def compute_true_monthly_cost(base_rent: int | None, cost_details: dict[str, Any] | None) -> dict[str, int] | None:
    """Compute all-in monthly cost from base rent and recurring/apportioned fees."""
    if base_rent is None:
        return None

    details = cost_details or {}
    parking = _to_nonnegative_int(details.get("parking_cost"))
    utilities = _to_nonnegative_int(details.get("utilities_estimate"))
    pet_fee = _to_nonnegative_int(details.get("pet_fee"))
    amenity_fee = _to_nonnegative_int(details.get("amenity_fee"))
    concession_months = _to_nonnegative_int(details.get("concession_months"))
    lease_term_months = _to_nonnegative_int(details.get("lease_term_months"), default=12) or 12

    recurring_fees = parking + utilities + pet_fee + amenity_fee
    concession_discount = int(round(base_rent * concession_months / lease_term_months)) if concession_months > 0 else 0
    true_monthly = max(0, base_rent + recurring_fees - concession_discount)

    return {
        "base_rent": base_rent,
        "true_monthly": true_monthly,
        "recurring_fees": recurring_fees,
        "concession_discount": concession_discount,
        "lease_term_months": lease_term_months,
        "concession_months": concession_months,
    }


def _expected_commute_score(apartment: Apartment, scenarios: list[dict[str, Any]]) -> tuple[float, int | None]:
    if not apartment.lat or not apartment.lon or not scenarios:
        return 50.0, None

    total_p = sum(max(0.0, float(s.get("probability", 1.0))) for s in scenarios) or len(scenarios)
    weighted = 0.0
    expected_mins = 0.0
    for s in scenarios:
        p = max(0.0, float(s.get("probability", 1.0))) / total_p
        mins = _drive_mins(apartment.lat, apartment.lon, float(s["lat"]), float(s["lon"]))
        expected_mins += mins * p
        weighted += _clamp(100.0 - max(0.0, mins - 10) * 5.0) * p
    return weighted, int(round(expected_mins))


def score_apartment_profile_v1(
    apartment: Apartment,
    units: list,
    nearby_locs: list,
    profile: dict[str, Any] | None,
    fallback_anchors: list[dict[str, Any]] | None = None,
) -> ScoreResult:
    profile = profile or {}
    constraints = profile.get("constraints", {})
    weights_raw = profile.get("weights", {})
    commute_scenarios = profile.get("commute_scenarios") or fallback_anchors or []

    priced = [u.price_min for u in units if u.price_min is not None]
    base_monthly = min(priced) if priced else None
    cost_snapshot = compute_true_monthly_cost(base_monthly, (apartment.extra or {}).get("cost_details"))
    true_monthly = cost_snapshot["true_monthly"] if cost_snapshot else base_monthly
    max_bed = max([u.bed for u in units if u.bed is not None], default=None)
    subtype = (apartment.extra or {}).get("subtype")
    amenities = (apartment.extra or {}).get("amenities", [])

    failed: list[str] = []
    max_true_monthly = constraints.get("max_true_monthly")
    if max_true_monthly is not None and true_monthly is not None and true_monthly > max_true_monthly:
        failed.append(f"True monthly cost exceeds ${int(max_true_monthly):,}/mo")
    min_bedrooms = constraints.get("min_bedrooms")
    if min_bedrooms is not None and max_bed is not None and max_bed < min_bedrooms:
        failed.append(f"No floor plan meets {int(min_bedrooms)}+ bedrooms")
    required_subtypes = [s.lower() for s in constraints.get("required_subtypes", [])]
    if required_subtypes and (subtype or "").lower() not in required_subtypes:
        failed.append("Subtype preference not satisfied")
    required_amenities = [a.lower() for a in constraints.get("required_amenities", [])]
    if required_amenities:
        have = {a.lower() for a in amenities}
        missing = [a for a in required_amenities if a not in have]
        if missing:
            failed.append(f"Missing required amenities: {', '.join(missing[:3])}")

    components: dict[str, float] = {}
    reasons: list[str] = []
    data_gaps: list[str] = []

    # affordability
    if base_monthly is None:
        components["affordability"] = 45.0
        data_gaps.append("Missing floor-plan pricing")
    else:
        effective_monthly = float(true_monthly)
        target = float(max_true_monthly or 3500)
        ratio = (effective_monthly - 1800) / max(1.0, target - 1800)
        components["affordability"] = _clamp((1.0 - math.sqrt(max(0.0, ratio))) * 100.0)
        reasons.append(f"Base rent ${base_monthly:,}/mo · True monthly ${true_monthly:,}/mo")

    # commute expected utility
    commute_score, expected_mins = _expected_commute_score(apartment, commute_scenarios)
    components["commute"] = commute_score
    if expected_mins is not None:
        reasons.append(f"Expected commute ~{expected_mins} min")
        max_expected = constraints.get("max_expected_commute_mins")
        if max_expected is not None and expected_mins > max_expected:
            failed.append(f"Expected commute exceeds {int(max_expected)} min")
    else:
        data_gaps.append("Missing commute scenarios")

    # type fit
    type_pref = [s.lower() for s in required_subtypes]
    if type_pref:
        components["type_fit"] = 100.0 if (subtype or "").lower() in type_pref else 20.0
    else:
        components["type_fit"] = 65.0

    # space
    if max_bed is None:
        components["space"] = 50.0
        data_gaps.append("Missing bedroom metadata")
    else:
        components["space"] = _clamp(55.0 + (max_bed - 1) * 20.0)

    # amenities
    components["amenities"] = _clamp((len(amenities) / 8.0) * 100.0)

    # proximity from gym + grocery
    gyms = [l for l in nearby_locs if isinstance(l, Gym) and l.lat and l.lon]
    groceries = [l for l in nearby_locs if isinstance(l, PointOfInterest) and l.lat and l.lon and l.category == "grocery"]
    prox_scores: list[float] = []
    for group in (gyms, groceries):
        if not group or not apartment.lat or not apartment.lon:
            continue
        nearest = min(_drive_mins(apartment.lat, apartment.lon, l.lat, l.lon) for l in group)
        prox_scores.append(_clamp(100.0 - max(0.0, nearest - 5) * 10.0))
    components["proximity"] = sum(prox_scores) / len(prox_scores) if prox_scores else 50.0

    # quality
    if apartment.rating:
        components["quality"] = _clamp((apartment.rating / 5.0) * 100.0)
        reasons.append(f"Rated {apartment.rating:.1f}★")
    else:
        components["quality"] = 55.0
        data_gaps.append("Missing rating data")

    weights = _normalize_weights(
        {
            "affordability": weights_raw.get("affordability", 0.35),
            "commute": weights_raw.get("commute", 0.20),
            "type_fit": weights_raw.get("type_fit", 0.10),
            "space": weights_raw.get("space", 0.10),
            "amenities": weights_raw.get("amenities", 0.10),
            "proximity": weights_raw.get("proximity", 0.10),
            "quality": weights_raw.get("quality", 0.05),
        }
    )
    weighted = sum(components[k] * weights.get(k, 0.0) for k in components)
    score = int(round(_clamp(weighted if not failed else weighted * 0.35)))
    tier, summary = _tier(score)

    confidence = "high"
    if len(data_gaps) >= 2:
        confidence = "low"
    elif len(data_gaps) == 1:
        confidence = "medium"

    return ScoreResult(
        score=score,
        tier=tier,
        summary=summary,
        components={k: int(round(v)) for k, v in components.items()},
        reasons=reasons[:4],
        confidence=confidence,
        data_gaps=data_gaps,
        eligibility={
            "passed": len(failed) == 0,
            "failed_constraints": failed,
            "pricing": {
                "base_monthly": base_monthly,
                "true_monthly": true_monthly,
                "cost_details": cost_snapshot,
            },
        },
    )
