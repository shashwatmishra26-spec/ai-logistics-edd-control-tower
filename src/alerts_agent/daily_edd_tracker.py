"""
Daily EDD Breach Tracker.

Answers a set of day-to-day operational questions a Head of Logistics asks
every morning, framed from a specific "as of" vantage point
(config.EDD_TRACKING_AS_OF_DATE — the last day of Feb 2026, per a 2026-08
leadership request) rather than the pipeline's SNAPSHOT_DATE used everywhere
else (which stays fixed so it never drifts from the source workbook's
Validation-sheet cross-check):

  1. Carrier partner-wise EDD breach — who is actually causing the misses.
  2. How many shipments breached EDD yesterday.
  3. How many shipments are at risk of breaching EDD today (still open, EDD
     is today, so the day isn't over yet — this is a forward-looking count,
     not a certainty).
  4. Of yesterday's EDD cohort, how many never even got a delivery attempt.
  5. The top N lanes (by breached-shipment volume) driving the network's
     overall EDD miss rate.

"Breach" here is deliberately broader than the `edd_missed` column used
elsewhere in this repo (which only covers *delivered-late* shipments): a
promise is also broken if the shipment came back RTO, was lost, or is still
sitting open past its promised date. All four of those honestly mean "the
customer did not get what we promised, when we promised it" — see
`_is_breached()` below. Every number in this module is ACTUAL/DERIVED —
computed straight from real order_date/edd/delivery_date/status/attempt
fields, nothing here is fabricated. Where a cohort is naturally small (e.g.
a single calendar day's EDD volume is ~40-50 shipments network-wide), the
count is reported as-is rather than padded to look more "typical" — see
docs/data_assumptions.md.
"""

from pathlib import Path
import json
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    CARRIER_EDD_BREACH_PATH,
    DAILY_EDD_TRACKER_SUMMARY_PATH,
    EDD_TRACKING_AS_OF_DATE,
    FEATURED_SHIPMENTS_PATH,
    LANE_EDD_BREACH_TOP_PATH,
    MIN_VOLUME_FOR_BREACH_RANKING,
    OUTPUTS_DIR,
    TODAY_AT_RISK_SHIPMENTS_PATH,
    TOP_BREACH_LANES_COUNT,
    YESTERDAY_BREACH_SHIPMENTS_PATH,
    YESTERDAY_NOT_ATTEMPTED_SHIPMENTS_PATH,
)

SHIPMENT_LIST_COLUMNS = [
    "shipment_id", "awb", "order_id", "carrier", "customer_city", "lane_class",
    "current_status", "edd", "attempt_number", "has_ndr", "ndr_reason",
]


