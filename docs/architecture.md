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
        ├──────────────┬──────────────────┬───────────────────┐
        ▼              ▼                  ▼                   ▼
 src/lane_engine   src/carrier_engine  src/ndr_agent      src/remittance_agent
 lane_intelligence  carrier_optimization ndr_recovery       cod_remittance
        │              │                  │                   │
        │ lane_scorecard.csv │ carrier_scorecard.csv,     │ customer_care_       │ cod_remittance_
        │              │ carrier_lane_scorecard.csv,│ notifications.csv     │ queue.csv
        │              │ carrier_mix_recommendations.csv│                    │
        └──────────────┴──────────────────┴───────────────────┘
                                │
                                ▼
                 src/decision_engine/central_decision_engine.py
                       -> outputs/central_action_queue.csv
                       -> outputs/kpi_summary.json
                                │
                 src/decision_engine/root_cause.py
                       -> outputs/root_cause_analysis.md
                                │
                 src/models/intervention_simulator.py
                       -> outputs/intervention_simulation.csv   (85% -> 94% pathway)
                                │
                 src/decision_engine/dashboard_export.py
                       -> dashboard/dashboard_data.json
                                │
                 dashboard/build_dashboard.py
                       -> dashboard/index.html   (self-contained executive dashboard)
```

Run the whole thing with `python run_pipeline.py`. Every stage also runs
standalone (`python -m src.lane_engine.lane_intelligence`) once its upstream
CSV exists, which is how the automated tests exercise individual modules.

## Why this shape

- **Single-responsibility modules.** Each agent (`lane_engine`, `carrier_engine`, `ndr_agent`, `remittance_agent`) is independently runnable, independently testable, and reads/writes plain CSV/JSON — no hidden shared state, no in-memory-only pipeline. This is deliberate: a real ops team needs to be able to re-run the NDR agent hourly without re-training the ML model or re-running the carrier analysis.
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
