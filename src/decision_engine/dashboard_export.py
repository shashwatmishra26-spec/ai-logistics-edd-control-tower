"""
Consolidates every agent's output into dashboard/dashboard_data.json — the
single data file the self-contained HTML dashboard (dashboard/index.html)
reads. Keeping this as a separate, explicit export step means the dashboard
itself stays a pure presentation layer with no business logic.
"""

from pathlib import Path
import json
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    ACTION_QUEUE_PATH,
    CARRIER_LANE_SCORECARD_PATH,
    CARRIER_MIX_RECOMMENDATIONS_PATH,
    CARRIER_SCORECARD_PATH,
    COD_QUEUE_PATH,
    DASHBOARD_DATA_PATH,
    EDD_TARGET,
    FEATURED_SHIPMENTS_PATH,
    KPI_SUMMARY_PATH,
    LANE_SCORECARD_PATH,
    NDR_QUEUE_PATH,
    PREDICTIONS_PATH,
    SIMULATION_PATH,
)


def run():
    df = pd.read_csv(FEATURED_SHIPMENTS_PATH, parse_dates=["order_date", "delivery_date", "edd"])
    preds = pd.read_csv(PREDICTIONS_PATH)
    lane_df = pd.read_csv(LANE_SCORECARD_PATH)
    carrier_df = pd.read_csv(CARRIER_SCORECARD_PATH)
    cl_df = pd.read_csv(CARRIER_LANE_SCORECARD_PATH)
    rec_df = pd.read_csv(CARRIER_MIX_RECOMMENDATIONS_PATH)
    ndr_q = pd.read_csv(NDR_QUEUE_PATH)
    cod_q = pd.read_csv(COD_QUEUE_PATH)
    action_q = pd.read_csv(ACTION_QUEUE_PATH)
    sim_df = pd.read_csv(SIMULATION_PATH)
    with open(KPI_SUMMARY_PATH) as f:
        kpis = json.load(f)

    # --- EDD adherence trend by order week (ACTUAL) -------------------------
    delivered = df[df["is_delivered"]].copy()
    delivered["order_week"] = delivered["order_date"].dt.to_period("W").apply(lambda p: str(p.start_time.date()))
    weekly = delivered.groupby("order_week").agg(
        shipments=("edd_met", "size"), edd_met=("edd_met", "sum")
    ).reset_index()
    weekly["edd_adherence_pct"] = (weekly["edd_met"] / weekly["shipments"] * 100).round(1)
    weekly = weekly.sort_values("order_week")

    # --- RTO trend by order week ---------------------------------------------
    df["order_week"] = df["order_date"].dt.to_period("W").apply(lambda p: str(p.start_time.date()))
    rto_weekly = df.groupby("order_week").agg(
        shipments=("is_rto", "size"), rto=("is_rto", "sum")
    ).reset_index()
    rto_weekly["rto_pct"] = (rto_weekly["rto"] / rto_weekly["shipments"] * 100).round(1)
    rto_weekly = rto_weekly.sort_values("order_week")

    # --- NDR Pareto -----------------------------------------------------------
    ndr_pareto = (
        df[df["has_ndr"]]["ndr_reason"].value_counts().reset_index()
    )
    ndr_pareto.columns = ["reason", "count"]
    ndr_pareto["cum_pct"] = (ndr_pareto["count"].cumsum() / ndr_pareto["count"].sum() * 100).round(1)

    # --- At-risk shipment funnel (open shipments only) -----------------------
    open_df = preds[preds["current_status"].isin(["In-Transit", "Out of delivery", "Pickup done", "Order Packed"])]
    funnel = [
        {"stage": "Open Shipments", "count": int(len(open_df))},
        {"stage": "Flagged Medium+ Risk", "count": int(open_df["risk_tier"].isin(["Medium", "High"]).sum())},
        {"stage": "Flagged High Risk", "count": int((open_df["risk_tier"] == "High").sum())},
        {"stage": "Active NDR", "count": int(open_df["has_ndr"].sum())},
        {"stage": "In Central Action Queue", "count": int(len(action_q))},
    ]

    # --- Carrier mix (volume share) -------------------------------------------
    carrier_mix = carrier_df[["carrier", "shipment_volume", "volume_share_pct", "edd_adherence_pct"]].to_dict(orient="records")

    # --- Compact per-shipment records for client-side filtering --------------
    shipments_compact = df[[
        "order_id", "carrier", "lane_class", "payment_mode", "current_status",
        "edd_met", "is_delivered", "is_rto", "is_lost", "is_open", "has_ndr", "order_date",
    ]].copy()
    shipments_compact["order_date"] = shipments_compact["order_date"].dt.strftime("%Y-%m-%d")
    risk_map = preds.set_index("order_id")["edd_risk_score"].to_dict()
    shipments_compact["edd_risk_score"] = shipments_compact["order_id"].map(risk_map)

    payload = {
        "shipments": shipments_compact.to_dict(orient="records"),
        "meta": {
            "generated_for": "AI Logistics EDD Control Tower",
            "snapshot_date": kpis["snapshot_date"],
            "edd_target": EDD_TARGET,
        },
        "kpis": kpis,
        "edd_trend_weekly": weekly.to_dict(orient="records"),
        "rto_trend_weekly": rto_weekly.to_dict(orient="records"),
        "ndr_pareto": ndr_pareto.to_dict(orient="records"),
        "at_risk_funnel": funnel,
        "carrier_scorecard": carrier_df.to_dict(orient="records"),
        "carrier_mix": carrier_mix,
        "carrier_lane_scorecard": cl_df.to_dict(orient="records"),
        "carrier_mix_recommendations": rec_df.to_dict(orient="records"),
        "lane_scorecard": lane_df[lane_df["lane_status"] != "Insufficient Sample"].to_dict(orient="records"),
        "simulation": sim_df.to_dict(orient="records"),
        "ndr_queue_summary": {
            "total": int(len(ndr_q)),
            "by_priority": ndr_q["priority"].value_counts().to_dict(),
            "by_category": ndr_q["ndr_category"].value_counts().to_dict(),
        },
        "cod_queue_summary": {
            "total": int(len(cod_q)),
            "overdue": int((cod_q["status"] == "Overdue").sum()),
            "overdue_amount": float(cod_q.loc[cod_q["status"] == "Overdue", "cod_amount"].sum()),
        },
        "action_queue_sample": action_q.head(20).to_dict(orient="records"),
        "filters": {
            "carriers": sorted(df["carrier"].unique().tolist()),
            "lanes": sorted(df["lane_class"].unique().tolist()),
            "payment_modes": sorted(df["payment_mode"].unique().tolist()),
            "statuses": sorted(df["current_status"].unique().tolist()),
        },
    }

    DASHBOARD_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_DATA_PATH, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"Wrote dashboard data -> {DASHBOARD_DATA_PATH}")
    return payload


if __name__ == "__main__":
    run()
