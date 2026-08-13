# Architecture

## Pipeline flow

```
data/raw/logistics_workbook_raw.xlsx
        │
        ▼
 src/data/ingest.py        (load + normalize column names)
        │
        ▼
 src/data/clean.py         (dedupe, date validation, pincode/amount QA, PII masking)
        │  -> data/processed/shipments_clean.csv
        ▼
 src/features/build_features.py   (AWB, carrier, lane, distance, SLA, attempts, NDR category, EDD flags)
        │  -> data/processed/shipments_features.csv
        ▼
 src/models/edd_risk_model.py     (train RandomForest outcome model + LogisticRegression NDR model)
        │  -> outputs/edd_risk_model.joblib, outputs/model_evaluation.json
        ▼
 src/predictions/predict.py       (score every shipment: risk score, reason, action)
        │  -> outputs/edd_risk_predictions.csv
        ├──────────────┬──────────────────┬──────────────────┬───────────────────┐
        ▼              ▼                  ▼                  ▼                   ▼
 src/alerts_agent  src/lane_engine    src/carrier_engine  src/ndr_agent      src/remittance_agent
 edd_breach_alerts  lane_intelligence  carrier_optimization ndr_recovery       cod_remittance
        │              │  (+ padding recs) │                  │                   │
        │ edd_breach_  │ lane_scorecard.csv,│ carrier_scorecard.csv, │ customer_care_    │ cod_remittance_
        │ alerts.csv,  │ edd_padding_        │ carrier_lane_scorecard.csv,│ notifications.csv│ queue.csv
        │ lane_breach_ │ recommendations.csv │ carrier_mix_recommendations.csv│           │
        │ summary.csv  │                     │                              │           │
        │              │ watchlist_candidate │ (uses lane_scorecard +     │           │
        │              │ feeds carrier_      │  padding recs to flag     │           │
        │              │ optimization.py ────┤  lanes for the watchlist) │           │
        │              │                     ▼                              ▼           │
        │              │           carrier_partner_watchlist.csv  src/ndr_agent/        │
        │              │           (mock "improve or shift        ndr_consolidated_report.py
        │              │            volume" carrier notice)          -> ndr_consolidated_report.csv
        │              │                                             -> ivr_call_sheet.csv
        │              │                                             -> ndr_customer_outreach.csv
        │              │                                             -> ndr_care_team_digest.txt
        │              │                                                    │
        │              │                                                    ▼
        │              │                                          src/ndr_agent/
        │              │                                          ndr_pending_response_export.py
        │              │                                             -> ndr_channel_routing.csv
        │              │                                             -> ndr_pending_response_outreach.xlsx
        │              │                                             (per-channel: IVR/WhatsApp/
        │              │                                              Manual Call/Email pending list)
        └──────────────┴──────────────────┴──────────────────┴───────────────────┘
                                │
                                ▼
                 src/decision_engine/central_decision_engine.py
                       -> outputs/central_action_queue.csv   (breach alerts + padding recs lead the queue)
                       -> outputs/kpi_summary.json
                                │
                 src/decision_engine/root_cause.py
                       -> outputs/root_cause_analysis.md
                                │
                 src/models/intervention_simulator.py
                       -> outputs/intervention_simulation.csv   (85% -> 95% pathway / funnel)
                                │
                 src/decision_engine/dashboard_export.py
                       -> dashboard/dashboard_data.json
                                │
                 dashboard/build_dashboard.py
                       -> dashboard/index.html   (self-contained executive dashboard;
                                                   breach alerts / padding / NDR outreach
                                                   are the primary, above-the-fold sections)
```

Run the whole thing with `python run_pipeline.py`. Every stage also runs
standalone (`python -m src.lane_engine.lane_intelligence`) once its upstream
CSV exists, which is how the automated tests exercise individual modules.

## Primary-objective agents (added on top of the base pipeline)

The dashboard's primary, above-the-fold objective is answering operational
questions directly, rather than leaving a Head of Logistics to infer them
from charts:

- **`src/alerts_agent/edd_breach_alerts.py`** — filters the EDD Risk Agent's
  predictions down to shipments still IN TRANSIT that are High/Medium risk
  and close to (or past) their promised EDD, ranks them P1/P2/P3 by urgency,
  and generates a mock Customer Care Team update + push notification for
  each one. It also rolls this up into a lane-level `lane_breach_summary.csv`
  — "which lanes are about to breach EDD right now" is a first-class output,
  not something you have to derive from a shipment list.
