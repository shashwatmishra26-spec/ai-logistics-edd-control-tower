"""
Central AI Logistics Decision Engine.

Combines the outputs of every agent (EDD risk predictions, Lane Intelligence,
Carrier Optimization, NDR Recovery, COD Remittance) into ONE ranked action
queue, and answers the standing operational questions a Head of Logistics
asks every morning:

  - Which shipments are likely to miss EDD, and why?
  - Which lanes are deteriorating?
  - Which carriers are causing the problem?
  - Should carrier allocation change?
  - Which NDR shipments require intervention?
  - Which COD shipments require remittance follow-up?
  - Which action should happen first?
  - What is the expected impact?

Output: outputs/central_action_queue.csv + outputs/kpi_summary.json (feeds
the dashboard).
"""

from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    ACTION_QUEUE_PATH,
    BREACH_ALERT_QUEUE_PATH,
    CARRIER_MIX_RECOMMENDATIONS_PATH,
    CARRIER_SCORECARD_PATH,
    COD_QUEUE_PATH,
    EDD_TARGET,
    FEATURED_SHIPMENTS_PATH,
    IVR_CALL_SHEET_PATH,
    KPI_SUMMARY_PATH,
    LANE_BREACH_SUMMARY_PATH,
    LANE_SCORECARD_PATH,
    NDR_QUEUE_PATH,
    OUTPUTS_DIR,
    PADDING_RECOMMENDATIONS_PATH,
    PREDICTIONS_PATH,
)

PRIORITY_RANK = {"P1 - Urgent": 0, "P2 - High": 1, "P3 - Standard": 2}


