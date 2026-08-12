"""
COD Remittance Agent.

Rule: for every COD shipment successfully delivered, the carrier remittance
notification is due REMITTANCE_DAYS_AFTER_DELIVERY (2) days after delivery.

`package_amount` is used as the COD-amount proxy — the raw workbook has no
separate "COD amount" field, so package_amount (ACTUAL) doubles as the
collectible amount for COD orders. This assumption is documented in
docs/data_assumptions.md ("COD amount").

Writes outputs/cod_remittance_queue.csv — a mock carrier email/action queue.
No real emails are sent.
"""

from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    COD_QUEUE_PATH,
    FEATURED_SHIPMENTS_PATH,
    OUTPUTS_DIR,
    REMITTANCE_DAYS_AFTER_DELIVERY,
    SNAPSHOT_DATE,
)


def _email_subject(row) -> str:
    return f"[Remittance Due] AWB {row['awb']} — COD ₹{row['package_amount']:,} — {row['carrier']}"


def _email_body(row) -> str:
    return (
        f"Dear {row['carrier']} Finance Team,\n\n"
        f"The following COD shipment was delivered on {row['delivery_date'].date()}. "
        f"Per the {REMITTANCE_DAYS_AFTER_DELIVERY}-day remittance SLA, payment of "
        f"₹{row['package_amount']:,} is due by {row['remittance_due_date']}.\n\n"
        f"AWB: {row['awb']}\nOrder ID: {row['order_id']}\nDelivery Date: {row['delivery_date'].date()}\n"
        f"Destination: {row['customer_city']}\nCOD Amount: ₹{row['package_amount']:,}\n\n"
        f"Please confirm remittance status.\n\nRegards,\nFinance Ops — AI Logistics Control Tower"
    )


OUTPUT_COLUMNS = [
    "carrier", "awb", "order_id", "delivery_date", "cod_amount", "remittance_due_date",
    "status", "priority", "email_subject", "email_body", "cod_amount_source",
]


def build_remittance_queue(df: pd.DataFrame) -> pd.DataFrame:
    snapshot = pd.Timestamp(SNAPSHOT_DATE)
    cod_delivered = df[(df["is_delivered"]) & (df["is_cod"])].copy()
    if cod_delivered.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    cod_delivered["remittance_due_date"] = (
        cod_delivered["delivery_date"] + pd.Timedelta(days=REMITTANCE_DAYS_AFTER_DELIVERY)
    )

    def _status(due_date):
        if due_date <= snapshot:
            return "Overdue" if due_date < snapshot else "Due Today"
        return "Pending"

    cod_delivered["status"] = cod_delivered["remittance_due_date"].apply(_status)

    def _priority(row):
        if row["status"] == "Overdue":
            days_overdue = (snapshot - row["remittance_due_date"]).days
            if row["package_amount"] > 2000 or days_overdue > 5:
                return "P1 - Urgent"
            return "P2 - High"
        if row["status"] == "Due Today":
            return "P2 - High"
        return "P3 - Standard"

    cod_delivered["priority"] = cod_delivered.apply(_priority, axis=1)
    cod_delivered["email_subject"] = cod_delivered.apply(_email_subject, axis=1)
    cod_delivered["email_body"] = cod_delivered.apply(_email_body, axis=1)
    cod_delivered["remittance_due_date"] = cod_delivered["remittance_due_date"].dt.strftime("%Y-%m-%d")

    out = cod_delivered[[
        "carrier", "awb", "order_id", "delivery_date", "package_amount",
        "remittance_due_date", "status", "priority", "email_subject", "email_body",
    ]].rename(columns={"package_amount": "cod_amount"})
    out["cod_amount_source"] = "DERIVED (package_amount used as COD-amount proxy — see docs/data_assumptions.md)"
    return out.sort_values(["priority", "remittance_due_date"])


def run() -> pd.DataFrame:
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    queue = build_remittance_queue(df)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    queue.to_csv(COD_QUEUE_PATH, index=False)
    print(f"Wrote {len(queue)} COD remittance entries -> {COD_QUEUE_PATH}")
    print(queue["status"].value_counts())
    total_due = queue.loc[queue["status"].isin(["Overdue", "Due Today"]), "cod_amount"].sum()
    print(f"Total COD amount currently due/overdue: Rs {total_due:,.0f}")
    return queue


if __name__ == "__main__":
    run()
