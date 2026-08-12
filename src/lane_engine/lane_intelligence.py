"""
Lane Intelligence Agent.

Computes a transparent Lane Health Score for every (customer_city, lane_class)
lane, and classifies each lane as Best / Watch / Deteriorating / Intervention
Required. Every verdict is backed by: What happened -> Why -> Evidence ->
Recommended action -> Expected impact.
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    FEATURED_SHIPMENTS_PATH,
    LANE_SCORECARD_PATH,
    MIN_VOLUME_FOR_LANE_INTERVENTION,
    OUTPUTS_DIR,
)

MID_POINT_WEEK = None  # set at runtime from data


def _p90(s: pd.Series) -> float:
    s = s.dropna()
    return float(np.percentile(s, 90)) if len(s) else np.nan


def compute_lane_scorecard(df: pd.DataFrame) -> pd.DataFrame:
    closed = df[~df["is_open"]].copy()
    delivered = closed[closed["is_delivered"]]

    grp_cols = ["customer_city", "lane_class"]
    rows = []
    for keys, g in closed.groupby(grp_cols):
        city, lane_class = keys
        gd = g[g["is_delivered"]]
        volume = len(g)
        edd_rate = gd["edd_met"].mean() if len(gd) else np.nan
        avg_transit = gd["transit_actual_days"].mean() if len(gd) else np.nan
        p90_transit = _p90(gd["transit_actual_days"]) if len(gd) else np.nan
        ndr_rate = g["has_ndr"].mean()
        rto_rate = g["is_rto"].mean()
        lost_rate = g["is_lost"].mean()
        late_rate = 1 - edd_rate if pd.notna(edd_rate) else np.nan
        cod_share = g["is_cod"].mean()
        cod_edd = gd[gd["is_cod"]]["edd_met"].mean() if len(gd[gd["is_cod"]]) else np.nan
        prepaid_edd = gd[~gd["is_cod"]]["edd_met"].mean() if len(gd[~gd["is_cod"]]) else np.nan

        # Trend: compare first half vs second half of the observed order
        # weeks on this lane (simple, transparent trend proxy — no black box).
        g_sorted = g.sort_values("order_date")
        half = len(g_sorted) // 2
        first_half, second_half = g_sorted.iloc[:half], g_sorted.iloc[half:]
        fh_rate = first_half[first_half["is_delivered"]]["edd_met"].mean() if half >= 5 else np.nan
        sh_rate = second_half[second_half["is_delivered"]]["edd_met"].mean() if len(second_half) >= 5 else np.nan
        trend_delta = (sh_rate - fh_rate) if pd.notna(fh_rate) and pd.notna(sh_rate) else np.nan

        rows.append({
            "customer_city": city,
            "lane_class": lane_class,
            "shipment_volume": volume,
            "edd_adherence_pct": round(edd_rate * 100, 1) if pd.notna(edd_rate) else None,
            "avg_transit_days": round(avg_transit, 2) if pd.notna(avg_transit) else None,
            "p90_transit_days": round(p90_transit, 2) if pd.notna(p90_transit) else None,
            "ndr_pct": round(ndr_rate * 100, 1),
            "rto_pct": round(rto_rate * 100, 1),
            "lost_pct": round(lost_rate * 100, 1),
            "late_pct": round(late_rate * 100, 1) if pd.notna(late_rate) else None,
            "cod_share_pct": round(cod_share * 100, 1),
            "cod_edd_adherence_pct": round(cod_edd * 100, 1) if pd.notna(cod_edd) else None,
            "prepaid_edd_adherence_pct": round(prepaid_edd * 100, 1) if pd.notna(prepaid_edd) else None,
            "trend_delta_pp": round(trend_delta * 100, 1) if pd.notna(trend_delta) else None,
        })

    lane_df = pd.DataFrame(rows)

    # --- Lane Health Score (0-100), fully transparent weighted formula -----
    # 45% EDD adherence, 25% (1 - RTO%), 20% (1 - NDR%), 10% (1 - Lost%).
    # All components already 0-100 scale.
    def _score(r):
        if r["edd_adherence_pct"] is None:
            return None
        edd_c = r["edd_adherence_pct"]
        rto_c = 100 - r["rto_pct"]
        ndr_c = 100 - r["ndr_pct"]
        lost_c = 100 - r["lost_pct"]
        return round(0.45 * edd_c + 0.25 * rto_c + 0.20 * ndr_c + 0.10 * lost_c, 1)

    lane_df["lane_health_score"] = lane_df.apply(_score, axis=1)

    def _status(r):
        if r["shipment_volume"] < MIN_VOLUME_FOR_LANE_INTERVENTION:
            return "Insufficient Sample"
        if r["lane_health_score"] is None:
            return "Insufficient Sample"
        if r["lane_health_score"] >= 90:
            return "Best Performing"
        if r["trend_delta_pp"] is not None and r["trend_delta_pp"] <= -8:
            return "Deteriorating"
        if r["lane_health_score"] < 75 or r["rto_pct"] > 25:
            return "Intervention Required"
        return "Watch"

    lane_df["lane_status"] = lane_df.apply(_status, axis=1)
    lane_df = lane_df.sort_values("lane_health_score", ascending=False, na_position="last")
    return lane_df


def explain_lane(row: pd.Series) -> dict:
    """Root-cause style explanation for a single lane row."""
    what = f"{row['customer_city']} ({row['lane_class']}) is {row['lane_status']} " \
           f"with a Lane Health Score of {row['lane_health_score']}."
    why_bits = []
    if row["edd_adherence_pct"] is not None and row["edd_adherence_pct"] < 85:
        why_bits.append(f"EDD adherence of {row['edd_adherence_pct']}% is below the 85% baseline")
    if row["rto_pct"] > 15:
        why_bits.append(f"RTO rate of {row['rto_pct']}% is elevated")
    if row["ndr_pct"] > 35:
        why_bits.append(f"NDR rate of {row['ndr_pct']}% is high")
    if row["trend_delta_pp"] is not None and row["trend_delta_pp"] < 0:
        why_bits.append(f"performance has deteriorated {abs(row['trend_delta_pp'])} pp over the observed period")
    why = "; ".join(why_bits) if why_bits else "no material deviation detected"

    evidence = (
        f"n={row['shipment_volume']} shipments, avg transit {row['avg_transit_days']}d, "
        f"P90 transit {row['p90_transit_days']}d, COD EDD {row['cod_edd_adherence_pct']}% "
        f"vs Prepaid EDD {row['prepaid_edd_adherence_pct']}%."
    )

    if row["lane_status"] == "Intervention Required":
        action = "Investigate hub processing + carrier allocation on this lane; consider carrier reallocation " \
                 "(see Carrier Optimization Agent) and proactive NDR outreach for COD orders."
        impact = f"Closing the gap to 90% lane health could recover an estimated " \
                 f"{max(0, round((90 - row['lane_health_score']) / 100 * row['shipment_volume']))} shipments/period."
    elif row["lane_status"] == "Deteriorating":
        action = "Escalate to ops for root-cause review before it becomes a systemic lane issue."
        impact = "Early intervention typically prevents further 5-10 pp erosion in EDD adherence."
    elif row["lane_status"] == "Best Performing":
        action = "Use as a benchmark lane; replicate carrier mix / process on comparable lanes."
        impact = "No action required — maintain current allocation."
    else:
        action = "Continue standard monitoring."
        impact = "No immediate action required."

    return {"what": what, "why": why, "evidence": evidence, "action": action, "expected_impact": impact}


def run() -> pd.DataFrame:
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    lane_df = compute_lane_scorecard(df)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    lane_df.to_csv(LANE_SCORECARD_PATH, index=False)
    print(f"Wrote {len(lane_df)} lane rows -> {LANE_SCORECARD_PATH}")
    print(lane_df["lane_status"].value_counts())
    worst = lane_df[lane_df["lane_status"] == "Intervention Required"].head(5)
    for _, r in worst.iterrows():
        print(explain_lane(r))
    return lane_df


if __name__ == "__main__":
    run()
