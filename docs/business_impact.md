# Business Impact

*Numbers below are live pipeline outputs as of the snapshot date (2026-03-05,
2,468 shipments). Re-run `python run_pipeline.py` after any data refresh —
every number here is reproducible from `outputs/*.csv`, never hand-edited.*

## Baseline (ACTUAL)

**84.99% EDD adherence** (1,546 of 1,819 delivered shipments met their
promised date), cross-checked against the workbook's own Validation sheet.
Target: **95%** (raised from 94% in the 2026-08 leadership review) — a
10.0-point gap.

**A note on the target and the SLA that measures it.** The per-lane transit
targets used to score this baseline were revised in 2026-08 to be
distance-proportionate and realistic: Local ≤2 days, Metro 3-4 days,
Regional ≤3 days, National ≤5 days (previously a flatter, unrealistic
schedule). This is a documented SYNTHETIC/policy assumption
(`config.py::TRANSIT_SLA_DAYS` / `MAX_LANE_EDD_CEILING_DAYS`), legitimately
changeable without touching ACTUAL data. What was **not** done: rewriting
historical `delivery_date` values to retroactively fit the new caps. That
was evaluated and rejected — it would have moved the baseline from 84.99% to
~90.1% (93 of 291 "over-cap" shipments flipping from missed→met, zero
flipping the other way), which would silently inflate the very number this
document exists to report honestly. Instead, the realistic caps are enforced
going forward, as a hard ceiling on how far the padding recommender is
allowed to loosen a promise (see "Lane EDD padding recommendations" below).

Alongside the headline number: 454 shipments (18.4% of all) went to RTO, 15
were Lost, 180 are still open/in-transit as of the snapshot, and 37.1% of
all shipments touched at least one NDR event before resolving. COD orders
RTO at **25.0%** vs. **3.0%** for Prepaid — an 8x gap that alone explains a
large share of the EDD-adherence shortfall, since RTO shipments are, by
definition, not on-time deliveries.

## Primary objective — breach prevention & failed-delivery recovery

This is the dashboard's lead section (everything above is charts and
scorecards feeding it). Three action queues, live from the current
snapshot:

**In-transit EDD breach alerts.** 177 open shipments are High/Medium risk
and close to (or past) their promised EDD — 168 P1-Urgent (including
already-past-EDD shipments still moving) and 8 P2-High. Each one has a
mock Customer Care Team queue update and push notification already queued
(see `outputs/edd_breach_alerts.csv`). At the lane level, 4 lanes are
currently flagged "Breach Risk" (Bangalore/Metro, Mumbai/Local, Gurgaon/Metro,
Pune/Metro — see `outputs/lane_breach_summary.csv`).

**Lane EDD padding recommendations.** Of 10 lanes evaluated, 9 get a direct,
transparent padding recommendation (avg. +1.0 day — hard-capped, both at a
sanity limit and at each lane's realistic per-lane EDD ceiling, so no
promise is ever erratically long) because their EDD gap is at least partly
transit-time-driven (P90 actual transit time exceeds the current SLA), up to
+18.3pp projected backtest lift on the best case (Mumbai/Local). 1 lane
(West Delhi/National) gets **zero** recommended padding — its gap traces to
NDR/RTO instead, and padding its SLA would not fix that. 8 of the 9 padded
lanes are *also* flagged on the Carrier Partner Watchlist below, because
even their capped, realistic promise still doesn't cover their P90 actual
transit time — padding alone can't honestly fix them. See
`outputs/edd_padding_recommendations.csv` and `methodology.md` §5 for the
full formula.

**Carrier Partner Improvement / Volume-Shift Watchlist.** 15 lanes
(covering 875 shipments/period) are flagged for a carrier-partner
improvement notice: 8 because their honest, capped EDD promise still can't
cover their actual transit time (`TRANSIT_CEILING_BREACH`), 10 because they
are chronically underperforming with a real gap to target
(`CHRONIC_UNDERPERFORMANCE`, some lanes hit both). Each gets a mock
"improve within 14 days or we shift volume" notice naming the lane's
dominant carrier by shipment share. See `outputs/carrier_partner_watchlist.csv`.

