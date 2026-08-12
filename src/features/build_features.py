"""
Feature engineering layer.

The raw workbook is a single-warehouse (Mumbai) COD/Prepaid shipment ledger
with NO carrier, AWB, hub, lane, distance, SLA or attempt-count fields — all
of which a real Indian logistics control tower needs. This module derives or
synthesizes every one of those fields using deterministic, explainable,
industry-informed rules (never random fabrication of outcomes).

Every derived/synthetic field is documented in docs/data_assumptions.md and
classified in docs/data_dictionary.md as ACTUAL / DERIVED / SYNTHETIC /
AI_PREDICTED. AI_PREDICTED fields (risk scores) are produced later by
src/models/edd_risk_model.py — this module only builds the *inputs* to that
model plus deterministic business-rule fields.
"""

from pathlib import Path
import hashlib
import sys

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    CARRIERS,
    CLEAN_SHIPMENTS_PATH,
    FEATURED_SHIPMENTS_PATH,
    LOCAL_MAX_KM,
    MAX_ATTEMPTS_BEFORE_RTO_RISK,
    METRO_CITIES,
    METRO_MAX_KM,
    ORIGIN_ZONE,
    PICKUP_SLA_DAYS,
    PROCESSED_DIR,
    REGIONAL_MAX_KM,
    SNAPSHOT_DATE,
    TRANSIT_SLA_DAYS,
    ZONE_DISTANCE_KM,
)

SUCCESS_STATUSES = {"Delivered"}
TERMINAL_FAIL_STATUSES = {"RTO", "Lost"}
OPEN_STATUSES = {"In-Transit", "Out of delivery", "Pickup done", "Order Packed"}

NDR_CATEGORY_MAP = {
    "Address issue": "Address Issue",
    "Landmark missing": "Address Issue",
    "Phone not reachable": "Contact Issue",
    "Customer not available": "Customer Availability",
    "Customer requested re-attempt": "Customer Availability",
    "Delivery postponed by customer": "Customer Availability",
    "Customer refused delivery": "Customer Rejection",
    "Customer cancelled order": "Customer Rejection",
    "COD payment declined": "Payment Issue",
    "Not Applicable": "N/A",
}

# High-risk-of-non-recovery NDR reasons: reattempting rarely converts these.
LOW_RECOVERY_REASONS = {"Customer refused delivery", "Customer cancelled order", "COD payment declined"}


def _stable_hash_int(*parts: str) -> int:
    """Deterministic, reproducible hash -> non-negative int (not Python's
    salted hash()). Used for synthetic-but-stable assignment (carrier, AWB,
    distance jitter) so re-running the pipeline always yields identical
    output."""
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(h[:12], 16)


def _synthetic_awb(order_id) -> str:
    return f"AWB{_stable_hash_int(str(order_id), 'awb') % 10_000_000_000:010d}"


def _zone_of(pincode: str) -> str:
    if pd.isna(pincode):
        return np.nan
    s = str(pincode).strip()
    return s[0] if s and s[0].isdigit() else np.nan


def _classify_lane(row) -> str:
    city = str(row["customer_city"]).strip().lower()
    zone = row["dest_zone"]
    if city in {"mumbai", "thane", "navi mumbai"}:
        return "Local"
    if city in METRO_CITIES:
        return "Metro"
    if pd.notna(zone) and zone == ORIGIN_ZONE:
        return "Regional"
    return "National"


def _estimate_distance_km(row) -> float:
    zone = row["dest_zone"]
    base = ZONE_DISTANCE_KM.get(zone, ZONE_DISTANCE_KM["4"]) if pd.notna(zone) else ZONE_DISTANCE_KM["4"]
    if row["lane_class"] == "Local":
        base = 15
    elif row["lane_class"] == "Metro" and row["dest_zone"] == ORIGIN_ZONE:
        base = 160  # e.g. Pune from Mumbai
    # Deterministic +/-15% jitter so lane-mates aren't all identical, derived
    # from the pincode hash (reproducible, not random.random()).
    jitter_seed = _stable_hash_int(str(row.get("customer_pincode", "")), "dist") % 1000 / 1000
    jitter = 0.85 + (0.30 * jitter_seed)
    return round(base * jitter, 1)