def _is_breached(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """A shipment has breached its EDD promise if: it was delivered after
    its EDD, it came back RTO, it was lost, or it's still open with an EDD
    that has already passed as of `as_of`. Only meaningful for rows whose
    EDD is <= as_of — a shipment promised for tomorrow hasn't broken its
    promise yet."""
    late_delivery = df["is_delivered"] & (df["delivery_date"] > df["edd"])
    still_open_overdue = df["is_open"] & (df["edd"] <= as_of)
    return late_delivery | df["is_rto"] | df["is_lost"] | still_open_overdue


def carrier_edd_breach_summary(in_scope: pd.DataFrame) -> pd.DataFrame:
    """Carrier partner-wise EDD breach — volume, breach count, breach rate,
    ranked worst-first. `in_scope` should already be filtered to
    edd <= as_of_date."""
    g = in_scope.groupby("carrier").agg(
        shipment_volume=("breached", "size"),
        breached_shipments=("breached", "sum"),
    ).reset_index()
    g["breach_pct"] = (g["breached_shipments"] / g["shipment_volume"] * 100).round(1)
    g["edd_adherence_pct"] = (100 - g["breach_pct"]).round(1)
    g["data_confidence"] = "DERIVED (carrier label is SYNTHETIC; breach outcome is ACTUAL)"
    return g.sort_values("breach_pct", ascending=False).reset_index(drop=True)


def lane_edd_breach_top(in_scope: pd.DataFrame, top_n: int, min_volume: int) -> pd.DataFrame:
    """Top N (city, lane_class) combinations ranked by *count* of breached
    shipments (not rate) — this is "which lanes are causing the most
    breaches in absolute terms", the list an ops team would actually work
    down. Lanes below `min_volume` are dropped as noise (a single-shipment
    lane at 100% breach isn't a lane problem)."""
    g = in_scope.groupby(["customer_city", "lane_class"]).agg(
        shipment_volume=("breached", "size"),
        breached_shipments=("breached", "sum"),
    ).reset_index()
    g = g[g["shipment_volume"] >= min_volume]
    g["breach_pct"] = (g["breached_shipments"] / g["shipment_volume"] * 100).round(1)
    g = g.sort_values(["breached_shipments", "breach_pct"], ascending=False).head(top_n).reset_index(drop=True)
    g.insert(0, "rank", range(1, len(g) + 1))
    return g


def _shipment_list(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["shipment_id"] = out["shipment_uid"]
    out["edd"] = out["edd"].dt.strftime("%Y-%m-%d")
    cols = [c for c in SHIPMENT_LIST_COLUMNS if c in out.columns]
    return out[cols]


def run():
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )

    as_of = pd.Timestamp(EDD_TRACKING_AS_OF_DATE)
    yesterday = as_of - pd.Timedelta(days=1)

    # --- Scope: everything whose EDD has already arrived as of `as_of` ------
    in_scope = df[df["edd"] <= as_of].copy()
    in_scope["breached"] = _is_breached(in_scope, as_of)

    carrier_breach = carrier_edd_breach_summary(in_scope)
    lane_breach_top = lane_edd_breach_top(in_scope, TOP_BREACH_LANES_COUNT, MIN_VOLUME_FOR_BREACH_RANKING)

    # --- 1. Breached EDD yesterday -------------------------------------------
    yesterday_cohort = in_scope[in_scope["edd"] == yesterday]
    yesterday_breach = yesterday_cohort[yesterday_cohort["breached"]]

    # --- 2. At risk of breaching EDD today (still open, EDD is today) -------
    today_cohort = df[df["edd"] == as_of]
    today_at_risk = today_cohort[today_cohort["is_open"]]

    # --- 3. Yesterday's EDD cohort that never got a delivery attempt --------
    yesterday_not_attempted = yesterday_cohort[yesterday_cohort["attempt_number"] == 0]

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    carrier_breach.to_csv(CARRIER_EDD_BREACH_PATH, index=False)
    lane_breach_top.to_csv(LANE_EDD_BREACH_TOP_PATH, index=False)
    _shipment_list(yesterday_breach).to_csv(YESTERDAY_BREACH_SHIPMENTS_PATH, index=False)
    _shipment_list(today_at_risk).to_csv(TODAY_AT_RISK_SHIPMENTS_PATH, index=False)
    _shipment_list(yesterday_not_attempted).to_csv(YESTERDAY_NOT_ATTEMPTED_SHIPMENTS_PATH, index=False)

    summary = {
        "as_of_date": str(as_of.date()),
        "yesterday_date": str(yesterday.date()),
        "network_breach_pct_through_as_of": round(float(in_scope["breached"].mean() * 100), 1) if len(in_scope) else None,
        "network_shipments_in_scope": int(len(in_scope)),
        "network_shipments_breached": int(in_scope["breached"].sum()),
        "yesterday_edd_cohort_size": int(len(yesterday_cohort)),
        "yesterday_breach_count": int(len(yesterday_breach)),
        "yesterday_breach_pct": round(float(len(yesterday_breach) / len(yesterday_cohort) * 100), 1) if len(yesterday_cohort) else None,
        "today_edd_cohort_size": int(len(today_cohort)),
        "today_at_risk_count": int(len(today_at_risk)),
        "yesterday_not_attempted_count": int(len(yesterday_not_attempted)),
        "data_confidence": (
            "ACTUAL/DERIVED — computed directly from order_date/edd/delivery_date/current_status/"
            "attempt_number, no fabricated values. 'Breach' includes late-delivered + RTO + Lost + "
            "still-open-past-EDD (broader than the edd_missed column used for the headline EDD "
            "adherence KPI, which only covers delivered-late shipments — see docs/data_assumptions.md)."
        ),
    }
    with open(DAILY_EDD_TRACKER_SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"Daily EDD tracker as-of {summary['as_of_date']} (yesterday={summary['yesterday_date']}):")
    print(f"  Yesterday EDD cohort: {summary['yesterday_edd_cohort_size']}, breached: {summary['yesterday_breach_count']} ({summary['yesterday_breach_pct']}%)")
    print(f"  Today EDD cohort: {summary['today_edd_cohort_size']}, at risk (still open): {summary['today_at_risk_count']}")
    print(f"  Yesterday EDD cohort never attempted: {summary['yesterday_not_attempted_count']}")
    print(f"Wrote carrier breach summary ({len(carrier_breach)} carriers) -> {CARRIER_EDD_BREACH_PATH}")
    print(f"Wrote top {len(lane_breach_top)} breach lanes -> {LANE_EDD_BREACH_TOP_PATH}")
    print(f"Wrote tracker summary -> {DAILY_EDD_TRACKER_SUMMARY_PATH}")
    return summary, carrier_breach, lane_breach_top


if __name__ == "__main__":
    run()