**Undelivered-shipment recovery (NDR).** 62 shipments currently have an
unresolved failed-delivery event, each routed to exactly one primary
outreach channel (plus a parallel WhatsApp where the customer needs to take
an action) so no one is contacted twice for the same case: 27 to IVR
(first-touch, low-complexity), 32 to Manual Agent Call (repeat failures,
high-value COD disputes, or aged past ~36h — the deliberately expensive
channel), and 3 to Email (phone unreachable); 37 of the 62 also get a
parallel WhatsApp. 59 of the 62 (everyone routed to IVR or Manual Agent
Call) are on the outbound call sheet (`outputs/ivr_call_sheet.csv`) with a
PII-safe call script asking for a landmark, address confirmation, or
alternate phone number depending on the reason; a mock digest email is
queued for the Customer Care Team (`outputs/ndr_care_team_digest.txt`); and
matching push/email/WhatsApp outreach is queued for the customers themselves
(`outputs/ndr_customer_outreach.csv`). A per-channel workbook listing only
customers still pending a response —
`outputs/ndr_pending_response_outreach.xlsx` — is the attachment for the
outreach email drafted to the Customer Care Team lead. No real name, phone,
or address is used anywhere in this queue — see the privacy note in
`src/ndr_agent/ndr_consolidated_report.py`.

## Why EDD is being missed

1. **NDR is the leading driver, not carrier or distance.** 37% of all shipments experience at least one NDR event; the top three reasons — Phone not reachable (172), Landmark missing (143), Address issue (143) — are all address/contact-quality problems, not carrier execution failures. This is a customer-data-quality problem before it is a logistics-execution problem.
2. **COD amplifies risk.** COD shipments RTO at 8x the rate of Prepaid (25.0% vs 3.0%). Payment friction (COD payment declined) and rejection-at-doorstep behavior are structurally different risk profiles that a single blended SLA target obscures.
3. **A small number of lanes carry a disproportionate share of the gap.** 5 lanes are flagged "Intervention Required" and 6 more "Deteriorating" (min. 20 shipments each) — see the table below. These lanes combine elevated NDR (40–52%) with elevated RTO (10–32%), and every one of them sits below the 30-shipment threshold ops would normally need before reallocating carrier capacity — meaning they've been below the radar of any lane-level review.

## Worst-performing lanes (min. 20 shipments)

| City | Lane | Volume | EDD% | RTO% | NDR% | Health Score | Status |
|---|---|---|---|---|---|---|---|
| South Delhi | National | 21 | 73.3% | 28.6% | 52.4% | 70.4 | Deteriorating |
| West Delhi | National | 31 | 81.0% | 29.0% | 51.6% | 73.6 | Intervention Required |
| Ahmedabad | Metro | 28 | 78.9% | 32.1% | 39.3% | 74.6 | Intervention Required |
| Ghaziabad | National | 45 | 81.8% | 26.7% | 40.0% | 77.1 | Intervention Required |
| Jaipur | National | 34 | 87.5% | 26.5% | 41.2% | 79.2 | Intervention Required |
| Faridabad | National | 25 | 88.9% | 28.0% | 44.0% | 79.2 | Deteriorating |
| North Delhi | National | 20 | 77.8% | 10.0% | 40.0% | 79.5 | Deteriorating |
| Thane | Local | 49 | 91.7% | 26.5% | 49.0% | 79.8 | Intervention Required |

Full root-cause chains (Problem → Evidence → 5-Why → Action → Expected
Impact) for each: `outputs/root_cause_analysis.md`.

## Carrier recommendation

**Important read-this-first caveat:** the source data has no real carrier
field. Carrier is a documented synthetic overlay (`docs/data_assumptions.md`)
built to demonstrate the full Carrier Optimization Agent methodology end to
end. Applied to *this* dataset, the engine correctly concludes **"no
change" on 3 of 4 lane classes** — the observed gaps (3.7–4.9pp) are not
statistically significant, exactly what should happen when carrier
assignment is independent of outcome. One lane (Regional) crosses the
significance bar: Carrier D at 80.6% EDD adherence (n=39) vs. Carrier A at
94.6% (n=46), a 14.0pp gap at p=0.046. Read as a worked methodology example
rather than a real carrier verdict, the engine's recommendation would be:
*shift ~39 shipments/period from Carrier D to Carrier A on Regional lanes,
effective 2026-03-05, expected +2.55pp lift on that lane* — and, per
`methodology.md`, a production deployment should additionally require this
gap to hold for a second observation period before acting, given the
multiple-comparisons risk of testing 4 lanes at once.

