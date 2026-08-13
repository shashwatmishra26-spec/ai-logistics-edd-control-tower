"""
EDD Breach Alert Agent.

The EDD Risk Prediction Agent (src/predictions/predict.py) scores every
shipment, including the ones still in transit. This agent is the narrower,
action-first layer on top of that score: it filters down to shipments still
IN TRANSIT that are at risk of missing (or have already passed) their
promised EDD, and for each one generates a mock, two-channel alert —

  1. a Customer Care Team update (what a care agent would see in their queue)
  2. a push-notification payload (what the customer's app would show)

No real message is sent anywhere — see docs/data_assumptions.md. The output
is a work queue, mirroring the same "mock action, real logic" pattern used by
src/ndr_agent and src/remittance_agent.

It also produces a LANE-level breach summary, because a Head of Logistics
needs "which lanes are about to blow their EDD promise right now" as much as
a shipment-level list — that's the primary question this agent exists to
answer.
"""

from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    BREACH_ALERT_HIGH_DAYS,
    BREACH_ALERT_QUEUE_PATH,
    BREACH_ALERT_URGENT_DAYS,
    FEATURED_SHIPMENTS_PATH,
    LANE_BREACH_SUMMARY_PATH,
    MIN_OPEN_VOLUME_FOR_BREACH_SUMMARY,
    OUTPUTS_DIR,
    PREDICTIONS_PATH,
    SNAPSHOT_DATE,
)

ALERT_COLUMNS = [
    "shipment_id", "awb", "order_id", "carrier", "lane_class", "customer_city",
    "current_status", "edd", "days_to_edd", "edd_already_breached",
    "edd_risk_score", "risk_tier", "attempt_number",
    "alert_priority", "care_team_update", "push_notification_title",
    "push_notification_body", "recommended_action", "data_confidence",
]

LANE_SUMMARY_COLUMNS = [
    "customer_city", "lane_class", "open_shipments", "at_risk_shipments",
    "already_breached", "p1_count", "at_risk_pct", "lane_status",
]

PRIORITY_RANK = {"P1 - Urgent": 0, "P2 - High": 1, "P3 - Standard": 2}


def _alert_priority(days_to_edd: int, risk_tier: str) -> str:
    """P1: EDD has already passed while the shipment is still moving, or a
    High-risk shipment whose EDD lands within BREACH_ALERT_URGENT_DAYS.
    P2: High-risk with more runway, or Medium-risk right up against EDD.
    P3: everything else that still cleared the High/Medium risk-tier filter."""
    if days_to_edd < 0:
        return "P1 - Urgent"
    if risk_tier == "High" and days_to_edd <= BREACH_ALERT_URGENT_DAYS:
        return "P1 - Urgent"
    if risk_tier == "High" and days_to_edd <= BREACH_ALERT_HIGH_DAYS:
        return "P2 - High"
    if risk_tier == "Medium" and days_to_edd <= BREACH_ALERT_URGENT_DAYS:
        return "P2 - High"
    return "P3 - Standard"


def _care_team_update(row) -> str:
    if row["edd_already_breached"]:
        return (
            f"AWB {row['awb']} ({row['customer_city']}, {row['lane_class']} lane) has PASSED its "
            f"promised EDD ({row['edd'].date()}) and is still showing '{row['current_status']}'. "
            f"Proactively contact the customer with a revised delivery estimate before they contact us."
        )
    return (
        f"AWB {row['awb']} ({row['customer_city']}, {row['lane_class']} lane) is {row['edd_risk_score']}% "
        f"likely to miss its EDD of {row['edd'].date()} ({int(row['days_to_edd'])} day(s) away). "
        f"Reason: {row['risk_reason']}"
    )


def _push_title(row) -> str:
    return "An update on your order" if row["edd_already_breached"] else "Your order may arrive a little later than planned"


def _push_body(row) -> str:
    if row["edd_already_breached"]:
        return (
            f"Order #{row['order_id']} is taking longer than expected. We're actively tracking it and "
            f"will share an updated delivery estimate shortly. Sorry for the wait."
        )
    return (
        f"Order #{row['order_id']} is on its way, but there's a chance it may not arrive by "
        f"{row['edd'].date()}. We're on it — no action needed from you right now."
    )


