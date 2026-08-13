# Data Dictionary

Every field that flows through the pipeline, classified as:

- **ACTUAL** — present in the source workbook (`data/raw/logistics_workbook_raw.xlsx`, "Raw Data" sheet), used as-is.
- **DERIVED** — computed deterministically from ACTUAL fields (date math, boolean flags, groupby aggregates). No assumption is injected; re-running the arithmetic on the same ACTUAL fields always reproduces the same value.
- **SYNTHETIC** — does not exist in the source data at all. Generated from an industry-informed, documented rule (never randomly). Full rationale for every SYNTHETIC field is in [`data_assumptions.md`](data_assumptions.md).
- **AI_PREDICTED** — output of a trained ML model (`src/models/edd_risk_model.py`). Only ever applies to future/uncertain outcomes, never restates a known ACTUAL outcome.
- **SIMULATED** — a forward-looking scenario computed from ACTUAL/DERIVED history but not a realized outcome (e.g. "if this lane's SLA were padded by N days, what fraction of past deliveries would have met it"). Never presented as a proven future result.
- **MOCK** — generated message *content* (a push notification, email, WhatsApp message, IVR script, or Customer Care Team ticket) that is never actually sent anywhere. Used only for the outbound-communication fields in the breach-alert and NDR-outreach agents; the underlying risk score or trigger driving the message may itself be ACTUAL/DERIVED/AI_PREDICTED — MOCK describes the delivery channel, not the logic.

## 1. Source workbook fields (as received)

| Raw column | Cleaned column | Confidence | Notes |
|---|---|---|---|
| Order Number | `order_id` | ACTUAL | Unique per shipment; used as the join key everywhere. Standard order ID also doubles as the closest analogue to a shipment reference in the raw file — there is no separate AWB. |
| Customer Name | *(dropped, replaced by `customer_name_masked`)* | ACTUAL → masked | PII. Never persisted downstream. |
| Customer Phone | *(dropped, replaced by `customer_phone_masked`)* | ACTUAL → masked | PII. Last 4 digits retained (masked) for support-agent lookups. |
| Customer Address | *(dropped, replaced by `customer_address_masked`)* | ACTUAL → masked | PII. Replaced with `[REDACTED - <city>]`. |
| Customer City | `customer_city` | ACTUAL | Used for lane/geography derivation. |
| Customer Pincode | `customer_pincode` | ACTUAL | 1 row had a city name ("Rajkot") instead of a 6-digit pincode — flagged, nulled, not guessed. |
| Package amount | `package_amount` | ACTUAL | Also used as the COD-amount proxy for COD orders (see assumptions doc). |
| Product SKU | `product_sku` | ACTUAL | Not used in modeling (2,240 near-unique SKUs, too sparse to be predictive at this volume). |
| Warehouse Address | `warehouse_address` | ACTUAL | Single warehouse (Bhiwandi, Mumbai) across the whole dataset. |
| warehouse Pincode | `warehouse_pincode` | ACTUAL | Constant: 400016. |
| Warehouse City | `warehouse_city` | ACTUAL | Constant: Mumbai. |
| Order Date | `order_date` | ACTUAL | |
| Pickup date | `pickup_date` | ACTUAL | |
| First Attempt date | `first_attempt_date` | ACTUAL | Null for shipments never attempted (still in transit / pre-attempt RTO). |
| Delivery date | `delivery_date` | ACTUAL | Null for RTO / Lost / still-open shipments — this is operationally meaningful, not missing data. |
| EDD | `edd` | ACTUAL | The promised delivery date — the core KPI target. |
| NRD reason | `ndr_reason` | ACTUAL | Null → filled with `"Not Applicable"` (63% of rows; only NDR-touched shipments carry a reason). |
| Payment Mode | `payment_mode` | ACTUAL | COD / Prepaid. |
| Current Status | `current_status` | ACTUAL | Delivered / RTO / Lost / In-Transit / Out of delivery / Pickup done / Order Packed. |

## 2. Cleaning-layer fields (`src/data/clean.py`)

| Field | Confidence | Definition |
|---|---|---|
| `shipment_uid` | DERIVED | SHA-256 hash of `order_id`, truncated — a safe, non-reversible shipment identifier usable in logs/UI instead of the raw order number. |
| `customer_name_masked`, `customer_phone_masked`, `customer_address_masked` | DERIVED (masking of ACTUAL) | PII-safe representations. |
| `dq_flags` | DERIVED | Semicolon-joined list of data-quality issues detected on that row (`duplicate_order_id_dropped`, `invalid_customer_pincode`, `likely_data_entry_error_amount`, `pickup_before_order_date`, `delivery_before_pickup_date`, `not_yet_attempted`, `high_value_order`). |
| `data_confidence_core` | DERIVED | Always `"ACTUAL"` — a marker for downstream code distinguishing this row set from feature-engineered SYNTHETIC additions. |

