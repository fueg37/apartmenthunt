# Foundational Redesign Plan: Apartment Decision Intelligence

## Goal

Turn the app from a location repository into a **profile-driven decision engine** where rankings are personalized, auditable, and actionable.

---

## 1) What exists today (implementation reality)

Current strengths we should preserve:

- Scoring is already centralized in `web/server.py::_decision_insight_for_location`.
- Commute anchors already exist (`/api/settings/commute-anchor` and `/api/settings/commute-anchors`).
- Apartment enrichment inputs already exist (manual units, amenities, subtype, cost details, pros/cons).
- Flexible persistence patterns already exist (`settings` and `locations.extra_json`).

Current gaps causing “arbitrary” outcomes:

- Hard-coded weights inside server logic.
- No hard-constraint gate (must-have vs nice-to-have).
- No persisted scenario profile model.
- No confidence/data-gap surface in recommendations.

---

## 2) Foundational redesign (if built this way from day one)

### Decision pipeline

`Active Profile -> Eligibility Gate -> Utility Components -> Weighted Score -> Confidence -> Recommendation`

### Design principles

1. **Must-have constraints are evaluated before ranking**.
2. **All soft preferences are profile weights** (editable, versioned).
3. **Commute is expected utility across multiple hospital scenarios**.
4. **Every rank has explanation + confidence + data gaps**.
5. **Score changes are diffable over time** (persist score runs).

---

## 3) Concrete implementation plan (what to build now)

## Phase A — Data foundation (schema + DB layer)

### A.1 Add profile domain tables

Update `db/schema.sql` with:

- `profiles`
- `profile_constraints`
- `profile_weights`
- `profile_commute_scenarios`
- `score_runs`

Suggested minimal schema contract:

- `profiles`: `id, name, is_active, created_at, updated_at`
- `profile_constraints`: `profile_id, max_true_monthly, max_expected_commute_mins, min_bedrooms, required_subtypes_json, required_amenities_json`
- `profile_weights`: `profile_id, affordability_w, commute_w, type_fit_w, space_w, amenities_w, proximity_w, quality_w`
- `profile_commute_scenarios`: `id, profile_id, name, lat, lon, probability`
- `score_runs`: `id, apartment_id, profile_id, score, tier, confidence, eligibility_passed, breakdown_json, reasons_json, computed_at`

### A.2 Add DB access modules

Create:

- `db/profiles.py`
- `db/score_runs.py`

Functions to implement first:

- `create_profile(...)`
- `list_profiles(...)`
- `get_active_profile(...)`
- `set_active_profile(profile_id)`
- `upsert_profile_constraints(...)`
- `upsert_profile_weights(...)`
- `replace_profile_commute_scenarios(...)`
- `insert_score_run(...)`

### A.3 Backfill migration path

On startup migration:

1. If no profiles exist, create `Balanced (Default)`.
2. Derive default weights from current hard-coded behavior.
3. Copy `settings.commute_anchors` into `profile_commute_scenarios`.

---

## Phase B — Scoring engine extraction

### B.1 Create scoring package

Add files:

- `scoring/contracts.py`
- `scoring/eligibility.py`
- `scoring/utilities.py`
- `scoring/engine.py`
- `scoring/explain.py`

### B.2 Eligibility gate (hard constraints)

Implement in `scoring/eligibility.py`:

- max true monthly cost
- max expected commute minutes
- minimum bedrooms
- required subtype
- required amenities

Output contract:

```python
{
  "passed": bool,
  "failed_constraints": [str],
  "blocking_reason_summary": str | None
}
```

### B.3 Utility scoring (soft preferences)

Implement utilities (0–100 each):

- affordability
- expected commute (probability-weighted)
- type fit
- space fit
- amenities
- proximity
- quality

Final score formula:

```
final_score = eligibility_multiplier * (
  Σ(weight_i * utility_i) / Σ(weight_i)
)
```

Where `eligibility_multiplier = 1.0` for pass, else 0 (or excluded list).

### B.4 Confidence and data gaps

Compute:

- `confidence`: high / medium / low
- `data_gaps`: missing rent, missing commute coordinates, missing subtype, stale scrape, etc.

---

## Phase C — API integration (without breaking UI)

### C.1 New profile endpoints

Add in `web/server.py`:

- `GET /api/profiles`
- `POST /api/profiles`
- `PATCH /api/profiles/{id}`
- `POST /api/profiles/{id}/activate`
- `GET /api/profiles/{id}/score-preview`

### C.2 Keep response compatibility

In existing apartment payloads (`/api/locations`, `/api/locations/{id}`):

- Keep `decision_insight` key.
- Add:
  - `eligibility`
  - `confidence`
  - `data_gaps`
  - `profile_name`

### C.3 Feature-flag rollout

Use `settings.scoring_mode`:

- `legacy` (existing behavior)
- `profile_v1` (new engine)

Rollout sequence:

1. ship dual path
2. compare score deltas
3. switch default to `profile_v1`
4. retire legacy path

---

## Phase D — UI changes that make this useful (not just more data entry)

### D.1 Decision Profile panel

In `web/templates/index.html` add:

- active profile selector
- edit profile modal (constraints + weights + scenario commute probabilities)
- “reset to balanced” action

### D.2 Decision clarity on cards/details

- Card badges: `Eligible` / `Blocked`, plus confidence tier.
- Detail panel: failed constraints, component breakdown, and next data needed.
- Compare modal: expected commute row and must-have pass/fail row.

### D.3 Reduce workflow friction

When score confidence is low, show targeted quick actions:

- `Add floor plan pricing`
- `Set subtype`
- `Add required amenities`
- `Fill recurring fees`

---

## Phase E — Commute realism

Current haversine drive estimate is acceptable for bootstrap but should be replaced by route-time APIs.

Implementation path:

1. Add routing adapter module (e.g., `services/commute_provider.py`).
2. Cache route results in DB keyed by `(origin, destination, departure_window)`.
3. Use cached route times in commute utility.
4. Fallback to haversine estimate when provider unavailable.

---

## 4) Acceptance criteria (definition of done)

A release is successful when:

1. Changing profile weights changes ranking order predictably.
2. Apartments failing must-haves are never shown as top recommendations.
3. Every recommendation includes explanation + confidence + missing-data prompts.
4. Multi-hospital uncertainty is represented by scenario probabilities, not one arbitrary anchor.
5. Partner can look at the compare view and clearly decide `Tour`, `Hold`, or `Reject`.

---

## 5) Immediate next PR to start implementation

Implement smallest vertical slice:

1. Add profile tables + DB helpers.
2. Add `GET /api/profiles` and default profile bootstrap.
3. Extract new scoring engine with legacy-equivalent defaults.
4. Return `profile_name`, `eligibility`, and `confidence` in apartment payloads (UI can ignore initially).

This keeps risk low while establishing the correct foundation.
