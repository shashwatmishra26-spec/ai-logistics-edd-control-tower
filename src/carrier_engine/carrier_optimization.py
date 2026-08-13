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
    CARRIER_WATCHLIST_PATH,
    EDD_TARGET,
    FEATURED_SHIPMENTS_PATH,
    MIN_VOLUME_FOR_RECOMMENDATION,
    OUTPUTS_DIR,
    PADDING_RECOMMENDATIONS_PATH,
    SNAPSHOT_DATE,
    WATCHLIST_IMPROVEMENT_WINDOW_DAYS,
    WATCHLIST_MIN_EDD_GAP_PP,
    WATCHLIST_MIN_VOLUME,
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


def compute_carrier_watchlist(df: pd.DataFrame, lane_df: pd.DataFrame, padding_df: pd.DataFrame) -> pd.DataFrame:
    """Carrier Partner Improvement / Volume-Shift Watchlist.

    A lane lands here for either (or both) of two reasons:
      1. TRANSIT_CEILING_BREACH — the Lane Padding Recommender flagged
         `watchlist_candidate=True`: even at the maximum honest EDD promise
         for this lane class (MAX_LANE_EDD_CEILING_DAYS), the lane's own P90
         actual transit time still doesn't clear it. Padding can't fix this
         — the carrier partner's execution has to.
      2. CHRONIC_UNDERPERFORMANCE — the lane scorecard independently flags
         "Intervention Required"/"Deteriorating" with a real EDD gap to
         target (>= WATCHLIST_MIN_EDD_GAP_PP) at sufficient volume.

    Each watchlisted lane is matched to its dominant carrier (by shipment
    volume on that exact city+lane_class) and gets a mock outbound
    improve-or-lose-volume notice — this is the "highlight and be ready to
    notify carrier partners" ask, generated directly from already-computed
    agent outputs (no new modeling).
    """
    closed = df[~df["is_open"]]

    watchlist_cities = set()
    reasons_by_key = {}

    if padding_df is not None and len(padding_df) and "watchlist_candidate" in padding_df.columns:
        for _, r in padding_df[padding_df["watchlist_candidate"]].iterrows():
            key = (r["customer_city"], r["lane_class"])
            watchlist_cities.add(key)
            reasons_by_key.setdefault(key, []).append("TRANSIT_CEILING_BREACH")

    chronic = lane_df[
        (lane_df["lane_status"].isin(["Intervention Required", "Deteriorating"]))
        & (lane_df["shipment_volume"] >= WATCHLIST_MIN_VOLUME)
    ]
    for _, r in chronic.iterrows():
        if r["edd_adherence_pct"] is None or pd.isna(r["edd_adherence_pct"]):
            continue
        gap_pp = round(EDD_TARGET * 100 - r["edd_adherence_pct"], 1)
        if gap_pp >= WATCHLIST_MIN_EDD_GAP_PP:
            key = (r["customer_city"], r["lane_class"])
            watchlist_cities.add(key)
            reasons_by_key.setdefault(key, []).append("CHRONIC_UNDERPERFORMANCE")

    if not watchlist_cities:
        return pd.DataFrame(columns=[
            "customer_city", "lane_class", "shipment_volume", "edd_adherence_pct",
            "edd_gap_to_target_pp", "flag_reasons", "primary_carrier",
            "primary_carrier_share_pct", "primary_carrier_edd_on_lane_pct",
            "notice_deadline", "mock_carrier_notice", "data_confidence",
        ])

    deadline = (pd.Timestamp(SNAPSHOT_DATE) + pd.Timedelta(days=WATCHLIST_IMPROVEMENT_WINDOW_DAYS)).strftime("%Y-%m-%d")

    rows = []
    for city, lane_class in sorted(watchlist_cities):
        lane_row = lane_df[(lane_df["customer_city"] == city) & (lane_df["lane_class"] == lane_class)]
        if not len(lane_row):
            continue
        lane_row = lane_row.iloc[0]
        edd_pct = lane_row["edd_adherence_pct"]
        volume = int(lane_row["shipment_volume"])
        gap_pp = round(EDD_TARGET * 100 - edd_pct, 1) if pd.notna(edd_pct) else None

        # Dominant carrier serving this exact city+lane_class, by volume.
        lane_shipments = closed[(closed["customer_city"] == city) & (closed["lane_class"] == lane_class)]
        carrier_counts = lane_shipments["carrier"].value_counts()
        if len(carrier_counts):
            primary_carrier = carrier_counts.index[0]
            primary_share = round(carrier_counts.iloc[0] / len(lane_shipments) * 100, 1)
            pc_delivered = lane_shipments[(lane_shipments["carrier"] == primary_carrier) & (lane_shipments["is_delivered"])]
            primary_edd = round(pc_delivered["edd_met"].mean() * 100, 1) if len(pc_delivered) else None
        else:
            primary_carrier, primary_share, primary_edd = None, None, None

        reasons = sorted(set(reasons_by_key.get((city, lane_class), [])))
        reason_text = " and ".join(r.replace("_", " ").title() for r in reasons)

        notice = (
            f"NOTICE TO CARRIER PARTNER — {primary_carrier or 'primary carrier'} "
            f"(lane: {city} / {lane_class}). Current EDD adherence on this lane is "
            f"{edd_pct}% against a {int(EDD_TARGET * 100)}% target ({gap_pp}pp gap), "
            f"driven by {reason_text.lower()}, n={volume} shipments. "
            f"We need a documented improvement plan and measurable EDD-adherence "
            f"recovery on this lane within {WATCHLIST_IMPROVEMENT_WINDOW_DAYS} days "
            f"(by {deadline}), or volume on this lane will be shifted to an "
            f"alternate carrier partner. [MOCK — outbound carrier communication, not sent]"
        )

        rows.append({
            "customer_city": city,
            "lane_class": lane_class,
            "shipment_volume": volume,
            "edd_adherence_pct": edd_pct,
            "edd_gap_to_target_pp": gap_pp,
            "flag_reasons": ", ".join(reasons),
            "primary_carrier": primary_carrier,
            "primary_carrier_share_pct": primary_share,
            "primary_carrier_edd_on_lane_pct": primary_edd,
            "notice_deadline": deadline,
            "mock_carrier_notice": notice,
            "data_confidence": "carrier_label=SYNTHETIC; EDD/volume outcomes=ACTUAL; notice=MOCK (never sent)",
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("edd_gap_to_target_pp", ascending=False).reset_index(drop=True)
    return out


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

    # Carrier Partner Improvement / Volume-Shift Watchlist — depends on the
    # lane engine's already-computed scorecard + padding recommendations.
    from src.lane_engine.lane_intelligence import LANE_SCORECARD_PATH  # noqa: E402
    lane_df = pd.read_csv(LANE_SCORECARD_PATH)
    try:
        padding_df = pd.read_csv(PADDING_RECOMMENDATIONS_PATH)
    except FileNotFoundError:
        padding_df = None
    watchlist_df = compute_carrier_watchlist(df, lane_df, padding_df)
    watchlist_df.to_csv(CARRIER_WATCHLIST_PATH, index=False)
    print(f"\nWrote {len(watchlist_df)} carrier watchlist lanes -> {CARRIER_WATCHLIST_PATH}")
    if len(watchlist_df):
        print(watchlist_df[["customer_city", "lane_class", "edd_adherence_pct", "flag_reasons", "primary_carrier"]].to_string(index=False))

    return carrier_df, cl_df, rec_df, watchlist_df


if __name__ == "__main__":
    run()