## 3. Feature-engineering fields (`src/features/build_features.py`)

| Field | Confidence | Definition |
|---|---|---|
| `awb` | SYNTHETIC | Deterministic hash-based 10-digit AWB (no AWB in source). |
| `origin_hub` | SYNTHETIC | Constant "Mumbai Bhiwandi FC" (only warehouse in the data). |
| `destination_hub` | SYNTHETIC | `"<City> Delivery Station"` — a plausible last-mile station name. |
| `carrier` | SYNTHETIC | No carrier field exists. Deterministic hash-based assignment across 4 fictional carriers (A–D), with a mild lane-conditional volume skew for realistic concentration patterns. **Independent of the shipment's real outcome** — see `data_assumptions.md` for the full caveat before trusting any carrier-level number. |
| `dest_zone` | DERIVED | First digit of `customer_pincode` (India Speed Post zone). |
| `lane_class` | DERIVED (rule) | Local / Metro / Regional / National — rule-based on city + zone (see assumptions doc). |
| `distance_km` | SYNTHETIC | Zone-level representative distance from Mumbai with deterministic pincode-hash jitter. Not haversine-precise. |
| `lane` | DERIVED | `"<City> <- Mumbai"` display label. |
| `pickup_sla_days`, `transit_sla_days`, `delivery_sla_days` | SYNTHETIC (policy) | Industry-informed SLA targets by lane class. |
| `order_to_pickup_days`, `pickup_to_delivery_days`, `order_to_delivery_days`, `edd_days_promised`, `transit_actual_days` | DERIVED | Date arithmetic on ACTUAL date fields. |
| `pickup_sla_breach`, `carrier_sla_breach` | DERIVED | Boolean: actual duration vs. the SYNTHETIC SLA target. |
| `is_delivered`, `is_rto`, `is_lost`, `is_open` | DERIVED | Boolean views of `current_status`. |
| `edd_met`, `edd_missed` | DERIVED | The core KPI. `edd_met` = delivered AND `delivery_date <= edd`. |
| `outcome_label` | DERIVED | One of Delivered On-Time / Delivered Late / RTO / Lost / Open. |
| `shipment_ageing_days` | DERIVED | Snapshot date (2026-03-05) minus `order_date`. |
| `has_ndr` | DERIVED | `ndr_reason != "Not Applicable"`. |
| `ndr_category` | DERIVED (rule) | Groups the 9 ACTUAL `ndr_reason` values into 5 operational categories. |
| `ndr_low_recovery_reason` | DERIVED (rule) | Boolean flag for reasons with historically low reattempt-success. |
| `attempt_number` | DERIVED (rule) | Not present in source (only one "First Attempt date" field exists). Rule-based on status + NDR presence + date gaps. Fully documented in `data_assumptions.md`. |
| `first_attempt_success` | DERIVED | `attempt_number == 1 AND is_delivered`. |
| `rto_reason` | DERIVED | Reuses ACTUAL `ndr_reason` for RTO rows only. |
| `order_dow`, `order_week`, `is_weekend_order` | DERIVED | Calendar features from `order_date`. |
| `is_cod` | DERIVED | `payment_mode == "COD"`. |
| `exception_category` | DERIVED (rule) | RTO / Lost / Late Delivery / Active NDR / None. |

## 4. Prediction fields (`src/models/edd_risk_model.py`, `src/predictions/predict.py`)

| Field | Confidence | Definition |
|---|---|---|
| `p_delivered_on_time`, `p_delivered_late`, `p_rto`, `p_lost` | AI_PREDICTED | RandomForestClassifier class probabilities. |
| `edd_risk_score` | AI_PREDICTED | `(1 - p_delivered_on_time) * 100`. |
| `ndr_risk_score` | AI_PREDICTED | LogisticRegression P(has_ndr), dispatch-time features only. |
| `risk_tier` | AI_PREDICTED (rule on top of model) | High (≥60) / Medium (≥30) / Low. |
| `risk_reason` | AI_PREDICTED (rule-based explanation) | Human-readable driver summary generated from the shipment's own feature values vs. segment baselines. |
| `recommended_action` | AI_PREDICTED → rule | Ops action mapped from risk tier + NDR state. |

## 5. Aggregate / scorecard fields

