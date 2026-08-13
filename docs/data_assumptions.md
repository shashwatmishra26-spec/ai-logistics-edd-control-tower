# Data Assumptions — SYNTHETIC & DERIVED Fields

The source workbook is a realistic but partial extract of an Indian D2C
e-commerce shipment ledger: one warehouse (Mumbai), COD/Prepaid orders,
dates, EDD, NDR reason, and final status. It has **no carrier, AWB, hub,
lane, distance, SLA target, or attempt-count field** — all of which a real
control tower needs. Every field below was either derived (pure arithmetic
on actual fields — no assumption injected) or synthesized (an
industry-informed, documented, deterministic rule). None of it is randomly
fabricated, and none of it is presented as proprietary data from any named
company.

> Industry-informed synthetic assumption based on common Indian e-commerce
> logistics operating patterns (Amazon India / Flipkart / Shiprocket / Myntra
> style operations, used only as directional reference points — no
> proprietary or company-confidential data is used or claimed).

---

### Carrier

- **Source:** Not present in the raw data at all.
- **Method:** Deterministic SHA-256 hash of `order_id` + lane class, mapped to one of 4 fictional carriers (Carrier A–D) with a lane-conditional weighting (e.g. National lanes skew toward Carrier D) so the dataset exhibits realistic carrier-concentration patterns.
- **Assumption:** 4-carrier network is typical for a mid-size Indian D2C shipper (vs. 1–2 for a very small seller or 6+ for a marketplace-scale operation).
- **Reason:** Needed to demonstrate the Carrier Optimization Agent's full methodology (lane×carrier scorecards, mix recommendations, concentration risk).
- **Critical caveat:** Because the label is assigned independently of the shipment's real outcome, any "Carrier X underperforms" finding computed from this dataset is a **methodology demonstration**, not a real judgment about any carrier. The statistical-significance gating (min. volume, two-proportion z-test, p<0.05 AND gap≥8pp) is built exactly as it would be used in production — which is also why, on this synthetic overlay, most lane×carrier comparisons correctly resolve to "not significant."
- **How to replace with production data:** Ingest the real carrier field from the WMS/OMS; delete `_assign_carrier()` in `src/features/build_features.py`; everything downstream (scorecards, recommendations, dashboard) works unchanged.

### AWB

- **Source:** Not present (only `Order Number` exists).
- **Method:** Deterministic 10-digit hash of `order_id`.
- **Reason:** Every downstream queue (NDR, COD remittance, action queue) needs a shipment-tracking-style identifier distinct from the internal order ID, matching how Indian 3PLs (Delhivery, Ecom Express, Xpressbees, etc.) reference shipments.
- **Replace with:** The carrier's real AWB/tracking number at ingestion.

### Origin/Destination hub, Delivery station

- **Source:** Not present (only warehouse address/city/pincode).
- **Method:** Origin hub is a constant label for the single observed warehouse; destination hub is `"<City> Delivery Station"`.
- **Replace with:** Real hub/facility codes from the carrier's network map.

### Lane class (Local / Metro / Regional / National)

- **Source:** Not present.
- **Method:** Rule-based on `customer_city` + destination pincode zone (first digit): same-city-as-warehouse → Local; in a fixed metro-city list → Metro; same India Speed Post zone as origin (zone 4: Maharashtra/MP/Chhattisgarh/Goa) → Regional; else → National.
- **Assumption:** This 4-tier taxonomy mirrors how Indian logistics operators (Shiprocket, Delhivery, etc.) commonly segment lanes for SLA-setting purposes.
- **Replace with:** The carrier's actual zone/lane master, or a proper pincode-to-lat/long distance calculation.

### Distance (km)