On the (synthetic-carrier, actual-outcome) scorecard: Carrier A leads at
85.8% EDD adherence, Carrier D trails at 83.6% — a 2.2pp spread across the
whole network, consistent with "no dominant carrier problem," which is the
honest finding for this dataset.

## NDR intervention logic

62 currently-open NDR shipments are in the customer-care queue: 28 at
P2-High (reattempt-success probability < 65%, empirical from historical
reason×attempt outcomes) and 34 at P3-Standard. None hit P1-Urgent in the
current snapshot. Each entry carries a specific recommended action (address
clarification, reattempt slot, RTO-prevention escalation) and a deadline
driven by urgency. Full queue: `outputs/customer_care_notifications.csv`.

## COD remittance status

1,170 delivered COD shipments are in the remittance queue; **1,152 are
currently overdue**, representing **₹10,74,096** in carrier-held working
capital past the 2-day remittance SLA. This is a pure rule-trigger (no ML)
and the single highest-value, lowest-effort action in this control tower —
a finance-ops follow-up email queue already exists
(`outputs/cod_remittance_queue.csv`) and needs no further analysis to act
on.

## AI prediction approach

A RandomForest outcome-classifier (explainable via feature importances) is
trained on 2,288 closed shipments and applied to every shipment, including
the 180 still open — this is the "real-time risk scoring" use case: 51 open
shipments are currently flagged High risk, each with a plain-English reason
and a recommended action. See `methodology.md` for the full model spec,
evaluation numbers, and the honest finding that a secondary NDR-propensity
model performs only marginally better than random (AUC≈0.57) given the
features available — flagged rather than hidden.

## Funnel: projected/simulated improvement toward 95%

This is the dashboard's "Funnel to 95% EDD Adherence" chart — every factor,
in order, from the ACTUAL baseline to the target:

| Stage | Type | Cumulative EDD Adherence |
|---|---|---|
| Baseline | ACTUAL | 85.0% |
| + High-risk shipment intervention | PROJECTED | 86.0% |
| + NDR customer-care intervention | PROJECTED | 86.3% |
| + Lane-specific intervention | PROJECTED | 86.7% |
| + Carrier reallocation | PROJECTED | 87.0% |
| + Lane EDD padding (right-sized promise, capped) | PROJECTED | 92.9% |
| + Carrier partner performance enforcement (watchlist) | PROJECTED | 94.5% |
| + Early-risk escalation (residual) | **SIMULATED** | 95.0% |

**Read this honestly:** six bottom-up, individually-justified interventions
get the network from 85.0% to **94.5%** — a defensible, auditable +9.5pp,
the large majority of it from two new stages: realistic, hard-capped lane
padding (+6.2pp — the same backtested `projected_lift_pp` already reported
per lane in `edd_padding_recommendations.csv`, just rolled into the funnel)
and carrier-partner enforcement on the watchlist (+1.6pp, conservatively
assuming only 25% of the gap on watchlisted lanes is recovered, since this
depends on an external commitment, not a system we control directly). The
remaining **~0.5pp to reach 95%** is explicitly labeled SIMULATED, not
PROJECTED — the size of the bet a leadership team would still be making on
sustained systemic investment (real-time SLA-breach alerting at the pickup
stage, hub capacity planning, and the compounding effect of the other six
interventions running continuously over multiple quarters rather than as a
one-time snapshot). Presenting this gap transparently — rather than papering
over it with a bigger assumed recovery rate elsewhere — is the more
credible, and more useful, story for a leadership review. Full formula per
stage: `outputs/intervention_simulation.csv`.

## Limitations (state these upfront in any interview or leadership readout)

- Single-warehouse, ~2 month dataset — no multi-warehouse, no seasonal (festive/peak) pattern to validate against.
- Carrier is synthetic; do not repeat the specific "Carrier D" number outside this repo as if it were a real finding.
- NDR-propensity prediction is weak with available features; needs address-quality data to be trustworthy for proactive (pre-NDR) intervention.
- Most individual lanes are below the reliable-sample-size threshold — 389 of 409 lane×class combinations are marked "Insufficient Sample."
- The residual ~0.5pp SIMULATED gap is an assumption, not a proof; a real rollout should track interventions 1–6 (including the new lane-padding and carrier-watchlist stages) for 4–8 weeks before committing to a 95% timeline.
- Carrier partner enforcement (the watchlist) depends on an external commitment from carrier partners — the 25% assumed recovery rate is deliberately conservative, but this is not a lever the control tower can pull by itself.