Lane (`outputs/lane_scorecard.csv`), Carrier (`outputs/carrier_scorecard.csv`), and Carrier×Lane scorecards are all DERIVED (statistical aggregates of ACTUAL outcome fields, grouped by DERIVED `lane_class` or SYNTHETIC `carrier`). `lane_health_score` is a DERIVED transparent weighted formula (see `methodology.md`). Carrier-mix recommendation confidence (`z`, `p-value`) is DERIVED via a two-proportion z-test.

## 6. Simulation fields (`outputs/intervention_simulation.csv`)

`cumulative_edd_adherence` per stage is labeled `ACTUAL` (baseline only), `PROJECTED` (interventions 1–6, each bottom-up from an explicit, auditable formula — this now includes the lane-padding and carrier-watchlist-enforcement stages), or `SIMULATED` (the final residual-gap-closing stage — an upper-bound scenario, not a bottom-up estimate). See `methodology.md` and `business_impact.md` for the full, honest breakdown of what is proven vs. assumed.

## 7. EDD Breach Alert fields (`outputs/edd_breach_alerts.csv`, `outputs/lane_breach_summary.csv`)

| Field | Confidence | Definition |
|---|---|---|
| `days_to_edd`, `edd_already_breached` | DERIVED | `edd - SNAPSHOT_DATE` in days on an open shipment; negative means the promise has already passed. |
| `alert_priority` | AI_PREDICTED (rule on top of `risk_tier`) | P1/P2/P3, per the table in `methodology.md` §3. |
| `care_team_update`, `push_notification_title`, `push_notification_body` | MOCK | Generated message content; no real ticket or notification is sent. |
| `at_risk_shipments`, `at_risk_pct`, `lane_status` (lane summary) | DERIVED | Aggregation of the shipment-level alert queue by `(customer_city, lane_class)`. |

## 8. Lane EDD Padding fields (`outputs/edd_padding_recommendations.csv`)

| Field | Confidence | Definition |
|---|---|---|
| `p90_actual_transit_days` | DERIVED | 90th percentile of `transit_actual_days` on that lane's delivered shipments. |
| `lane_ceiling_days` | SYNTHETIC (policy) | `MAX_LANE_EDD_CEILING_DAYS[lane_class]` — the hard, realistic per-lane cap the padded promise may never cross (Local 2 / Metro 4 / Regional 3 / National 5 days). |
| `recommended_padding_days`, `new_transit_sla_days` | DERIVED (formula) | See the transparent formula in `methodology.md` §5 — capped at both `MAX_RECOMMENDED_PADDING_DAYS` and `lane_ceiling_days`, whichever binds first. |
| `manual_review_needed` | DERIVED (rule) | True when either cap above was hit, i.e. the raw transit-time gap was larger than what an honest promise can cover. |
| `watchlist_candidate` | DERIVED (rule) | True when the lane's P90 actual transit time still exceeds `new_transit_sla_days` even after capped padding — padding can't fix this lane; it feeds the Carrier Partner Watchlist (§8b). |
| `current_pct_meeting_transit_sla`, `projected_pct_meeting_transit_sla`, `projected_lift_pp` | SIMULATED | Backtest of the current vs. padded SLA against historical transit-time distribution — not a promise of future results. |
| `rationale` | DERIVED (generated text) | Explains the recommendation, or explicitly states why 0 padding is recommended (EDD gap is NDR/RTO-driven, not transit-time-driven) or why the lane was routed to the watchlist instead. |

## 8b. Carrier Partner Watchlist fields (`outputs/carrier_partner_watchlist.csv`)

