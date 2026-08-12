"""
EDD Risk Prediction pipeline — scores every shipment with:
  edd_risk_score       (0-100, AI_PREDICTED)  — P(NOT delivered on time)
  p_delivered_on_time  (AI_PREDICTED)
  p_delivered_late     (AI_PREDICTED)
  p_rto                (AI_PREDICTED)
  p_lost                (AI_PREDICTED)
  ndr_risk_score       (0-100, AI_PREDICTED)  — P(experiences an NDR event)
  risk_tier            (High / Medium / Low)
  risk_reason          (plain-language explanation)
  recommended_action   (ops decision)

This is the "prediction pipeline" referenced in the spec: load the saved
model bundle, featurize the full shipment universe, score it, and write
outputs/edd_risk_predictions.csv. In production this same function would run
on a schedule (e.g. hourly) against newly created / still-open shipments —
see `retrain()` below for the retraining approach.
"""

from pathlib import Path
import sys

import joblib
import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    FEATURED_SHIPMENTS_PATH,
    MODEL_PATH,
    PREDICTIONS_PATH,
    OUTPUTS_DIR,
)
from src.models.edd_risk_model import (  # noqa: E402
    OUTCOME_CAT_FEATURES,
    OUTCOME_NUM_FEATURES,
    NDR_CAT_FEATURES,
    NDR_NUM_FEATURES,
)

RISK_HIGH = 60
RISK_MEDIUM = 30


def _map_hist_rates(df: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    df = df.copy()
    df["carrier_hist_edd_rate"] = df["carrier"].map(bundle["carrier_hist_edd_rate"]).fillna(bundle["global_edd_rate"])
    df["lane_hist_edd_rate"] = df["lane_class"].map(bundle["lane_hist_edd_rate"]).fillna(bundle["global_edd_rate"])
    df["carrier_hist_ndr_rate"] = df["carrier"].map(bundle["carrier_hist_ndr_rate"]).fillna(bundle["global_ndr_rate"])
    df["lane_hist_ndr_rate"] = df["lane_class"].map(bundle["lane_hist_ndr_rate"]).fillna(bundle["global_ndr_rate"])
    return df


def _risk_tier(score: float) -> str:
    if score >= RISK_HIGH:
        return "High"
    if score >= RISK_MEDIUM:
        return "Medium"
    return "Low"


def _reason(row) -> str:
    parts = []
    if row["attempt_number"] >= 2:
        parts.append(f"{int(row['attempt_number'])} delivery attempts already recorded")
    if row.get("has_ndr"):
        parts.append(f"active NDR ({row['ndr_reason']})")
    if row["lane_hist_edd_rate"] < row["carrier_hist_edd_rate"] - 0.03:
        parts.append(f"{row['lane_class']} lane has a below-average historical EDD rate "
                      f"({row['lane_hist_edd_rate']:.0%})")
    if row["carrier_hist_edd_rate"] < 0.83:
        parts.append(f"{row['carrier']} historical EDD adherence is {row['carrier_hist_edd_rate']:.0%} "
                      f"on this sample")
    if row.get("pickup_sla_breach"):
        parts.append("pickup SLA already breached")
    if row["distance_km"] > 900:
        parts.append(f"long-haul lane (~{int(row['distance_km'])} km)")
    if row["is_cod"] and row["package_amount"] > 2000:
        parts.append("high-value COD order (elevated refusal/RTO risk)")
    if not parts:
        parts.append("no elevated risk signals detected; shipment tracking within normal parameters")
    return "; ".join(parts)


def _recommended_action(row) -> str:
    tier = row["risk_tier"]
    if row["current_status"] in {"RTO", "Lost", "Delivered"}:
        return "No action — shipment closed"
    if row.get("has_ndr"):
        if row["ndr_low_recovery_reason"]:
            return "Route to NDR Recovery Agent — low reattempt-success reason, consider RTO-prevention escalation"
        return "Route to NDR Recovery Agent — customer contact for reattempt"
    if tier == "High":
        return "Proactive customer outreach + prioritize reattempt slot within 24h; flag to carrier ops"
    if tier == "Medium":
        return "Send proactive delivery-reminder notification; monitor next scan"
    return "Standard tracking — no intervention needed"


def score(df: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    df = _map_hist_rates(df, bundle)
    outcome_pipe = bundle["outcome_pipe"]
    ndr_pipe = bundle["ndr_pipe"]
    classes = bundle["outcome_classes"]

    X = df[OUTCOME_CAT_FEATURES + OUTCOME_NUM_FEATURES]
    proba = outcome_pipe.predict_proba(X)
    proba_df = pd.DataFrame(proba, columns=[f"p_{c.lower().replace(' ', '_')}" for c in classes], index=df.index)
    df = pd.concat([df, proba_df], axis=1)

    ontime_col = "p_delivered_on-time" if "p_delivered_on-time" in df.columns else "p_delivered_on_time"
    # normalize column name (spaces from class label)
    df = df.rename(columns={c: c.replace("-", "_") for c in df.columns if c.startswith("p_")})
    if "p_delivered_on_time" not in df.columns:
        # fallback in case of naming mismatch
        cand = [c for c in df.columns if c.startswith("p_delivered_on")]
        df = df.rename(columns={cand[0]: "p_delivered_on_time"})

    df["edd_risk_score"] = ((1 - df["p_delivered_on_time"]) * 100).round(1)

    Xn = df[NDR_CAT_FEATURES + NDR_NUM_FEATURES]
    df["ndr_risk_score"] = (ndr_pipe.predict_proba(Xn)[:, 1] * 100).round(1)

    df["risk_tier"] = df["edd_risk_score"].apply(_risk_tier)
    df["risk_reason"] = df.apply(_reason, axis=1)
    df["recommended_action"] = df.apply(_recommended_action, axis=1)
    df["prediction_confidence"] = "AI_PREDICTED"
    return df


def run() -> pd.DataFrame:
    bundle = joblib.load(MODEL_PATH)
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    scored = score(df, bundle)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    keep_cols = [
        "order_id", "awb", "shipment_uid", "current_status", "outcome_label", "carrier", "lane_class",
        "lane", "distance_km", "payment_mode", "attempt_number", "has_ndr", "ndr_reason",
        "edd_risk_score", "p_delivered_on_time", "p_delivered_late", "p_rto", "p_lost",
        "ndr_risk_score", "risk_tier", "risk_reason", "recommended_action", "prediction_confidence",
        "order_date", "edd", "delivery_date",
    ]
    out = scored[[c for c in keep_cols if c in scored.columns]]
    out.to_csv(PREDICTIONS_PATH, index=False)
    print(f"Scored {len(out)} shipments -> {PREDICTIONS_PATH}")
    print(out["risk_tier"].value_counts())
    open_high = scored[(scored["is_open"]) & (scored["risk_tier"] == "High")]
    print(f"Open shipments currently flagged High risk: {len(open_high)}")
    return out


if __name__ == "__main__":
    run()