def build_breach_alert_queue(preds: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """preds: edd_risk_predictions.csv-shaped frame, scored for every
    shipment including open ones. features: shipments_features.csv-shaped
    frame, needed here for `customer_city` and `is_open` which predict.py's
    slim prediction output doesn't carry."""
    snapshot = pd.Timestamp(SNAPSHOT_DATE)
    open_ids = set(features.loc[features["is_open"], "order_id"])
    open_preds = preds[preds["order_id"].isin(open_ids)].copy()
    open_preds = open_preds[open_preds["risk_tier"].isin(["High", "Medium"])]

    if open_preds.empty:
        return pd.DataFrame(columns=ALERT_COLUMNS)

    open_preds = open_preds.merge(
        features[["order_id", "customer_city"]], on="order_id", how="left"
    )
    open_preds["edd"] = pd.to_datetime(open_preds["edd"])
    open_preds["days_to_edd"] = (open_preds["edd"] - snapshot).dt.days
    open_preds["edd_already_breached"] = open_preds["days_to_edd"] < 0
    open_preds["alert_priority"] = open_preds.apply(
        lambda r: _alert_priority(r["days_to_edd"], r["risk_tier"]), axis=1
    )
    open_preds["shipment_id"] = open_preds["shipment_uid"]
    open_preds["care_team_update"] = open_preds.apply(_care_team_update, axis=1)
    open_preds["push_notification_title"] = open_preds.apply(_push_title, axis=1)
    open_preds["push_notification_body"] = open_preds.apply(_push_body, axis=1)
    open_preds["data_confidence"] = (
        "AI_PREDICTED (risk score) + MOCK (message content — no real push notification or "
        "Customer Care Team ticket is actually sent; see docs/data_assumptions.md)"
    )

    open_preds["_rank"] = open_preds["alert_priority"].map(PRIORITY_RANK)
    open_preds = open_preds.sort_values(["_rank", "days_to_edd"]).drop(columns="_rank")

    return open_preds[[c for c in ALERT_COLUMNS if c in open_preds.columns]]


def lane_breach_summary(alert_queue: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Ranks lanes by concentration of at-risk in-transit shipments — 'which
    lanes are about to breach EDD', not just which individual shipments."""
    open_df = features[features["is_open"]]
    grp_cols = ["customer_city", "lane_class"]
    open_counts = open_df.groupby(grp_cols).size().rename("open_shipments").reset_index()

    if len(alert_queue):
        at_risk_counts = alert_queue.groupby(grp_cols).agg(
            at_risk_shipments=("shipment_id", "count"),
            already_breached=("edd_already_breached", "sum"),
            p1_count=("alert_priority", lambda s: int((s == "P1 - Urgent").sum())),
        ).reset_index()
    else:
        at_risk_counts = pd.DataFrame(columns=grp_cols + ["at_risk_shipments", "already_breached", "p1_count"])

    summary = open_counts.merge(at_risk_counts, on=grp_cols, how="left")
    for c in ["at_risk_shipments", "already_breached", "p1_count"]:
        summary[c] = summary[c].fillna(0).astype(int)
    summary["at_risk_pct"] = (summary["at_risk_shipments"] / summary["open_shipments"] * 100).round(1)

    def _status(r):
        if r["open_shipments"] < MIN_OPEN_VOLUME_FOR_BREACH_SUMMARY:
            return "Insufficient Sample"
        if r["already_breached"] > 0 or r["at_risk_pct"] >= 50:
            return "Breach Risk"
        if r["at_risk_pct"] >= 25:
            return "Watch"
        return "Healthy"

    summary["lane_status"] = summary.apply(_status, axis=1)
    summary = summary.sort_values(["at_risk_pct", "open_shipments"], ascending=[False, False]).reset_index(drop=True)
    return summary[LANE_SUMMARY_COLUMNS]


def run() -> pd.DataFrame:
    preds = pd.read_csv(PREDICTIONS_PATH, parse_dates=["order_date", "edd", "delivery_date"])
    features = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    alert_queue = build_breach_alert_queue(preds, features)
    lane_summary = lane_breach_summary(alert_queue, features)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    alert_queue.to_csv(BREACH_ALERT_QUEUE_PATH, index=False)
    lane_summary.to_csv(LANE_BREACH_SUMMARY_PATH, index=False)

    print(f"Wrote {len(alert_queue)} breach alerts -> {BREACH_ALERT_QUEUE_PATH}")
    if len(alert_queue):
        print(alert_queue["alert_priority"].value_counts())
    print(f"Wrote {len(lane_summary)} lane breach-summary rows -> {LANE_BREACH_SUMMARY_PATH}")
    breach_lanes = lane_summary[lane_summary["lane_status"] == "Breach Risk"]
    print(f"Lanes currently at 'Breach Risk': {len(breach_lanes)}")
    return alert_queue


if __name__ == "__main__":
    run()