| Field | Confidence | Definition |
|---|---|---|
| `edd_gap_to_target_pp` | DERIVED | `EDD_TARGET*100 - edd_adherence_pct` for this lane, from the lane scorecard. |
| `flag_reasons` | DERIVED (rule) | `TRANSIT_CEILING_BREACH` (from §8's `watchlist_candidate`), `CHRONIC_UNDERPERFORMANCE` (lane status is "Intervention Required"/"Deteriorating" with volume/gap above threshold), or both. |
| `primary_carrier`, `primary_carrier_share_pct`, `primary_carrier_edd_on_lane_pct` | DERIVED (carrier_label=SYNTHETIC; outcomes=ACTUAL) | The carrier with the largest shipment share on this exact `(customer_city, lane_class)`, and its own EDD adherence there. |
| `notice_deadline` | SYNTHETIC (policy) | `SNAPSHOT_DATE + WATCHLIST_IMPROVEMENT_WINDOW_DAYS` (14 days). |
| `mock_carrier_notice` | MOCK | Generated outbound "improve or we shift volume" notice text; never actually sent to a carrier. |

## 8c. Daily EDD Breach Tracker fields (`outputs/carrier_edd_breach_summary.csv`, `outputs/lane_edd_breach_top20.csv`, `outputs/daily_edd_tracker_summary.json`, `outputs/yesterday_edd_breach_shipments.csv`, `outputs/today_edd_at_risk_shipments.csv`, `outputs/yesterday_not_attempted_shipments.csv`)

All scoped to shipments whose `edd <= EDD_TRACKING_AS_OF_DATE` (2026-02-28 —
see docs/data_assumptions.md for why this is a second, independent date from
`SNAPSHOT_DATE`).

| Field | Confidence | Definition |
|---|---|---|
| `breached` (internal column, not exported) | DERIVED | `True` if delivered after EDD, RTO, Lost, or still open with EDD already passed as of the as-of date. Broader than `edd_missed` (§3), which only covers delivered-late shipments. |
| `shipment_volume`, `breached_shipments`, `breach_pct` (carrier summary) | DERIVED (carrier label=SYNTHETIC; outcome=ACTUAL) | Per-carrier breach count and rate among in-scope shipments. |
| `rank`, `shipment_volume`, `breached_shipments`, `breach_pct` (top lanes) | DERIVED | Top `TOP_BREACH_LANES_COUNT` (20) `(customer_city, lane_class)` combinations ranked by absolute breach count (not rate), minimum `MIN_VOLUME_FOR_BREACH_RANKING` (3) shipments to filter single-shipment noise. |
| `yesterday_breach_count`, `yesterday_breach_pct` | DERIVED | Of shipments whose EDD was the day before the as-of date, how many breached. |
| `today_at_risk_count` | DERIVED | Of shipments whose EDD is the as-of date itself, how many are still open (not yet resolved) — a forward-looking count, not a certainty, since the day isn't over. |
| `yesterday_not_attempted_count` | DERIVED | Of yesterday's EDD cohort, how many have `attempt_number == 0` (no delivery attempt was ever made). Reported as computed even when small — see docs/data_assumptions.md's note on not padding small, honest cohort counts. |

## 9. NDR Consolidation / IVR / Outreach / Channel Routing fields (`outputs/ndr_consolidated_report.csv`, `outputs/ivr_call_sheet.csv`, `outputs/ndr_customer_outreach.csv`, `outputs/ndr_care_team_digest.txt`, `outputs/ndr_channel_routing.csv`, `outputs/ndr_pending_response_outreach.xlsx`)

| Field | Confidence | Definition |
|---|---|---|
| `open_count`, `pct_of_open_queue`, `avg_reattempt_success_probability` (consolidated report) | DERIVED | Aggregation of the NDR Recovery Agent's queue (§2 masking already applied upstream) by reason, by lane, and by `recommended_channel`. |
| `recommended_channel` | DERIVED (deterministic rule cascade) | One of `IVR` / `Manual Agent Call` / `Email`, assigned by `assign_ndr_channel()` per `ndr_reason`, `attempt_number`, COD/amount, and case age — see `methodology.md` §7. Not ML, not random; fully reproducible. |
| `also_whatsapp` | DERIVED (rule) | True when the NDR reason requires the customer to take an action (confirm slot, share location pin, verify address, confirm COD readiness) — WhatsApp runs in parallel with `recommended_channel`, never as a replacement. |
| `channel_rationale` | DERIVED (generated text) | Plain-English reason the shipment got its assigned channel. |
| `info_needed`, `call_script` (IVR sheet) | DERIVED (rule) | Mapped from `ndr_reason` — what an outbound caller should ask for (landmark / address confirmation / alternate phone number). IVR sheet now includes both `IVR`- and `Manual Agent Call`-routed shipments (i.e. every voice-channel case). |
| `contact_lookup_key` | DERIVED | `shipment_id` (itself a hash — see §2). A real deployment resolves the actual phone number/address from a secure CRM using this key **at call/send time**; it is never stored in this analytics output. |
| `push_notification_title/body`, `email_subject/body`, `whatsapp_message` (outreach + Customer Care Team digest) | MOCK | Generated message content; no real push notification, email, WhatsApp message, or call is ever sent. |
| `ndr_pending_response_outreach.xlsx` sheets | Same confidence as the source row (ACTUAL shipment fields + DERIVED routing) | One sheet per channel, listing only shipments still open with an unresolved NDR event on that channel — see the workbook's own "Read Me" sheet for why this equals "pending a response" with no extra modeling needed. |

No field in §7–§9 contains a real customer name, phone number, or address at any point — see the privacy note in `src/ndr_agent/ndr_consolidated_report.py` and §2 above.
