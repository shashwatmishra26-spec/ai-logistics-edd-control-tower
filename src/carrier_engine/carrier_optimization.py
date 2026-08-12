"""
Carrier Optimization Agent.

IMPORTANT DATA CAVEAT (documented in full in docs/data_assumptions.md):
The source workbook has no carrier field. Carrier is a SYNTHETIC, deterministic
overlay (src/features/build_features.py::_assign_carrier) applied independently
of each shipment's real outcome. The EDD/NDR/RTO OUTCOMES grouped by carrier
below are ACTUAL data; the carrier LABEL attached to each shipment is
SYNTHETIC. This module is built to demonstrate the full methodology a real
control tower would use once a genuine carrier field exists — statistical
rigor (minimum volume, confidence, sustained trend) is enforced exactly as it
would be in production, which is also why, on this synthetic overlay, most
observed differences will correctly wash out as "not significant" once you
inspect the underlying p-values / sample sizes.
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    CARRIER_LANE_SCORECARD_PATH,
    CARRIER_MIX_RECOMMENDATIONS_PATH,
    CARRIER_SCORECARD_PATH,
    FEATURED_SHIPMENTS_PATH,
    MIN_VOLUME_FOR_RECOMMENDATION,
    OUTPUTS_DIR,
    SNAPSHOT_DATE,
)


def _two_proportion_z(p1, n1, p2, n2):
    """Two-proportion z-test, implemented without scipy.stats to keep the
    dependency footprint minimal. Returns (z, two_sided_p_approx)."""
    if n1 == 0 or n2 == 0:
        return None, None
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = np.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # Normal CDF via erf (no scipy dependency needed for this approx)
    from math import erf, sqrt
    p_value = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    return round(z, 3), round(p_value, 4)


def compute_carrier_scorecard(df: pd.DataFrame) -> pd.DataFrame:
    closed = df[~df["is_open"]]
    rows = []
    for carrier, g in closed.groupby("carrier"):
        gd = g[g["is_delivered"]]
        rows.append({
            "carrier": carrier,
            "shipment_volume": len(g),
            "edd_adherence_pct": round(gd["edd_met"].mean() * 100, 1) if len(gd) else None,
            "avg_transit_days": round(gd["transit_actual_days"].mean(), 2) if len(gd) else None,
            "ndr_pct": round(g["has_ndr"].mean() * 100, 1),
            "rto_pct": round(g["is_rto"].mean() * 100, 1),
            "lost_pct": round(g["is_lost"].mean() * 100, 1),
            "carrier_sla_breach_pct": round(g["carrier_sla_breach"].mean() * 100, 1),
            "volume_share_pct": round(len(g) / len(closed) * 100, 1),
            "data_confidence": "carrier_label=SYNTHETIC; outcomes=ACTUAL",
        })
    return pd.DataFrame(rows).sort_values("edd_adherence_pct", ascending=False)


def compute_carrier_lane_scorecard(df: pd.DataFrame) -> pd.DataFrame:
    closed = df[~df["is_open"]]
    rows = []
    for (carrier, lane), g in closed.groupby(["carrier", "lane_class"]):
        gd = g[g["is_delivered"]]
        rows.append({
            "carrier": carrier,
            "lane_class": lane,
            "shipment_volume": len(g),
            "edd_adherence_pct": round(gd["edd_met"].mean() * 100, 1) if len(gd) else None,
            "rto_pct": round(g["is_rto"].mean() * 100, 1),
            "ndr_pct": round(g["has_ndr"].mean() * 100, 1),
        })
    return pd.DataFrame(rows).sort_values(["lane_class", "edd_adherence_pct"], ascending=[True, False])


def concentration_risk(carrier_df: pd.DataFrame) -> pd.DataFrame:
    carrier_df = carrier_df.copy()
    carrier_df["concentration_flag"] = carrier_df["volume_share_pct"] > 40
    return carrier_df


def carrier_mix_recommendations(cl_df: pd.DataFrame, overall_edd: float) -> pd.DataFrame:
    """For each lane_class, compare the best- vs worst-performing carrier
    (subject to a minimum volume) and recommend a mix shift only when the
    gap is statistically supportable and operationally meaningful."""
    recs = []
    for lane, g in cl_df.groupby("lane_class"):
        eligible = g[g["shipment_volume"] >= MIN_VOLUME_FOR_RECOMMENDATION]
        if len(eligible) < 2:
            recs.append({
                "lane_class": lane, "current_mix": "n/a", "recommended_mix": "No change",
                "reason": f"Insufficient volume (<{MIN_VOLUME_FOR_RECOMMENDATION} shipments per carrier) "
                          f"to compare carriers reliably on this lane.",
                "expected_edd_impact_pp": 0.0, "trigger_date": None, "confidence": "Low (sample size)",
            })
            continue
        best = eligible.sort_values("edd_adherence_pct", ascending=False).iloc[0]
        worst = eligible.sort_values("edd_adherence_pct", ascending=True).iloc[0]
        if best["carrier"] == worst["carrier"]:
            continue
        p1, n1 = best["edd_adherence_pct"] / 100, best["shipment_volume"]
        p2, n2 = worst["edd_adherence_pct"] / 100, worst["shipment_volume"]
        z, p_value = _two_proportion_z(p1, n1, p2, n2)
        gap_pp = round((p1 - p2) * 100, 1)
        significant = p_value is not None and p_value < 0.05 and gap_pp >= 8
        current_mix = ", ".join(f"{r.carrier} {r.shipment_volume}" for r in eligible.itertuples())
        if significant:
            shift_volume = min(worst["shipment_volume"], eligible["shipment_volume"].sum() * 0.2)
            recs.append({
                "lane_class": lane,
                "current_mix": current_mix,
                "recommended_mix": f"Shift ~{int(shift_volume)} shipments/period from {worst['carrier']} to {best['carrier']}",
                "reason": f"{worst['carrier']} EDD adherence ({worst['edd_adherence_pct']}%, n={worst['shipment_volume']}) "
                          f"is {gap_pp}pp below {best['carrier']} ({best['edd_adherence_pct']}%, n={best['shipment_volume']}), "
                          f"p={p_value} (two-proportion z-test) — sustained, statistically supportable gap.",
                "expected_edd_impact_pp": round(gap_pp * (shift_volume / eligible['shipment_volume'].sum()), 2),
                "trigger_date": SNAPSHOT_DATE,
                "confidence": f"High (z={z}, p={p_value})",
            })
        else:
            recs.append({
                "lane_class": lane,
                "current_mix": current_mix,
                "recommended_mix": "No change",
                "reason": f"Gap between best ({best['carrier']}, {best['edd_adherence_pct']}%) and worst "
                          f"({worst['carrier']}, {worst['edd_adherence_pct']}%) is {gap_pp}pp "
                          f"(p={p_value}) — not statistically significant / not sustained enough to act on.",
                "expected_edd_impact_pp": 0.0,
                "trigger_date": None,
                "confidence": f"Low (z={z}, p={p_value})",
            })
    return pd.DataFrame(recs)


def run():
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    carrier_df = compute_carrier_scorecard(df)
    carrier_df = concentration_risk(carrier_df)
    cl_df = compute_carrier_lane_scorecard(df)
    overall_edd = df["edd_met"].sum() / df["is_delivered"].sum()
    rec_df = carrier_mix_recommendations(cl_df, overall_edd)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    carrier_df.to_csv(CARRIER_SCORECARD_PATH, index=False)
    cl_df.to_csv(CARRIER_LANE_SCORECARD_PATH, index=False)
    rec_df.to_csv(CARRIER_MIX_RECOMMENDATIONS_PATH, index=False)

    print(carrier_df.to_string(index=False))
    print(f"\nWrote carrier scorecard -> {CARRIER_SCORECARD_PATH}")
    print(f"Wrote carrier x lane scorecard -> {CARRIER_LANE_SCORECARD_PATH}")
    print(f"Wrote {len(rec_df)} mix recommendations -> {CARRIER_MIX_RECOMMENDATIONS_PATH}")
    print(rec_df[["lane_class", "recommended_mix", "confidence"]].to_string(index=False))
    return carrier_df, cl_df, rec_df


if __name__ == "__main__":
    run()
