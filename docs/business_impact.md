# Business Impact

*Numbers below are live pipeline outputs as of the snapshot date (2026-03-05,
2,468 shipments). Re-run `python run_pipeline.py` after any data refresh —
every number here is reproducible from `outputs/*.csv`, never hand-edited.*

## Baseline (ACTUAL)

**84.99% EDD adherence** (1,546 of 1,819 delivered shipments met their
promised date), cross-checked against the workbook's own Validation sheet.
Target: **94%** — a 9.0-point gap.

Alongside the headline number: 454 shipments (18.4% of all) went to RTO, 15
were Lost, 180 are still open/in-transit as of the snapshot, and 37.1% of
all shipments touched at least one NDR event before resolving. COD orders
RTO at **25.0%** vs. **3.0%** for Prepaid — an 8x gap that alone explains a
large share of the EDD-adherence shortfall, since RTO shipments are, by
definition, not on-time deliveries.

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

## Projected/simulated improvement toward 94%

| Stage | Type | Cumulative EDD Adherence |
|---|---|---|
| Baseline | ACTUAL | 85.0% |
| + High-risk shipment intervention | PROJECTED | 86.0% |
| + NDR customer-care intervention | PROJECTED | 86.3% |
| + Lane-specific intervention | PROJECTED | 86.7% |
| + Carrier reallocation | PROJECTED | 87.0% |
| + Early-risk escalation (residual) | **SIMULATED** | 94.0% |

**Read this honestly:** the four bottom-up, individually-justified
interventions get the network from 85.0% to **87.0%** — a defensible,
auditable +2.0pp. The remaining **7.0pp to reach 94%** is explicitly labeled
SIMULATED, not PROJECTED — it is the size of the bet a leadership team would
still be making on sustained systemic investment (real-time SLA-breach
alerting at the pickup stage, hub capacity planning, and the compounding
effect of the other four interventions running continuously over multiple
quarters rather than as a one-time snapshot). Presenting this gap
transparently — rather than papering over it with a bigger assumed recovery
rate on interventions 1–4 — is the more credible, and more useful, story
for a leadership review.

## Limitations (state these upfront in any interview or leadership readout)

- Single-warehouse, ~2 month dataset — no multi-warehouse, no seasonal (festive/peak) pattern to validate against.
- Carrier is synthetic; do not repeat the specific "Carrier D" number outside this repo as if it were a real finding.
- NDR-propensity prediction is weak with available features; needs address-quality data to be trustworthy for proactive (pre-NDR) intervention.
- Most individual lanes are below the reliable-sample-size threshold — 389 of 409 lane×class combinations are marked "Insufficient Sample."
- The residual 7pp SIMULATED gap is an assumption, not a proof; a real rollout should track intervention 1–4 results for 4–8 weeks before committing to a 94% timeline.
