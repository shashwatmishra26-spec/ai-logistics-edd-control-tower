# EDD Control Tower

An end-to-end, working AI-powered logistics control tower for an Indian
D2C e-commerce shipper — built from a raw shipment workbook to a trained ML
risk model, six decision-support agents, a central AI decision engine, an
intervention simulator, and an executive dashboard. Every number is
reproducible from the raw data with one command.

**Business problem:** EDD (Estimated Delivery Date) adherence is stuck at
an **actual baseline of 84.99%**, against a **95% target** (raised from 94%
in the 2026-08 leadership review). This repo answers, with evidence at every
step: *why* shipments miss EDD, *which* shipments are at risk right now,
*which* lanes and carrier allocations need to change, and *how much* of the
85%→95% gap can be closed by which interventions — clearly separating what's
ACTUAL, what's DERIVED, what's a documented SYNTHETIC assumption, what's an
AI_PREDICTED model output, and what's a forward-looking PROJECTED/SIMULATED
scenario. A funnel chart on the dashboard walks through every factor in
order, from the 85.0% baseline to the 95% target.

**The dashboard's primary objective** (everything else is supporting
analytics below it): which lanes are about to breach EDD for shipments
still in transit — with a mock Customer Care Team update and push
notification already queued for each at-risk shipment; a direct, transparent
suggestion of how many days of EDD padding an underperforming lane needs,
hard-capped so the promise never gets erratically long (or an explicit "no,
that's not your problem" when the gap is really NDR/RTO); a **Carrier
Partner Improvement / Volume-Shift Watchlist** for lanes that can't be
honestly fixed by padding alone, or are chronically underperforming — each
with a mock "improve or we shift volume" notice; and, for every delivery
that could not be completed, a consolidated NDR report, an outreach channel
assignment (IVR / WhatsApp / Manual Agent Call / Email, gated by severity so
no customer is contacted twice for the same case), a mock Customer Care Team
digest email, and a PII-safe IVR call sheet for getting a landmark, address
confirmation, or alternate phone number from the customer.

➡️ **Start here for the numbers:** [`docs/business_impact.md`](docs/business_impact.md)
➡️ **Start here for the "how":** [`docs/methodology.md`](docs/methodology.md)
➡️ **Start here for data caveats:** [`docs/data_assumptions.md`](docs/data_assumptions.md)

---

## For business / logistics leadership

Baseline EDD adherence is real, measured, and cross-checked against the
source workbook's own validation numbers — no historical data was adjusted
to make the story look better (see "Why the raw delivery dates were not
rewritten" below). The top three drivers of the 10-point gap to target are
(1) NDR — 37% of shipments touch at least one failed-delivery event, mostly
address/contact-quality issues, not carrier failures; (2) COD orders RTO at
8x the rate of Prepaid; and (3) a small set of lanes (Delhi NCR, Ahmedabad,
Thane) combine elevated NDR and RTO and have historically been too
low-volume to show up on a carrier review. Six concrete, auditable
interventions — proactive outreach on AI-flagged high-risk shipments, active
NDR customer-care follow-up, lane-specific hub/process fixes, one
statistically-supportable carrier reallocation, realistic per-lane EDD
padding (hard-capped, never erratically high), and carrier-partner
performance enforcement on the watchlist — get the network to a defensible
**94.5%**. The remaining ~0.5pp gap to 95% is modeled as a transparent,
explicitly-labeled **simulation**, not a promise — see
[`docs/business_impact.md`](docs/business_impact.md) for the full,
un-sugarcoated breakdown, including what this system gets *wrong* today
(e.g. NDR prediction is weak with the fields available) and what real data
would fix.

**Why the raw delivery dates were not rewritten.** Real-world EDD targets
must be distance-proportionate (Local ≤2d, Metro 3-4d, Regional ≤3d,
National ≤5d) — the original flat SLA targets didn't reflect that, which is
a legitimate, documented SYNTHETIC/policy assumption (`config.py`), safely
changed without touching any ACTUAL data. Literally rewriting historical
`delivery_date` values to fit the new realistic caps was considered and
rejected: it would have shifted the baseline from 84.99% to ~90.1% (93 of
291 "over-cap" shipments flipping from missed→met), invalidating the
"cross-checked, no historical data was adjusted" claim this project is built
on. Instead, the realistic caps are enforced going forward as a hard EDD
ceiling on the padding recommender — and any lane that can't be honestly
served within that ceiling is routed to the Carrier Partner Improvement /
Volume-Shift Watchlist instead of being given a padding number it can't
actually hit.

## For technical interviewers

This is a small (2,468-row), intentionally *incomplete* dataset — no
carrier, AWB, hub, lane, SLA, or attempt-count field exists in the source.
The interesting engineering problem was: how do you build a *credible*
control tower on top of that, without either (a) pretending synthetic data
is real, or (b) refusing to build anything until "real" data shows up?
The answer here is a hard separation of ACTUAL / DERIVED / SYNTHETIC /
AI_PREDICTED at the field level (`docs/data_dictionary.md`), synthetic
fields built from deterministic, reproducible, industry-informed rules
(never `random.random()` — see `docs/data_assumptions.md`), and statistical
rigor (minimum sample sizes, two-proportion z-tests, held-out test-set
evaluation) applied even when it produces an unglamorous "not significant"
result. See [`docs/methodology.md`](docs/methodology.md) for the ML model
spec, the lane health-score formula, and — importantly — an honest
discussion of a false-positive the carrier-significance test produced on
this dataset and why that's the *expected*, correct behavior of a rigorous
test, not a bug.

---

## Architecture

```
Raw workbook -> Ingest -> Clean -> Feature Engineering -> EDD Risk Model
     -> Predictions -> {EDD Breach Alerts, Lane Engine (+ padding recs),
                         Carrier Engine, NDR Agent (+ consolidated report/
                         IVR/outreach), COD Agent}
     -> Central Decision Engine -> Root-Cause Engine -> Intervention Simulator
     -> Dashboard (breach alerts / padding / NDR outreach are primary;
                    trend charts and scorecards are supporting analytics)
```

Full diagram and design rationale: [`docs/architecture.md`](docs/architecture.md).

```
ai-logistics-edd-control-tower/
├── README.md
├── requirements.txt
├── run_pipeline.py              <- run the whole thing
├── config/config.py             <- every business rule/threshold, in one place
├── data/
│   ├── raw/                     <- source workbook (copied in)
│   ├── processed/                <- cleaned + featured CSVs (generated)
│   └── sample/
├── src/
│   ├── data/                    <- ingest.py, clean.py
│   ├── features/                <- build_features.py
│   ├── models/                  <- edd_risk_model.py, intervention_simulator.py
│   ├── predictions/             <- predict.py
│   ├── alerts_agent/             <- edd_breach_alerts.py (in-transit breach detection, primary objective)
│   ├── lane_engine/              <- lane_intelligence.py (lane scorecard + padding recommendations, primary objective)
│   ├── carrier_engine/           <- carrier_optimization.py
│   ├── ndr_agent/                <- ndr_recovery.py, ndr_consolidated_report.py (report/IVR/outreach, primary objective)
│   ├── remittance_agent/         <- cod_remittance.py
│   └── decision_engine/          <- central_decision_engine.py, root_cause.py, dashboard_export.py
├── dashboard/
│   ├── template.html            <- dashboard shell (primary breach/padding/NDR sections + supporting charts, filters, CSS)
│   ├── build_dashboard.py        <- embeds data into template.html -> index.html
│   ├── dashboard_data.json       <- generated data payload
│   └── index.html               <- self-contained executive dashboard (open in a browser)
├── tests/                        <- 87 automated tests, unittest
├── outputs/                       <- every agent's CSV/JSON output (generated)
└── docs/
    ├── architecture.md
    ├── data_dictionary.md
    ├── data_assumptions.md
    ├── methodology.md
    └── business_impact.md
```

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

Requires Python 3.10+. Only pandas, numpy, scikit-learn, openpyxl and
matplotlib are needed — no exotic dependencies, no GPU, no external API
keys.

## Execution

```bash
python run_pipeline.py
```

Runs the full pipeline end to end in under 10 seconds and regenerates every file
under `data/processed/`, `outputs/`, and `dashboard/`. Then open
`dashboard/index.html` directly in a browser (no server needed — the data
is embedded).

Any single stage can also be re-run independently once its upstream CSV
exists, e.g.:

```bash
python -m src.alerts_agent.edd_breach_alerts
python -m src.lane_engine.lane_intelligence
python -m src.carrier_engine.carrier_optimization
python -m src.ndr_agent.ndr_recovery
python -m src.ndr_agent.ndr_consolidated_report
```

### Run the tests

```bash
python -m unittest discover -s tests -v
```

87 tests across data ingestion, cleaning, date math, EDD/NDR/RTO
classification, lane and carrier scoring (including statistical
significance edge cases), the ML model, the prediction pipeline, the
remittance-trigger rule, notification generation, the decision engine, the
EDD breach-alert agent, the lane padding recommender, and the NDR
consolidated-report/IVR/outreach agent — including edge cases (empty
dataframes, zero-volume carriers, duplicate AWBs, invalid pincodes, extreme
outlier amounts, lanes with an unrealistically large SLA gap). All pass;
see `tests/` for the full suite.

## Example outputs

- `outputs/edd_risk_predictions.csv` — every shipment scored with an EDD risk %, reason, and recommended action.
- `outputs/edd_breach_alerts.csv`, `outputs/lane_breach_summary.csv` — in-transit shipments/lanes about to breach EDD, with mock Customer Care Team + push alerts (primary objective).
- `outputs/edd_padding_recommendations.csv` — direct, transparent EDD padding suggestions per lane, or an explicit "not transit-time-driven" verdict (primary objective).
- `outputs/lane_scorecard.csv` — every lane with a transparent Lane Health Score and status.
- `outputs/carrier_mix_recommendations.csv` — carrier reallocation recommendations with z-test confidence.
- `outputs/customer_care_notifications.csv` — the NDR recovery action queue.
- `outputs/ndr_consolidated_report.csv`, `outputs/ivr_call_sheet.csv`, `outputs/ndr_customer_outreach.csv`, `outputs/ndr_care_team_digest.txt`, `outputs/ndr_channel_routing.csv` — undelivered-shipment (NDR) consolidation, PII-safe IVR call sheet, channel-routed (IVR/WhatsApp/Manual Call/Email) customer outreach, and mock Customer Care Team digest (primary objective).
- `outputs/cod_remittance_queue.csv` — the COD remittance follow-up queue (mock emails).
- `outputs/central_action_queue.csv` — everything combined and ranked by priority.
- `outputs/root_cause_analysis.md` — auto-generated 5-Why root-cause chains for the worst lanes.
- `outputs/intervention_simulation.csv` — the auditable 85%→95% pathway (funnel chart on the dashboard).
- `outputs/carrier_partner_watchlist.csv` — lanes flagged for a carrier "improve or we shift volume" notice (primary objective).
- `outputs/ndr_channel_routing.csv`, `outputs/ndr_pending_response_outreach.xlsx` — per-shipment NDR outreach channel assignment (IVR/WhatsApp/Manual Call/Email) and the per-channel "still pending a response" workbook.
- `dashboard/index.html` — the executive dashboard (breach alerts / padding / NDR outreach lead the page; charts and scorecards are supporting analytics further down).

## Business Problem → Architecture → Pipeline → ML → Agents → Decision Engine → Dashboard → Impact

Each of these has a dedicated write-up:

| Topic | Doc |
|---|---|
| Business problem, baseline, target | `docs/business_impact.md` |
| Data pipeline, cleaning, data quality | `docs/data_dictionary.md` |
| What's real vs. assumed, and why | `docs/data_assumptions.md` |
| ML approach, lane/carrier statistics, simulation formulas | `docs/methodology.md` |
| System design, module responsibilities | `docs/architecture.md` |

## Assumptions & Limitations

The full, itemized list is in `docs/data_assumptions.md` and the
"Limitations" section of `docs/business_impact.md`. Headline items: carrier
is a documented synthetic overlay (no carrier field in source data); the
dataset spans ~2 months from a single Mumbai warehouse; most individual
lanes are below a reliable sample-size threshold; and the final 7 points of
the final ~0.5 points of the 85%→95% simulation is an explicitly-labeled
SIMULATED scenario, not a proven result.

## Future Roadmap

- Ingest real carrier, AWB, and hub fields when available; delete the synthetic carrier-assignment function (one-line change, everything downstream is unaffected).
- Add a real pincode-to-lat/long distance table to replace zone-level distance approximation.
- Enrich the NDR-propensity model with address-quality signals (its current AUC≈0.57 is honestly reported as weak).
- Move from single-snapshot carrier significance testing to a rolling 2-period sustained-trend gate (flagged as a risk in `methodology.md`).
- Wire the central action queue into a real ticketing/CRM system instead of CSV output.
- Add a proper backtesting harness once 2+ quarters of production data exist, to validate the intervention simulator's assumed recovery rates against realized outcomes.

## Privacy

Customer name, phone, address are masked/redacted at the cleaning stage
(`src/data/clean.py`) and never appear downstream. A hashed `shipment_uid`
is used in place of any customer-identifying field where a per-shipment key
is needed. See `docs/data_dictionary.md` §2.
