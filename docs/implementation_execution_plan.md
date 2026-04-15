# Implementation Plan: From Listing Repository to Decision Engine

This plan turns the redesign direction into an implementation sequence grounded in the current codebase.

## 0) Current system baseline (what we are redesigning)

- Score calculation is centralized in `web/server.py::_decision_insight_for_location` and currently uses hard-coded component weights and heuristics.
- Commute context already supports single and multi-anchor settings via settings endpoints.
- Apartment detail capture already includes manual units, amenities, subtype, cost details, pros/cons, and visits.
- Persistence patterns already exist for flexible settings (`settings` table) and typed location metadata (`extra_json`).

This is a good foundation: we can refactor without replacing the whole app.

---

## 1) Foundational redesign target

Treat **user preference modeling** as a first-class domain:

1. **Eligibility engine** (hard constraints, pass/fail)
2. **Ranking engine** (weighted soft-preference utilities)
3. **Scenario profiles** (saved constraints + weights + commute probabilities)
4. **Explainability + confidence** (component math + data gaps)

If this had been foundational from the start, score generation would be an explicit pipeline:

`Profile -> Eligibility -> Utilities -> Weighted Score -> Confidence -> Recommendation`

---

## 2) Data model and migration plan

### 2.1 New tables

Add migrations in `db/schema.sql` (and migration strategy in initialization path):

- `profiles`
  - `id`, `name`, `is_active`, `created_at`, `updated_at`
- `profile_constraints`
  - `profile_id`, `max_true_monthly`, `max_expected_commute_mins`, `min_bedrooms`, `must_allow_pets`, `required_subtypes_json`, `required_amenities_json`
- `profile_weights`
  - `profile_id`, `affordability_w`, `commute_w`, `type_fit_w`, `space_w`, `amenities_w`, `proximity_w`, `quality_w`
- `profile_commute_scenarios`
  - `id`, `profile_id`, `name`, `lat`, `lon`, `probability`
- `score_runs`
  - `id`, `apartment_id`, `profile_id`, `score`, `tier`, `confidence`, `eligibility_passed`, `breakdown_json`, `reasons_json`, `computed_at`

### 2.2 Near-term compatibility

- Keep existing `settings.commute_anchors` and old scoring response shape during transition.
- Add profile-backed scoring in parallel and gate by a feature flag setting (`settings.scoring_mode = legacy|profile_v1`).

### 2.3 Data backfill

- Create a default profile from existing behavior (legacy-equivalent weights).
- Convert `commute_anchors` settings into `profile_commute_scenarios` for the default profile.

---

## 3) Scoring engine refactor plan

### 3.1 Code structure (new module boundary)

Create a dedicated scorer package:

- `scoring/contracts.py` → request/response models for scoring
- `scoring/eligibility.py` → hard-constraint checks
- `scoring/utilities.py` → utility functions per component
- `scoring/engine.py` → weighted aggregation + tier + reasons + confidence
- `scoring/explain.py` → explanation strings and labels

Keep `web/server.py` orchestration-only.

### 3.2 Eligibility design (hard constraints)

Eligibility output per apartment:

- `passed: bool`
- `failed_constraints: list[str]`
- `blocking_reason_summary`

Hard constraints to start:

- max true monthly cost
- max expected commute minutes
- minimum bedrooms
- required subtype set (e.g., townhome)
- required amenities

### 3.3 Utility design (soft preferences)

Utility scores are 0–100 and deterministic:

- affordability utility
- expected commute utility (probability-weighted across commute scenarios)
- type-fit utility
- space utility
- amenities utility
- proximity utility
- quality utility

### 3.4 Confidence model

Confidence tier based on data completeness and freshness:

- Missing rent/floor-plan data
- Missing commute anchor coordinates
- Sparse quality signals (no rating/review count)
- stale scrape age

---

## 4) API plan

### 4.1 New profile APIs

Add endpoints in `web/server.py` (or `web/routes/profiles.py` if route split is adopted):

- `GET /api/profiles`
- `POST /api/profiles`
- `PATCH /api/profiles/{id}`
- `POST /api/profiles/{id}/activate`
- `GET /api/profiles/{id}/score-preview`

### 4.2 Scoring payload extension

For apartment payloads in `GET /api/locations` and `GET /api/locations/{id}`:

- keep `decision_insight` key for UI compatibility
- enrich response with:
  - `eligibility`
  - `confidence`
  - `data_gaps`
  - `profile_name`

### 4.3 Transition strategy

- Release profile APIs first, then switch UI controls, then flip default scoring mode.
- Maintain legacy score path until profile parity is validated.

---

## 5) UI/UX implementation plan

### 5.1 New left-panel section: "Decision Profile"

In `web/templates/index.html`:

- active profile selector
- edit profile modal (constraints + weights + commute probabilities)
- "reset to balanced" action

### 5.2 Apartment card and detail enhancements

- Card: show pass/fail badge (`Eligible`/`Blocked`) and confidence indicator.
- Detail panel: explicit failed constraints, component table, and data gaps.
- Comparison table: add row for expected commute and eligibility reasons.

### 5.3 Lower-friction capture

- If a required constraint cannot be evaluated due to missing data, show "Needs data" CTA directly in the card/detail.
- Add quick actions: "add fees", "set subtype", "add amenities".

---

## 6) CLI alignment plan

To keep CLI and web consistent:

- Add profile-aware flags to `commands/show.py` (e.g., `--profile`)
- Add profile management command group (`main.py profile ...`)
- Keep default CLI behavior equivalent to active profile

---

## 7) Quality plan (tests/checks)

### 7.1 Unit tests (new)

- scoring engine deterministic tests
- eligibility boundary tests
- expected commute probability math tests
- confidence tier tests

### 7.2 Integration tests (new)

- `/api/locations` response shape under legacy and profile mode
- profile CRUD + activation flow
- migration/backfill smoke tests

### 7.3 Product acceptance criteria

- Changing profile weights materially changes ranking order.
- Apartments failing must-haves are never labeled "Strong fit".
- Top-ranked apartments have explanation + confidence + no silent assumptions.

---

## 8) Delivery phases and milestone outputs

### Milestone A — Domain foundation (1 PR series)

- schema additions
- profile CRUD DB module (`db/profiles.py`)
- scorer module scaffold
- feature flag for scoring mode

### Milestone B — Score parity + explainability

- profile-driven score engine wired into APIs
- decision_insight compatibility layer
- detail explainability fields

### Milestone C — UI profile controls

- profile selector and editor
- eligibility/confidence badges
- data-gap CTAs

### Milestone D — Commute realism

- probability-weighted scenario inputs
- optional routing API adapter + cache

### Milestone E — Optimization and cleanup

- retire legacy scoring path
- remove dead settings keys
- finalize docs and runbook

---

## 9) Risks and mitigations

- **Risk:** Score instability during migration.
  - **Mitigation:** Run dual-score mode and log deltas before rollout.
- **Risk:** More user inputs increases friction.
  - **Mitigation:** profile wizard with sensible defaults + progressive disclosure.
- **Risk:** External commute API cost/latency.
  - **Mitigation:** cache route times + fallback approximation.

---

## 10) Immediate next implementation PR (recommended)

Start with the smallest foundational slice:

1. Add profile tables and DB helpers.
2. Add read-only `GET /api/profiles` + default profile bootstrap.
3. Introduce `scoring/engine.py` with legacy-equivalent defaults but profile input contract.
4. Keep UI unchanged while returning profile name and confidence skeleton in payload.

This gives a safe vertical slice that preserves behavior while setting up the full redesign.
