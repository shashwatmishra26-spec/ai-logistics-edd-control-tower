"""
Data quality + cleaning layer.

Takes the normalized raw DataFrame from `ingest.py` and produces an
analysis-ready DataFrame with:
  - de-duplicated shipment records
  - validated / coerced date columns
  - fixed or flagged bad pincodes
  - flagged (never silently corrected) amount outliers
  - masked customer PII
  - a `dq_flags` column documenting every fix applied to that row

Nothing here fabricates a delivery outcome. Rows with data-quality issues are
flagged, not dropped, unless they are exact duplicate AWBs (in which case the
later record is kept as the authoritative one).
"""

from pathlib import Path
import hashlib
import re
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import CLEAN_SHIPMENTS_PATH, PROCESSED_DIR  # noqa: E402
from src.data.ingest import load_raw_shipments  # noqa: E402

PINCODE_RE = re.compile(r"^\d{6}$")


def _flag(flags: pd.Series, mask: pd.Series, label: str) -> pd.Series:
    """Append `label` to the dq_flags string for rows where mask is True."""
    flags = flags.copy()
    flags.loc[mask] = flags.loc[mask].apply(
        lambda existing: f"{existing};{label}" if existing else label
    )
    return flags


def _hash_id(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:10].upper()


def clean_shipments(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    dq_flags = pd.Series([""] * len(df), index=df.index)

    # --- 1. Duplicate order_id / AWB -------------------------------------
    dup_mask = df.duplicated(subset=["order_id"], keep="last")
    n_dupes = int(dup_mask.sum())
    # Flag the SURVIVING row (not the discarded one, which is dropped and
    # would otherwise carry the flag into the void) so downstream consumers
    # can see which shipments had a duplicate AWB resolved.
    survivor_had_dupe = df["order_id"].isin(df.loc[dup_mask, "order_id"]) & ~dup_mask
    dq_flags = _flag(dq_flags, survivor_had_dupe, "duplicate_order_id_dropped")
    df = df.loc[~dup_mask].copy()
    dq_flags = dq_flags.loc[df.index]

    # --- 2. Date coercion + validity --------------------------------------
    date_cols = ["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"]
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    # Logical date-order checks (pickup should not precede order, delivery
    # should not precede pickup). We flag violations rather than dropping —
    # in production these would route to a data-quality alert, not silent
    # deletion.
    bad_pickup = df["pickup_date"].notna() & (df["pickup_date"] < df["order_date"])
    dq_flags = _flag(dq_flags, bad_pickup, "pickup_before_order_date")

    bad_delivery = (
        df["delivery_date"].notna()
        & df["pickup_date"].notna()
        & (df["delivery_date"] < df["pickup_date"])
    )
    dq_flags = _flag(dq_flags, bad_delivery, "delivery_before_pickup_date")

    # --- 3. Pincode validation --------------------------------------------
    pin_str = df["customer_pincode"].astype(str).str.strip()
    bad_pin_mask = ~pin_str.str.match(PINCODE_RE)
    dq_flags = _flag(dq_flags, bad_pin_mask, "invalid_customer_pincode")
    # For invalid pincodes we cannot safely derive a 6-digit code (the raw
    # value observed here is literally a city name typed into the pincode
    # field). We keep the row (it still has a valid city + outcome) but null
    # out the pincode so downstream lane/distance logic falls back to the
    # city-name mapping instead of guessing digits.
    df.loc[bad_pin_mask, "customer_pincode"] = pd.NA
    df["customer_pincode"] = df["customer_pincode"].astype("string")

    df["warehouse_pincode"] = df["warehouse_pincode"].astype(str).str.zfill(6)

    # --- 4. Amount outliers (flag only, never fabricate a "corrected"
    #        value — see docs/data_assumptions.md) -------------------------
    # Two tiers:
    #  - "high_value_order": statistically unusual but plausible (premium
    #    SKU, bulk order). Useful signal for COD/RTO risk, not a data bug.
    #  - "likely_data_entry_error": magnitude implausible for e-commerce
    #    parcels (e.g. Rs 3,60,002 for a single package) — almost certainly
    #    an extra digit typed at source. Flagged, never silently corrected.
    q1, q3 = df["package_amount"].quantile([0.25, 0.75])
    iqr = q3 - q1
    high_value_fence = q3 + 3 * iqr
    error_fence = 15000  # domain judgement: no legitimate parcel SKU here exceeds this

    high_value_mask = df["package_amount"] > high_value_fence
    dq_flags = _flag(dq_flags, high_value_mask, "high_value_order")

    error_mask = df["package_amount"] > error_fence
    dq_flags = _flag(dq_flags, error_mask, "likely_data_entry_error_amount")
    amount_outlier_mask = error_mask

    # --- 5. Missing-value semantics ----------------------------------------
    # first_attempt_date / delivery_date are legitimately null for shipments
    # that never reached an attempt / were never delivered (RTO before first
    # attempt, still in transit, lost, etc). We do NOT impute these — a null
    # here is operationally meaningful, not missing data.
    still_pending_no_attempt = df["first_attempt_date"].isna() & df["current_status"].isin(
        ["Order Packed", "Pickup done", "In-Transit"]
    )
    dq_flags = _flag(dq_flags, still_pending_no_attempt, "not_yet_attempted")

    # NDR reason is null for ~63% of rows by design (only NDR/RTO/Out for
    # delivery shipments carry a reason). Make that explicit rather than
    # leaving a silent NaN.
    df["ndr_reason"] = df["ndr_reason"].fillna("Not Applicable")

    # --- 6. Status text normalization --------------------------------------
    df["current_status"] = df["current_status"].str.strip()
    df["payment_mode"] = df["payment_mode"].str.strip().str.title()

    # --- 7. PII masking ------------------------------------------------------
    df["shipment_uid"] = df["order_id"].astype(str).apply(_hash_id)
    df["customer_name_masked"] = df["customer_name"].apply(
        lambda n: (str(n).strip().split()[0][0] + "*** " + "C" + "***") if pd.notna(n) else "N/A"
    )
    df["customer_phone_masked"] = df["customer_phone"].astype(str).apply(
        lambda p: "XXXXXX" + p[-4:] if len(p) >= 4 else "XXXXXXXXXX"
    )
    df["customer_address_masked"] = "[REDACTED - " + df["customer_city"].astype(str) + "]"
    df = df.drop(columns=["customer_name", "customer_phone", "customer_address"])

    df["dq_flags"] = dq_flags
    df["data_confidence_core"] = "ACTUAL"  # these are the fields taken as-is from source

    meta = {
        "rows_in": len(df) + n_dupes,
        "duplicate_awbs_removed": n_dupes,
        "invalid_pincodes_flagged": int(bad_pin_mask.sum()),
        "amount_outliers_flagged": int(amount_outlier_mask.sum()),
        "rows_out": len(df),
    }
    df.attrs["cleaning_meta"] = meta
    return df


def run() -> pd.DataFrame:
    raw = load_raw_shipments()
    clean = clean_shipments(raw)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    clean.to_csv(CLEAN_SHIPMENTS_PATH, index=False)
    print("Cleaning summary:", clean.attrs["cleaning_meta"])
    print(f"Wrote {len(clean)} rows -> {CLEAN_SHIPMENTS_PATH}")
    return clean


if __name__ == "__main__":
    run()