def _assign_carrier(row) -> str:
    """Deterministic synthetic carrier assignment. Roughly even split, with a
    mild lane-conditional skew so the dataset exhibits realistic carrier
    concentration patterns (e.g. one carrier stronger on metro, another used
    more on national) for the Carrier Optimization Agent to analyse.

    IMPORTANT: because the source data has no real carrier field, this
    assignment is independent of the shipment's true (actual) outcome. Any
    carrier-level performance difference computed downstream reflects real
    ACTUAL outcomes grouped by a SYNTHETIC carrier label — see
    docs/data_assumptions.md ("Carrier") for the full caveat.
    """
    lane = row["lane_class"]
    bucket = _stable_hash_int(str(row["order_id"]), "carrier", lane) % 100
    if lane == "Local":
        weights = [40, 35, 15, 10]
    elif lane == "Metro":
        weights = [30, 30, 25, 15]
    elif lane == "Regional":
        weights = [20, 25, 30, 25]
    else:  # National
        weights = [15, 20, 25, 40]
    cum = np.cumsum(weights)
    idx = int(np.searchsorted(cum, bucket, side="right"))
    idx = min(idx, len(CARRIERS) - 1)
    return CARRIERS[idx]


def _attempt_number(row) -> int:
    """Derive attempt count — not present in source data. Rule-based on
    status + presence of an NDR reason + gap between first attempt and
    delivery. See docs/data_assumptions.md ("Attempt number")."""
    status = row["current_status"]
    has_ndr = row["ndr_reason"] != "Not Applicable"
    if status in OPEN_STATUSES and pd.isna(row["first_attempt_date"]):
        return 0
    if status == "Delivered":
        if not has_ndr:
            return 1
        gap = (row["delivery_date"] - row["first_attempt_date"]).days if pd.notna(row["first_attempt_date"]) else 1
        return 3 if gap > 4 else 2
    if status == "RTO":
        return MAX_ATTEMPTS_BEFORE_RTO_RISK if pd.notna(row["first_attempt_date"]) else 1
    if status == "Lost":
        return 1 if pd.notna(row["first_attempt_date"]) else 0
    if status == "Out of delivery":
        if pd.isna(row["first_attempt_date"]):
            return 0
        days_since = (pd.Timestamp(SNAPSHOT_DATE) - row["first_attempt_date"]).days
        return int(min(MAX_ATTEMPTS_BEFORE_RTO_RISK, max(1, days_since + 1)))
    return 0


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    snapshot = pd.Timestamp(SNAPSHOT_DATE)

    # --- Geography / lane / distance ---------------------------------------
    df["dest_zone"] = df["customer_pincode"].apply(_zone_of)
    df["lane_class"] = df.apply(_classify_lane, axis=1)
    df["distance_km"] = df.apply(_estimate_distance_km, axis=1)
    df["lane"] = df["customer_city"].astype(str).str.title() + " <- " + "Mumbai"

    # --- Identifiers ---------------------------------------------------------
    df["awb"] = df["order_id"].apply(_synthetic_awb)
    df["origin_hub"] = "Mumbai Bhiwandi FC"
    df["destination_hub"] = df["customer_city"].astype(str).str.title() + " Delivery Station"
    df["carrier"] = df.apply(_assign_carrier, axis=1)

    # --- SLA targets -----------------------------------------------------
    df["pickup_sla_days"] = PICKUP_SLA_DAYS
    df["transit_sla_days"] = df["lane_class"].map(TRANSIT_SLA_DAYS)
    df["delivery_sla_days"] = df["pickup_sla_days"] + df["transit_sla_days"]

    # --- Actual cycle-time measurements (ACTUAL, derived only via subtraction)
    df["order_to_pickup_days"] = (df["pickup_date"] - df["order_date"]).dt.days
    df["pickup_to_delivery_days"] = (df["delivery_date"] - df["pickup_date"]).dt.days
    df["order_to_delivery_days"] = (df["delivery_date"] - df["order_date"]).dt.days
    df["edd_days_promised"] = (df["edd"] - df["order_date"]).dt.days

    df["pickup_sla_breach"] = df["order_to_pickup_days"] > df["pickup_sla_days"]

    # --- EDD adherence (the core KPI) --------------------------------------
    # A shipment is EDD-adherent only if it was actually DELIVERED on/before
    # the promised EDD. RTO / Lost / still-open shipments are, by definition,
    # not a successful on-time delivery.
    df["is_delivered"] = df["current_status"] == "Delivered"
    df["edd_met"] = df["is_delivered"] & (df["delivery_date"] <= df["edd"])
    df["edd_missed"] = df["is_delivered"] & (df["delivery_date"] > df["edd"])
    df["is_rto"] = df["current_status"] == "RTO"
    df["is_lost"] = df["current_status"] == "Lost"
    df["is_open"] = df["current_status"].isin(OPEN_STATUSES)

    # Outcome label used for ML training / reporting on CLOSED shipments only.
    def _outcome(row):
        if row["edd_met"]:
            return "Delivered On-Time"
        if row["edd_missed"]:
            return "Delivered Late"
        if row["is_rto"]:
            return "RTO"
        if row["is_lost"]:
            return "Lost"
        return "Open"

    df["outcome_label"] = df.apply(_outcome, axis=1)

    # --- Shipment ageing (days since order, capped at snapshot date) -------
    df["shipment_ageing_days"] = (snapshot - df["order_date"]).dt.days

    # --- NDR ------------------------------------------------------------
    df["has_ndr"] = df["ndr_reason"] != "Not Applicable"
    df["ndr_category"] = df["ndr_reason"].map(NDR_CATEGORY_MAP).fillna("Other")
    df["ndr_low_recovery_reason"] = df["ndr_reason"].isin(LOW_RECOVERY_REASONS)

    # --- Attempts (DERIVED — see docstring) ---------------------------------
    df["attempt_number"] = df.apply(_attempt_number, axis=1)
    df["first_attempt_success"] = (df["attempt_number"] == 1) & df["is_delivered"]

    # --- RTO reason (reuse actual ndr_reason where status == RTO) ----------
    df["rto_reason"] = np.where(df["is_rto"], df["ndr_reason"], "Not Applicable")

    # --- Day-of-week / calendar features (ACTUAL, derived via .dt) ---------
    df["order_dow"] = df["order_date"].dt.day_name()
    df["order_week"] = df["order_date"].dt.isocalendar().week.astype(int)
    df["is_weekend_order"] = df["order_date"].dt.dayofweek >= 5

    # --- COD flag ------------------------------------------------------
    df["is_cod"] = df["payment_mode"] == "Cod"
    df["payment_mode"] = df["payment_mode"].replace({"Cod": "COD"})

    # --- Carrier SLA breach (transit SLA vs actual, for closed shipments) --
    df["transit_actual_days"] = df["pickup_to_delivery_days"]
    df["carrier_sla_breach"] = df["is_delivered"] & (
        df["transit_actual_days"] > df["transit_sla_days"]
    )

    df["exception_category"] = np.select(
        [df["is_rto"], df["is_lost"], df["edd_missed"], df["has_ndr"] & ~df["is_delivered"]],
        ["RTO", "Lost", "Late Delivery", "Active NDR"],
        default="None",
    )

    return df


def run() -> pd.DataFrame:
    df = pd.read_csv(CLEAN_SHIPMENTS_PATH, parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"])
    featured = build_features(df)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    featured.to_csv(FEATURED_SHIPMENTS_PATH, index=False)
    print(f"Built {featured.shape[1]} features on {len(featured)} rows -> {FEATURED_SHIPMENTS_PATH}")
    print("Lane class distribution:\n", featured["lane_class"].value_counts())
    print("Carrier distribution:\n", featured["carrier"].value_counts())
    closed = featured[~featured["is_open"]]
    print(f"EDD adherence (closed, delivered only, ACTUAL): "
          f"{featured['edd_met'].sum()}/{featured['is_delivered'].sum()} = "
          f"{featured['edd_met'].sum()/featured['is_delivered'].sum():.2%}")
    return featured


if __name__ == "__main__":
    run()
