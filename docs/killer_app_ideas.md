# Killer App Ideas: Apartment Hunt as a Decision Copilot

This document proposes ambitious feature ideas and, for each one, explains how to redesign the current system as if the feature had been a foundational assumption from day one.

---

## 1) Decision Copilot (LLM + deterministic rules)

### Vision
A conversational copilot that answers:
- “Which 3 should we tour next weekend?”
- “What’s the single biggest unknown for this apartment?”
- “If hospital B becomes primary, how does ranking change?”

### Foundational redesign
Instead of bolting AI onto cards, create a **Decision Engine API** with structured outputs:
- `recommendations[]`
- `tradeoffs[]`
- `missing_data_actions[]`
- `scenario_delta[]`

The copilot should consume these deterministic objects and only narrate/coach.

### Why this fits existing code
- Reuse `decision_insight` and future profile score artifacts as machine-readable explanation payloads.
- Extend server APIs with recommendation endpoints, not free-form prompt spaghetti.

---

## 2) "Tour Optimizer" mode

### Vision
Given available times, generate optimal tours:
- Cluster apartments geographically
- Minimize driving
- Sequence by confidence gaps (“tour the most uncertain contenders first”)

### Foundational redesign
Treat tours as first-class entities:
- `tour_plans`
- `tour_stops`
- `tour_outcomes`

After each stop, score updates automatically from visit notes + impressions.

### Why this fits existing code
- Reuse visits table and add route-aware plan generation.
- Leverage current map UI and location coordinates.

---

## 3) Regret-Minimization Ranking

### Vision
Don’t just rank by expected score—rank by **lowest expected regret** under uncertainty.

Examples:
- Apartment A is best if Hospital 1 wins.
- Apartment B is robust across all hospitals.

### Foundational redesign
Store and compute scenario distributions natively:
- expected score
- worst-case score
- regret score
- robustness score

Default recommendation = max expected score subject to regret ceiling.

### Why this fits existing code
- Existing multi-anchor commute can become scenario probabilities.
- Existing score breakdown already supports component-level comparisons.

---

## 4) Deal Intelligence (automatic anomaly detection)

### Vision
Show “this is unusually good/bad” insights:
- Price-per-sqft anomaly vs local comps
- Sudden concession changes
- Rating/review trend changes over time

### Foundational redesign
Persist market snapshots and derived features:
- normalized rent index
- concession trend
- neighborhood baseline metrics

Add a `deal_signal` component with confidence.

### Why this fits existing code
- Existing scrape and change-log pipeline is already event-friendly.
- Existing unit data can be transformed into time-series features.

---

## 5) Lifestyle Fit Graph

### Vision
Go beyond rent + commute:
- Gym quality fit
- Grocery quality fit
- Social/quiet preference fit
- Weekend life fit (parks, classes, coffee, etc.)

### Foundational redesign
Move from point-distance heuristics to a **personal utility graph**:
- user preference nodes
- amenity/location nodes
- weighted edges

Apartment utility becomes graph traversal + weighted matching.

### Why this fits existing code
- Existing POI model already has category + weight primitives.
- Existing map and search tools can populate graph nodes incrementally.

---

## 6) "Unknowns First" workflow

### Vision
The app should reduce work, not add it.

Each apartment gets a “next best action”:
- Add floor plan pricing
- Verify pet fees
- Confirm internet options
- Schedule visit

### Foundational redesign
Define a **decision readiness model**:
- readiness score
- blocking unknowns
- expected value of information (EVI)

Sort action queue by highest EVI per minute.

### Why this fits existing code
- Current fields (units, costs, amenities, visits, ISP notes) already map to missing-data checks.

---

## 7) Partner Alignment Mode

### Vision
Each partner gets separate preferences and hard constraints.
Then app computes:
- overlap score
- disagreement reasons
- compromise frontier

### Foundational redesign
Support multi-stakeholder profiles:
- `person_profiles`
- `household_profile`
- negotiation logic (weighted fairness)

Output "Pareto frontier" view of options neither person should reject quickly.

### Why this fits existing code
- New profile architecture can be extended to multi-profile merges.
- Compare table can display per-person utilities side-by-side.

---

## 8) Financial Reality Simulator

### Vision
Compare true cost over 12–24 months:
- rent growth assumptions
- move-in costs
- utility seasonality
- commute fuel/toll/parking
- pet and amenity fees

### Foundational redesign
Treat each apartment as a mini cash-flow model:
- monthly ledger
- uncertainty ranges
- scenario curves

Ranking can optimize for expected total cost and downside risk.

### Why this fits existing code
- Existing `cost_details` and units are the seed for a richer cost engine.

---

## 9) Auto Negotiation Assistant

### Vision
Generate leverage packets:
- comp rents nearby
- concession benchmarks
- personalized offer script

Track outcomes by property manager and strategy.

### Foundational redesign
Add `negotiation_runs` model:
- strategy used
- offer sent
- response quality
- eventual rent delta

Continuously learn what tactics work by property type/area.

### Why this fits existing code
- Scrape + history + notes already capture most required context.

---

## 10) "Move Decision Day" mode (high-stakes final screen)

### Vision
A single decisive screen with:
- top 3 finalists
- confidence + downside risks
- unresolved unknowns
- recommendation and why

### Foundational redesign
Add a finalization workflow:
- `finalists`
- `final_decision`
- `post-mortem` (after move)

Use post-mortem data to tune future scoring defaults.

### Why this fits existing code
- Existing compare view can evolve into decision-day orchestration.

---

## 11) Killer UX principle stack

If this is meant to be addictive/useful, every screen should satisfy:

1. **Tell me what to do next**.
2. **Show me what changed and why**.
3. **Make uncertainty explicit**.
4. **Minimize required typing**.
5. **Support “we” decisions, not just “my” decisions**.

---

## 12) Suggested build order for maximum impact

1. Unknowns First workflow + readiness score
2. Partner Alignment mode
3. Regret/robustness ranking
4. Tour Optimizer
5. Financial Reality Simulator
6. Decision Copilot
7. Deal Intelligence + Negotiation Assistant

This order maximizes usefulness quickly while preserving architectural elegance.