def build_action_queue(preds, lane_df, rec_df, ndr_q, cod_q, breach_q=None, padding_df=None) -> pd.DataFrame:
    actions = []

    # 0. In-transit EDD breach alerts — the primary, forward-looking queue.
    # These shipments are a SUBSET of "high risk open" (below) filtered down
    # to P1/P2 urgency by days-to-EDD, so they're listed first but do not
    # double-count against item 1's broader risk-tier action.
    if breach_q is not None and len(breach_q):
        urgent_breach = breach_q[breach_q["alert_priority"].isin(["P1 - Urgent", "P2 - High"])]
        for _, r in urgent_breach.iterrows():
            actions.append({
                "source_agent": "EDD Breach Alert Agent",
                "entity": r["awb"],
                "issue": "EDD already passed, still in transit" if r["edd_already_breached"]
                         else f"{r['risk_tier']} risk, EDD in {int(r['days_to_edd'])}d",
                "recommended_action": f"{r['recommended_action']} (push notification + care-team update queued)",
                "priority": r["alert_priority"],
                "expected_impact": "Proactive customer contact before EDD miss becomes a complaint/RTO",
                "confidence": "AI_PREDICTED (risk score) + MOCK (message content)",
            })

    # 0b. Lane EDD padding recommendations — direct, transparent suggestions.
    if padding_df is not None and len(padding_df):
        real_padding = padding_df[padding_df["recommended_padding_days"] > 0]
        for _, r in real_padding.iterrows():
            actions.append({
                "source_agent": "Lane Padding Agent",
                "entity": f"{r['customer_city']} ({r['lane_class']})",
                "issue": f"P90 transit {r['p90_actual_transit_days']}d vs {r['current_transit_sla_days']}d SLA "
                         f"— gap is transit-time driven",
                "recommended_action": f"Add {int(r['recommended_padding_days'])} day(s) of EDD padding "
                                       f"(new SLA {int(r['new_transit_sla_days'])}d)",
                "priority": "P2 - High" if r["manual_review_needed"] else "P3 - Standard",
                "expected_impact": f"+{r['projected_lift_pp']}pp on this lane (SIMULATED backtest)",
                "confidence": "SIMULATED",
            })

    # 1. High-risk open shipments from the EDD Risk Agent
    high_risk_open = preds[(preds["risk_tier"] == "High") & (~preds["current_status"].isin(["Delivered", "RTO", "Lost"]))]
    for _, r in high_risk_open.iterrows():
        actions.append({
            "source_agent": "EDD Risk Agent",
            "entity": r["awb"],
            "issue": f"High EDD-miss risk ({r['edd_risk_score']}%)",
            "recommended_action": r["recommended_action"],
            "priority": "P1 - Urgent" if r["edd_risk_score"] >= 80 else "P2 - High",
            "expected_impact": "Prevents 1 EDD miss / possible RTO",
            "confidence": "AI_PREDICTED",
        })

    # 2. Lane interventions
    lane_issues = lane_df[lane_df["lane_status"].isin(["Intervention Required", "Deteriorating"])]
    for _, r in lane_issues.iterrows():
        actions.append({
            "source_agent": "Lane Intelligence Agent",
            "entity": f"{r['customer_city']} ({r['lane_class']})",
            "issue": f"Lane health {r['lane_health_score']} — {r['lane_status']}",
            "recommended_action": "Investigate hub + carrier allocation; prioritize NDR outreach for COD",
            "priority": "P1 - Urgent" if r["lane_status"] == "Intervention Required" else "P2 - High",
            "expected_impact": f"{r['shipment_volume']} shipments/period at risk",
            "confidence": "DERIVED (statistical lane scorecard)",
        })

    # 3. Carrier mix changes
    for _, r in rec_df[rec_df["recommended_mix"] != "No change"].iterrows():
        actions.append({
            "source_agent": "Carrier Optimization Agent",
            "entity": r["lane_class"],
            "issue": "Sustained carrier underperformance on lane",
            "recommended_action": r["recommended_mix"],
            "priority": "P2 - High",
            "expected_impact": f"+{r['expected_edd_impact_pp']}pp lane EDD adherence",
            "confidence": r["confidence"],
        })

    # 4. NDR interventions (P1/P2 only — P3 is low-touch monitoring)
    for _, r in ndr_q[ndr_q["priority"].isin(["P1 - Urgent", "P2 - High"])].iterrows():
        actions.append({
            "source_agent": "NDR Recovery Agent",
            "entity": r["awb"],
            "issue": f"Active NDR: {r['ndr_reason']}",
            "recommended_action": r["recommended_action"],
            "priority": r["priority"],
            "expected_impact": f"Reattempt success probability {r['reattempt_success_probability']:.0%}",
            "confidence": r["ai_confidence"],
        })

    # 5. COD remittance overdue
    overdue = cod_q[cod_q["status"] == "Overdue"]
    if len(overdue):
        actions.append({
            "source_agent": "COD Remittance Agent",
            "entity": f"{len(overdue)} shipments",
            "issue": f"Overdue COD remittance, total Rs {overdue['cod_amount'].sum():,.0f}",
            "recommended_action": "Send remittance follow-up to carriers (see cod_remittance_queue.csv)",
            "priority": "P1 - Urgent" if overdue["cod_amount"].sum() > 50000 else "P2 - High",
            "expected_impact": f"Recover Rs {overdue['cod_amount'].sum():,.0f} working capital",
            "confidence": "ACTUAL (rule-based trigger)",
        })

    df = pd.DataFrame(actions)
    df["priority_rank"] = df["priority"].map(PRIORITY_RANK)
    df = df.sort_values("priority_rank").drop(columns="priority_rank").reset_index(drop=True)
    df.insert(0, "action_id", [f"ACT-{i+1:05d}" for i in range(len(df))])
    return df


