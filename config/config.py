"""
Central configuration for the AI Logistics EDD Control Tower.

All thresholds, business rules and synthetic-data parameters live here so the
rest of the codebase never hard-codes a "magic number". Change values here to
re-tune the system for a different business (different warehouse city, SLA
targets, carrier list, remittance policy, etc).
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DATA_PATH = ROOT_DIR / "data" / "raw" / "logistics_workbook_raw.xlsx"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
SAMPLE_DIR = ROOT_DIR / "data" / "sample"
OUTPUTS_DIR = ROOT_DIR / "outputs"
DOCS_DIR = ROOT_DIR / "docs"

CLEAN_SHIPMENTS_PATH = PROCESSED_DIR / "shipments_clean.csv"
FEATURED_SHIPMENTS_PATH = PROCESSED_DIR / "shipments_features.csv"
PREDICTIONS_PATH = OUTPUTS_DIR / "edd_risk_predictions.csv"
LANE_SCORECARD_PATH = OUTPUTS_DIR / "lane_scorecard.csv"
CARRIER_SCORECARD_PATH = OUTPUTS_DIR / "carrier_scorecard.csv"
CARRIER_LANE_SCORECARD_PATH = OUTPUTS_DIR / "carrier_lane_scorecard.csv"
CARRIER_MIX_RECOMMENDATIONS_PATH = OUTPUTS_DIR / "carrier_mix_recommendations.csv"
NDR_QUEUE_PATH = OUTPUTS_DIR / "customer_care_notifications.csv"
COD_QUEUE_PATH = OUTPUTS_DIR / "cod_remittance_queue.csv"
ACTION_QUEUE_PATH = OUTPUTS_DIR / "central_action_queue.csv"
SIMULATION_PATH = OUTPUTS_DIR / "intervention_simulation.csv"
KPI_SUMMARY_PATH = OUTPUTS_DIR / "kpi_summary.json"
DASHBOARD_DATA_PATH = ROOT_DIR / "dashboard" / "dashboard_data.json"
MODEL_PATH = OUTPUTS_DIR / "edd_risk_model.joblib"
MODEL_METRICS_PATH = OUTPUTS_DIR / "model_evaluation.json"
ROOT_CAUSE_PATH = OUTPUTS_DIR / "root_cause_analysis.md"

# EDD Breach Alert Agent (in-transit shipments at risk of missing EDD)
BREACH_ALERT_QUEUE_PATH = OUTPUTS_DIR / "edd_breach_alerts.csv"
LANE_BREACH_SUMMARY_PATH = OUTPUTS_DIR / "lane_breach_summary.csv"

# Lane EDD Padding Recommender
PADDING_RECOMMENDATIONS_PATH = OUTPUTS_DIR / "edd_padding_recommendations.csv"

# NDR Consolidated Report + IVR + outreach
NDR_CONSOLIDATED_REPORT_PATH = OUTPUTS_DIR / "ndr_consolidated_report.csv"
IVR_CALL_SHEET_PATH = OUTPUTS_DIR / "ivr_call_sheet.csv"
NDR_CARE_TEAM_DIGEST_PATH = OUTPUTS_DIR / "ndr_care_team_digest.txt"
NDR_CUSTOMER_OUTREACH_PATH = OUTPUTS_DIR / "ndr_customer_outreach.csv"
NDR_CHANNEL_ROUTING_PATH = OUTPUTS_DIR / "ndr_channel_routing.csv"
NDR_PENDING_RESPONSE_XLSX_PATH = OUTPUTS_DIR / "ndr_pending_response_outreach.xlsx"

# Carrier Partner Improvement / Volume-Shift Watchlist
CARRIER_WATCHLIST_PATH = OUTPUTS_DIR / "carrier_partner_watchlist.csv"

# ---------------------------------------------------------------------------
# Business targets
# ---------------------------------------------------------------------------
EDD_TARGET = 0.95          # Target EDD adherence (leadership goal; raised from 0.94 2026-08)
SNAPSHOT_DATE = "2026-03-05"  # "Today" for the dataset, per Validation sheet

# ---------------------------------------------------------------------------
# Warehouse / origin (single-origin operation observed in raw data)
# ---------------------------------------------------------------------------
ORIGIN_CITY = "Mumbai"
ORIGIN_PINCODE = "400016"
ORIGIN_ZONE = "4"  # first digit of pincode -> India Speed Post zone

# ---------------------------------------------------------------------------
# Lane classification rules (SYNTHETIC / DERIVED — see docs/data_assumptions.md)
# ---------------------------------------------------------------------------
METRO_CITIES = {
    "mumbai", "delhi", "new delhi", "bangalore", "bengaluru", "chennai",
    "hyderabad", "pune", "kolkata", "ahmedabad", "gurgaon", "gurugram",
    "noida", "gautam buddha nagar", "thane", "navi mumbai",
}

# Approximate road distance (km) from Mumbai (zone 4) to each India Speed
# Post zone (first digit of destination pincode). Representative midpoints,
# not haversine-precise — this is an industry-informed synthetic estimate
# used only to rank lanes into Local/Metro/Regional/National buckets and to
# give the ML model a monotonic distance signal.
ZONE_DISTANCE_KM = {
    "0": 1500,  # Army Postal Service / special
    "1": 1400,  # Delhi, Haryana, Punjab, HP, J&K, Chandigarh
    "2": 1300,  # UP, Uttarakhand
    "3": 700,   # Rajasthan, Gujarat
    "4": 350,   # Maharashtra, MP, Chhattisgarh, Goa (own zone)
    "5": 850,   # Karnataka, Andhra Pradesh, Telangana
    "6": 1250,  # Tamil Nadu, Kerala, Puducherry
    "7": 1900,  # West Bengal, Odisha, North-East
    "8": 1650,  # Bihar, Jharkhand
    "9": 1500,  # APS / reserved
}

LOCAL_MAX_KM = 40
METRO_MAX_KM = 900   # metro-to-metro / same-zone major city
REGIONAL_MAX_KM = 1100

# ---------------------------------------------------------------------------
# SLA targets (industry-informed synthetic assumption, days)
# ---------------------------------------------------------------------------
# Revised 2026-08 to realistic, distance-proportionate ceilings after
# leadership review flagged the original targets as unrealistic ("looks very
# fake") — see docs/data_assumptions.md for the full rationale. IMPORTANT:
# these are the SLA *targets* used to score EDD adherence and size padding
# recommendations. They are a documented SYNTHETIC/policy assumption, safely
# changeable without touching any ACTUAL raw shipment data (delivery_date is
# never rewritten — see PADDING ceiling note below and
# docs/data_assumptions.md §"Why we did not rewrite raw delivery dates").
#
# These are AGGRESSIVE/default targets — the promise a lane starts with
# before any padding. They deliberately sit below MAX_LANE_EDD_CEILING_DAYS
# below, so the padding recommender has honest room to loosen a promise when
# the data supports it, while MAX_LANE_EDD_CEILING_DAYS is the hard cap that
# promise can never cross (that's the leadership-specified realistic max):
#
#             default target   realistic ceiling (hard cap, never exceeded)
# Local              1 day            <= 2 days
# Metro              3 days           3-4 days  (this IS the stated 3-4d range)
# Regional           2 days           <= 3 days
# National           4 days           <= 5 days
#
# Metro's ceiling is slower than Regional's despite the name because
# _classify_lane() buckets both near AND far metros here — e.g.
# Bangalore/Kolkata are 850-1900km from the Mumbai origin — so a single
# blended Metro ceiling has to accommodate the far end.
PICKUP_SLA_DAYS = 1
TRANSIT_SLA_DAYS = {
    "Local": 1,
    "Metro": 3,
    "Regional": 2,
    "National": 4,
}

# Hard EDD ceilings (promise-to-customer caps), independent of whatever
# padding math computes. No lane's padded/promised EDD may ever exceed these
# — this is what keeps "no padding should be erratically high" true even if
# a lane's P90 actual transit time is far worse than its SLA target. A lane
# that cannot be honestly served within its ceiling, even at max padding, is
# NOT given more padding — it is routed to the Carrier Partner Improvement /
# Volume-Shift Watchlist instead (see src/carrier_engine/carrier_optimization.py).
MAX_LANE_EDD_CEILING_DAYS = {
    "Local": 2,
    "Metro": 4,
    "Regional": 3,
    "National": 5,
}

# ---------------------------------------------------------------------------
# Synthetic carriers (no carrier field exists in source data)
# ---------------------------------------------------------------------------
CARRIERS = ["Carrier A", "Carrier B", "Carrier C", "Carrier D"]

# Minimum shipment volume on a lane x carrier combination before the
# Carrier Optimization Agent is allowed to recommend a mix change.
MIN_VOLUME_FOR_RECOMMENDATION = 30
MIN_VOLUME_FOR_LANE_INTERVENTION = 20

# ---------------------------------------------------------------------------
# EDD Breach Alert Agent (in-transit shipments about to miss EDD)
# ---------------------------------------------------------------------------
# Minimum currently-OPEN shipment volume on a lane before it's ranked in the
# breach-risk lane summary — open volume per lane is naturally much smaller
# than total historical volume, so this is intentionally lower than
# MIN_VOLUME_FOR_LANE_INTERVENTION.
MIN_OPEN_VOLUME_FOR_BREACH_SUMMARY = 5
# A shipment still in transit whose EDD is this many days away (or already
# passed) and carries a High/Medium risk score is escalated for outreach.
BREACH_ALERT_URGENT_DAYS = 1   # EDD already passed, or passes within 1 day -> P1
BREACH_ALERT_HIGH_DAYS = 3     # EDD passes within 3 days -> P2

# ---------------------------------------------------------------------------
# Lane EDD Padding Recommender
# ---------------------------------------------------------------------------
# Padding is sized off the P90 of actual transit time on a lane (i.e. "pad
# enough that 90% of historically observed deliveries would have met the new
# EDD"), rounded up to the nearest whole day. Only recommended for lanes
# below this adherence threshold — a lane already hitting the target doesn't
# need its promise loosened.
PADDING_PERCENTILE = 90
PADDING_EDD_ADHERENCE_THRESHOLD = 0.90
# Lowered 2026-08 (was 5) — a customer-facing EDD promise should never be
# padded by more than 2 days off the lane-class SLA target before it starts
# hurting customer experience ("no padding should be erratically high").
# Padding is additionally hard-capped per lane by MAX_LANE_EDD_CEILING_DAYS
# above; whichever cap binds first wins. Lanes whose real transit time can't
# be honestly served within both caps are routed to the Carrier Partner
# Improvement / Volume-Shift Watchlist instead of given more padding.
MAX_RECOMMENDED_PADDING_DAYS = 2

# ---------------------------------------------------------------------------
# Carrier Partner Improvement / Volume-Shift Watchlist
# ---------------------------------------------------------------------------
# A lane lands on the watchlist when honest padding (within the caps above)
# cannot close its EDD gap — i.e. its P90 actual transit time still exceeds
# MAX_LANE_EDD_CEILING_DAYS[lane_class] even after MAX_RECOMMENDED_PADDING_DAYS
# is applied — OR it is already flagged "Intervention Required" /
# "Deteriorating" by the lane scorecard with a statistically-significant
# carrier gap. These lanes get a mock outbound "improve or we shift volume"
# notice to the carrier partner instead of a padded promise to the customer.
WATCHLIST_MIN_VOLUME = 20            # same floor as lane intervention threshold
WATCHLIST_IMPROVEMENT_WINDOW_DAYS = 14   # "improve within N days" in the mock notice
WATCHLIST_MIN_EDD_GAP_PP = 5.0       # min percentage-point gap below target to qualify

# ---------------------------------------------------------------------------
# NDR / RTO business rules
# ---------------------------------------------------------------------------
# Reasons after which a repeat attempt is historically less likely to
# succeed (used to prioritise the customer-care queue).
HIGH_RISK_NDR_REASONS = {
    "COD payment declined",
    "Customer refused delivery",
    "Customer cancelled order",
}
MAX_ATTEMPTS_BEFORE_RTO_RISK = 3

# ---------------------------------------------------------------------------
# NDR outreach channel routing
# ---------------------------------------------------------------------------
# Priority-ordered rule cascade (highest priority first) deciding which
# channel handles a given NDR case. Deliberately keeps the expensive channel
# (manual agent call) gated by severity/attempt-count/age rather than
# blanket-applied. See docs/methodology.md for the full rationale.
#
#   1. MANUAL AGENT CALL (₹15-25/call) — repeat failures, high-value/COD
#      disputes, anything aged past this many hours.
#   2. EMAIL — backup/documentation channel, used when phone is unreachable.
#   3. IVR (automated call) — default first-touch, near-zero cost, bulk
#      Day-1 volume.
#   + WhatsApp runs IN PARALLEL with IVR (not instead of) whenever the
#     customer needs to take an action (confirm slot, share location pin,
#     verify address, confirm COD readiness) — flagged via also_whatsapp.
NDR_MANUAL_CALL_MIN_ATTEMPT = 2                 # 2nd/3rd+ attempt -> manual call
NDR_MANUAL_CALL_AGE_HOURS = 36                  # aged past 24-48h -> manual call (midpoint)
NDR_MANUAL_CALL_HIGH_VALUE_INR = 2000           # COD-amount-dispute threshold
NDR_EMAIL_REASONS = {"Phone not reachable"}      # backup channel when phone doesn't work
NDR_WHATSAPP_ACTION_REASONS = {                  # customer must DO something -> also WhatsApp
    "Address issue",
    "Landmark missing",
    "COD payment declined",
    "Customer requested re-attempt",
}

# ---------------------------------------------------------------------------
# COD remittance rule
# ---------------------------------------------------------------------------
REMITTANCE_DAYS_AFTER_DELIVERY = 2

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
