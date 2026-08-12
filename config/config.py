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

# ---------------------------------------------------------------------------
# Business targets
# ---------------------------------------------------------------------------
EDD_TARGET = 0.94          # Target EDD adherence (leadership goal)
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
PICKUP_SLA_DAYS = 1
TRANSIT_SLA_DAYS = {
    "Local": 1,
    "Metro": 2,
    "Regional": 4,
    "National": 6,
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
MAX_RECOMMENDED_PADDING_DAYS = 5  # sanity cap; beyond this, flag for manual ops review instead

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
# COD remittance rule
# ---------------------------------------------------------------------------
REMITTANCE_DAYS_AFTER_DELIVERY = 2

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