- **Source:** Not present.
- **Method:** Representative road-distance midpoint per India Speed Post zone (first digit of pincode) from Mumbai, with a deterministic ±15% jitter derived from a hash of the pincode (reproducible — not `random.random()`).
- **Assumption:** Zone-level approximation, not haversine-precise geocoding.
- **Reason:** No pincode-to-lat/long lookup table was available offline; zone-level distance is a standard, defensible approximation for lane-tiering and as a monotonic ML feature.
- **Replace with:** A real pincode centroid database (e.g. India Post's pincode directory with lat/long) for haversine or road-network distance.

### SLA targets (pickup / transit / delivery, by lane class)

- **Source:** Not present.
- **Method:** Fixed values in `config/config.py`: pickup 1 day; transit 1/2/4/6 days for Local/Metro/Regional/National.
- **Assumption:** Industry-informed synthetic assumption based on common Indian e-commerce logistics operating patterns — typical D2C SLA commitments for these lane tiers.
- **Reason:** Needed to measure SLA breach and feed the risk model; no SLA field exists in source data.
- **Replace with:** The carrier's contracted SLA matrix.

### Attempt number

- **Source:** Only a single `First Attempt date` exists — no attempt-count field.
- **Method:** Rule-based: no NDR + delivered → 1 attempt; NDR + delivered → 2 (or 3 if the gap between first attempt and delivery exceeds 4 days); RTO → 3 (assumes attempts exhausted per standard 3-attempt RTO policy) unless never attempted; still-open with an NDR → scaled by days since first attempt, capped at 3.
- **Assumption:** 3-attempt-then-RTO is the standard policy used by most Indian 3PLs/D2C brands.
- **Reason:** Attempt count is a strong, standard EDD/RTO risk feature; needed for the ML model and the NDR agent.
- **Replace with:** Real per-attempt scan events from the carrier's tracking API.

### NDR category, RTO reason

- **Source:** `NRD reason` (ACTUAL) exists per shipment; a *category* grouping does not.
- **Method:** The 9 observed reasons are grouped into 5 operational categories (Address Issue, Contact Issue, Customer Availability, Customer Rejection, Payment Issue) via a fixed mapping. `rto_reason` simply reuses the ACTUAL `ndr_reason` for RTO-status rows.
- **Replace with:** Nothing needed — reason text is ACTUAL; only the category grouping is a documented taxonomy choice.

### RTO probability, Lost probability (risk-model outputs)

- **Source:** Not present as raw fields.
- **Method:** `p_rto` / `p_lost` are AI_PREDICTED class probabilities from the trained RandomForestClassifier (`src/models/edd_risk_model.py`), evaluated on a held-out test set (see `outputs/model_evaluation.json`).
- **Replace with:** Nothing — these are legitimately model outputs, not data-entry gaps. Retrain periodically as real outcomes accumulate (see `methodology.md`).

### COD amount

- **Source:** No dedicated "COD amount" field; `package_amount` (ACTUAL) is the only monetary field.
- **Method:** `package_amount` is used directly as the COD-amount proxy for COD orders in the remittance agent.
- **Assumption:** Package amount ≈ order value ≈ COD collectible (true when there's no separate shipping/COD-handling fee line item, which is common in simpler D2C setups).
- **Reason:** No better field exists; documented rather than silently treated as authoritative.
- **Replace with:** A real `cod_amount` field from the OMS if COD-handling fees differ from product price.

### Shipment ageing

- **Method:** `snapshot_date (2026-03-05) - order_date`, in days. DERIVED, not synthetic — pure arithmetic on ACTUAL fields, listed here only because it's frequently confused with a "live" ageing clock. In production this would be computed against `datetime.now()`, not a fixed snapshot.

### Daily EDD Tracker "as of" date (2026-08)

- **Source:** Not a data field — a second, independent reference date, `EDD_TRACKING_AS_OF_DATE = "2026-02-28"` in `config/config.py`.
- **Method:** Used only by `src/alerts_agent/daily_edd_tracker.py` to answer questions framed as "as of end of day" — shipments that breached EDD yesterday, shipments still open and at risk of breaching today, and yesterday's EDD cohort that never received a delivery attempt. Deliberately kept separate from `SNAPSHOT_DATE` (2026-03-05), which anchors every ageing/attempt-count feature and is cross-checked against the source workbook's own Validation sheet — changing `SNAPSHOT_DATE` to "play along" with this exercise would have silently shifted that cross-check and every downstream ML feature.
- **Assumption:** Per a 2026-08 leadership request to view the network from the vantage point of the last day of February 2026. "Breach" in this tracker is intentionally broader than the `edd_missed` column used for the headline EDD-adherence KPI (delivered-late only) — it also counts RTO, Lost, and still-open shipments whose EDD has already passed, since all four honestly mean the promise wasn't kept.
- **Reason:** The underlying dataset only spans Jan 1 – Feb 27, 2026 for order dates, so "yesterday" (27 Feb) and "today" (28 Feb) land inside the real data (49 and 43 shipments were promised EDD on those two dates respectively) rather than requiring any invented rows.
- **Replace with:** `datetime.now()` in production — the "as of" framing is a leadership-requested snapshot, not a permanent design choice.

---

## Fields explicitly NOT fabricated

- **No delivery outcome (Delivered/RTO/Lost/EDD-met) is ever synthesized.** These are 100% ACTUAL, taken from `Delivery date`, `EDD`, and `Current Status` exactly as provided.
- **No historical EDD adherence number is adjusted to make the 85%→95% story look better.** The baseline is computed once, directly from the data, and cross-checked against the workbook's own "Validation" sheet (`1546/1819 = 84.99%`) in `tests/test_model_and_decision_engine.py::test_baseline_matches_validation_sheet`.
- **Amount outliers (e.g. a package_amount of ₹3,60,002) are flagged, never corrected.** We do not guess what the "real" value should have been.

### Why the raw `delivery_date` values were not rewritten to fit realistic SLA caps (2026-08)

A 2026-08 leadership review correctly flagged the original per-lane SLA
targets as unrealistic ("looks very fake") — actual transit time was nearly
flat across Local/Metro/Regional/National (2.3-2.8 days average each),
which doesn't reflect real-world distance differences. The fix that was
**rejected**: rewriting historical `delivery_date` (and therefore
`transit_actual_days`) to retroactively fit new, distance-proportionate
caps. Diagnostics showed this would flip 93 of 291 "over-cap" delivered
shipments from missed→met and zero the other way, moving the baseline from
84.99% to ~90.1% — silently inflating the exact number this project exists
to report honestly, and invalidating the "cross-checked against the
Validation sheet, no historical data was adjusted" claim repeated
throughout the docs.

The fix that was applied instead: `TRANSIT_SLA_DAYS` (the default per-lane
target) and `MAX_LANE_EDD_CEILING_DAYS` (the hard ceiling a padded promise
may never cross) were both updated to realistic, distance-proportionate
values — a legitimate, already-labeled SYNTHETIC/policy change, since these
were always assumptions, never raw data. Combined with a lowered
`MAX_RECOMMENDED_PADDING_DAYS`, this guarantees no customer-facing EDD
promise is ever erratically long, without touching a single ACTUAL
`delivery_date`. Lanes whose real transit time can't be honestly covered
even at the maximum realistic promise are routed to the Carrier Partner
Improvement / Volume-Shift Watchlist instead of being given a padding number
they can't actually hit — see `methodology.md` §§5-6b.