def build_kpi_summary(df, preds, lane_df, carrier_df, ndr_q, cod_q,
                       breach_q=None, lane_breach_df=None, padding_df=None, ivr_sheet=None) -> dict:
    delivered = df[df["is_delivered"]]
    baseline_edd = round(delivered["edd_met"].sum() / len(delivered), 4)
    open_shipments = df[df["is_open"]]

    kpis = {
        "snapshot_date": str(df["order_date"].max().date()) if len(df) else None,
        "total_shipments": int(len(df)),
        "actual": {
            "edd_adherence": baseline_edd,
            "edd_target": EDD_TARGET,
            "gap_to_target_pp": round((EDD_TARGET - baseline_edd) * 100, 1),
            "delivered": int(df["is_delivered"].sum()),
            "rto": int(df["is_rto"].sum()),
            "lost": int(df["is_lost"].sum()),
            "open_in_transit": int(open_shipments.shape[0]),
            "ndr_pct_of_all": round(df["has_ndr"].mean() * 100, 1),
            "rto_pct_of_all": round(df["is_rto"].mean() * 100, 1),
            "cod_rto_pct": round(df[df["is_cod"]]["is_rto"].mean() * 100, 1),
            "prepaid_rto_pct": round(df[~df["is_cod"]]["is_rto"].mean() * 100, 1),
        },
        "predicted": {
            "open_shipments_high_risk": int(
                preds.loc[preds["current_status"].isin(["In-Transit", "Out of delivery", "Pickup done", "Order Packed"]), "risk_tier"].eq("High").sum()
            ),
            "open_shipments_medium_risk": int(
                preds.loc[preds["current_status"].isin(["In-Transit", "Out of delivery", "Pickup done", "Order Packed"]), "risk_tier"].eq("Medium").sum()
            ),
        },
        "lane": {
            "lanes_analyzed": int(len(lane_df)),
            "lanes_intervention_required": int((lane_df["lane_status"] == "Intervention Required").sum()),
            "lanes_deteriorating": int((lane_df["lane_status"] == "Deteriorating").sum()),
            "lanes_best_performing": int((lane_df["lane_status"] == "Best Performing").sum()),
        },
        "carrier": carrier_df.to_dict(orient="records"),
        "ndr_queue": {
            "open_notifications": int(len(ndr_q)),
            "p1_urgent": int((ndr_q["priority"] == "P1 - Urgent").sum()),
            "p2_high": int((ndr_q["priority"] == "P2 - High").sum()),
            "ivr_call_sheet_size": int(len(ivr_sheet)) if ivr_sheet is not None else None,
            "care_team_digest_generated": ivr_sheet is not None,
        },
        "cod_remittance": {
            "overdue_count": int((cod_q["status"] == "Overdue").sum()),
            "overdue_amount": float(cod_q.loc[cod_q["status"] == "Overdue", "cod_amount"].sum()),
        },
    }

    if breach_q is not None:
        kpis["breach_alerts"] = {
            "in_transit_at_risk": int(len(breach_q)),
            "already_breached_in_transit": int(breach_q["edd_already_breached"].sum()) if len(breach_q) else 0,
            "p1_urgent": int((breach_q["alert_priority"] == "P1 - Urgent").sum()) if len(breach_q) else 0,
            "p2_high": int((breach_q["alert_priority"] == "P2 - High").sum()) if len(breach_q) else 0,
            "lanes_breach_risk": int((lane_breach_df["lane_status"] == "Breach Risk").sum())
                                  if lane_breach_df is not None else None,
        }
    if padding_df is not None:
        real_padding = padding_df[padding_df["recommended_padding_days"] > 0] if len(padding_df) else padding_df
        kpis["padding_recommendations"] = {
            "lanes_evaluated": int(len(padding_df)),
            "lanes_recommended_for_padding": int(len(real_padding)) if len(padding_df) else 0,
            "avg_recommended_padding_days": round(float(real_padding["recommended_padding_days"].mean()), 1)
                                             if len(real_padding) else 0,
            "max_projected_lift_pp": float(real_padding["projected_lift_pp"].max()) if len(real_padding) else 0,
        }

    return kpis


def run():
    df = pd.read_csv(FEATURED_SHIPMENTS_PATH, parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"])
    preds = pd.read_csv(PREDICTIONS_PATH)
    lane_df = pd.read_csv(LANE_SCORECARD_PATH)
    carrier_df = pd.read_csv(CARRIER_SCORECARD_PATH)
    rec_df = pd.read_csv(CARRIER_MIX_RECOMMENDATIONS_PATH)
    ndr_q = pd.read_csv(NDR_QUEUE_PATH)
    cod_q = pd.read_csv(COD_QUEUE_PATH)
    breach_q = pd.read_csv(BREACH_ALERT_QUEUE_PATH, parse_dates=["edd"])
    lane_breach_df = pd.read_csv(LANE_BREACH_SUMMARY_PATH)
    padding_df = pd.read_csv(PADDING_RECOMMENDATIONS_PATH)
    ivr_sheet = pd.read_csv(IVR_CALL_SHEET_PATH)

    action_queue = build_action_queue(preds, lane_df, rec_df, ndr_q, cod_q, breach_q, padding_df)
    kpis = build_kpi_summary(df, preds, lane_df, carrier_df, ndr_q, cod_q,
                              breach_q, lane_breach_df, padding_df, ivr_sheet)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    action_queue.to_csv(ACTION_QUEUE_PATH, index=False)
    with open(KPI_SUMMARY_PATH, "w") as f:
        json.dump(kpis, f, indent=2, default=str)

    print(f"Wrote {len(action_queue)} actions -> {ACTION_QUEUE_PATH}")
    print(action_queue["priority"].value_counts())
    print(f"Wrote KPI summary -> {KPI_SUMMARY_PATH}")
    print(json.dumps(kpis["actual"], indent=2))
    return action_queue, kpis


if __name__ == "__main__":
    run()