- **`src/lane_engine/lane_intelligence.py::compute_padding_recommendations()`**
  — for every lane whose EDD adherence is below target, checks whether the
  gap is actually explained by transit time (P90 actual transit vs. the
  lane's SLA target). If it is, it recommends a specific number of padding
  days (hard-capped both at a sanity limit and at a realistic per-lane EDD
  ceiling — the promise can never be erratically long) and backtests the
  recommendation (SIMULATED) against historical deliveries. If the gap is
  NOT transit-time-driven, it explicitly recommends zero padding and points
  at NDR/RTO as the real driver — the agent will not silently inflate an SLA
  to make a metric look better. Lanes that can't be honestly fixed even at
  the maximum padding are flagged `watchlist_candidate=True` and handed to
  the carrier watchlist agent below.
- **`src/carrier_engine/carrier_optimization.py::compute_carrier_watchlist()`**
  — the Carrier Partner Improvement / Volume-Shift Watchlist. Flags lanes
  where padding alone can't honestly close the EDD gap, or that are
  chronically underperforming per the lane scorecard, matches each to its
  dominant carrier, and generates a mock "improve within N days or we shift
  volume" outbound notice — built entirely from already-computed lane and
  padding outputs, no new model.
- **`src/ndr_agent/ndr_consolidated_report.py`** — for every shipment with
  an unresolved failed-delivery (NDR) event, i.e. a delivery that could not
  be completed, this produces a manager-level consolidated report, a mock
  digest email to the Customer Care Team, a PII-safe IVR call sheet for an
  outbound-calling team, and mock customer-facing push/email/WhatsApp
  outreach — all asking for the same three things: a landmark, an address
  confirmation, or an alternate phone number.
- **`src/ndr_agent/ndr_recovery.py::assign_ndr_channel()`** — routes each
  open NDR case to exactly one primary outreach channel (IVR / Manual Agent
  Call / Email), gated by severity/attempt-count/case-age so the expensive
  manual-call channel isn't blanket-applied, plus a parallel WhatsApp flag
  for reasons needing customer action. `src/ndr_agent/ndr_pending_response_export.py`
  turns this into a per-channel Excel workbook of customers still pending a
  response — the attachment for the outreach email drafted to the
  responsible team.

## Why this shape

- **Single-responsibility modules.** Each agent (`alerts_agent`, `lane_engine`, `carrier_engine`, `ndr_agent`, `remittance_agent`) is independently runnable, independently testable, and reads/writes plain CSV/JSON — no hidden shared state, no in-memory-only pipeline. This is deliberate: a real ops team needs to be able to re-run the NDR agent hourly without re-training the ML model or re-running the carrier analysis.
- **`config/config.py` centralizes every business rule and threshold** (SLA days, minimum volume for a carrier recommendation, remittance day count, EDD target). Changing the business (different city, different SLA policy, different target) means editing one file, not hunting through the codebase.
- **The decision engine is a thin combinator, not a second brain.** It doesn't re-derive risk or re-score lanes; it reads each agent's already-computed output and ranks/merges them. This keeps the "why did the system recommend X" answer traceable to exactly one upstream agent.
- **The dashboard is a pure presentation layer.** All business logic lives in `src/`; `dashboard_export.py` is the only place that touches both the ML/analytics layer and the presentation layer, and `dashboard/index.html` contains zero business logic beyond client-side filtering of already-computed per-shipment records.
- **Explainability over black-box power.** RandomForest + LogisticRegression were chosen deliberately over a heavier gradient-boosted or deep model — `feature_importances_` and logistic coefficients are directly inspectable by a non-ML stakeholder, which matters more here than squeezing out another 2 points of AUC.

## Data confidence flows through every layer

Every output CSV/JSON carries enough context to trace a number back to
ACTUAL / DERIVED / SYNTHETIC / AI_PREDICTED (see `data_dictionary.md`). The
dashboard's legend strip and the carrier scorecard's `data_confidence`
column are the two places this is most visible to an end user.

## Extensibility

- **New carrier field arrives in production:** delete `_assign_carrier()` in `build_features.py`, ingest the real column in `ingest.py`. No other module changes.
- **New lane/city:** the lane engine and dashboard are fully data-driven — no hardcoded city list beyond the metro-classification set in `config.py`.
- **Retraining:** `src/models/edd_risk_model.py::train()` is idempotent and fast (<2s on this dataset). See `methodology.md` for the recommended retraining cadence.
