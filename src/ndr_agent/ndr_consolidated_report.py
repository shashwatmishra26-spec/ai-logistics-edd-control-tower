"""
NDR Consolidated Report + Multi-Channel Outreach.

For every shipment that currently has an unresolved failed-delivery (NDR)
event — i.e. "delivery could not be done yet" — this module produces the
management + operations layer on top of src/ndr_agent/ndr_recovery.py's
per-shipment queue:

  1. A consolidated report (outputs/ndr_consolidated_report.csv): one table,
     broken down by reason AND by lane, so a manager can see where the NDR
     problem is concentrated without opening the raw queue.
  2. A mock digest email to the customer-care team (outputs/ndr_care_team_
     digest.txt) — one email, not one per shipment, summarizing the queue.
  3. A call sheet for an IVR / outbound-calling team (outputs/
     ivr_call_sheet.csv) — what to ask the customer for (landmark, address,
     alternate phone number) based on the NDR reason.
  4. Mock customer-facing outreach content (outputs/ndr_customer_outreach.
     csv) — push-notification and email copy asking the CUSTOMER directly for
     the missing information.

PRIVACY NOTE (read before wiring this into a real system): src/data/clean.py
masks/drops customer name, phone and address at the cleaning stage — this
pipeline never has real PII downstream of that step (see docs/data_
dictionary.md §2). The IVR sheet and outreach content below are therefore
built entirely from non-PII fields (shipment_uid, AWB, city, NDR reason) plus
a `contact_lookup_key`. In a real deployment, the actual phone number /
address a calling agent or push service needs would be joined in at send-time
from a secure CRM using that key — it is intentionally never persisted in
this analytics output, so this repo (which is public) never carries a real
customer's contact details. This is a design decision, not an oversight.
"""

from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    FEATURED_SHIPMENTS_PATH,
    IVR_CALL_SHEET_PATH,
    NDR_CARE_TEAM_DIGEST_PATH,
    NDR_CONSOLIDATED_REPORT_PATH,
    NDR_CUSTOMER_OUTREACH_PATH,
    NDR_QUEUE_PATH,
    OUTPUTS_DIR,
    SNAPSHOT_DATE,
)

INFO_NEEDED_MAP = {
    "Address issue": "Confirm the complete delivery address",
    "Landmark missing": "Ask for a nearby landmark",
    "Phone not reachable": "Request an alternate phone number",
    "Customer not available": "Confirm the best delivery time window",
    "Customer requested re-attempt": "Confirm the requested reattempt date/slot",
    "Delivery postponed by customer": "Confirm the new delivery date",
    "Customer refused delivery": "Confirm whether to cancel or re-offer the order",
    "Customer cancelled order": "Confirm cancellation (no further reattempt)",
    "COD payment declined": "Offer a prepaid/UPI payment alternative",
}
DEFAULT_INFO_NEEDED = "General reattempt confirmation"

CONTACT_NOTE = (
    "Contact via secure CRM lookup on shipment_uid — customer name/phone/address are "
    "masked at the cleaning stage and never stored in this analytics output (see "
    "docs/data_dictionary.md §2)."
)


def consolidate_ndr_report(ndr_queue: pd.DataFrame) -> pd.DataFrame:
    """One table, two dimensions: rows broken down by `reason` and by
    `lane_class`, both drawn from the same open-NDR queue, so a manager can
    scan a single CSV instead of cross-referencing several."""
    columns = [
        "dimension", "value", "open_count", "pct_of_open_queue",
        "avg_reattempt_success_probability", "p1_urgent_count", "p2_high_count",
    ]
    if ndr_queue.empty:
        return pd.DataFrame(columns=columns)

    total = len(ndr_queue)

    def _breakdown(group_col: str, dim_label: str) -> pd.DataFrame:
        g = ndr_queue.groupby(group_col).agg(
            open_count=("shipment_id", "count"),
            avg_reattempt_success_probability=("reattempt_success_probability", "mean"),
            p1_urgent_count=("priority", lambda s: int((s == "P1 - Urgent").sum())),
            p2_high_count=("priority", lambda s: int((s == "P2 - High").sum())),
        ).reset_index().rename(columns={group_col: "value"})
        g.insert(0, "dimension", dim_label)
        g["pct_of_open_queue"] = round(g["open_count"] / total * 100, 1)
        g["avg_reattempt_success_probability"] = g["avg_reattempt_success_probability"].round(3)
        return g[columns]

    report = pd.concat(
        [_breakdown("ndr_reason", "reason"), _breakdown("lane_class", "lane_class")],
        ignore_index=True,
    )
    return report.sort_values(["dimension", "open_count"], ascending=[True, False]).reset_index(drop=True)


def build_ivr_call_sheet(ndr_queue: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "shipment_id", "awb", "order_id", "carrier", "lane_class", "customer_city",
        "ndr_reason", "ndr_category", "attempt_number", "priority", "info_needed",
        "call_script", "contact_lookup_key", "contact_note",
    ]
    if ndr_queue.empty:
        return pd.DataFrame(columns=columns)

    sheet = ndr_queue.merge(
        features[["order_id", "customer_city"]], left_on="order_id", right_on="order_id", how="left"
    ).copy()
    sheet["info_needed"] = sheet["ndr_reason"].map(INFO_NEEDED_MAP).fillna(DEFAULT_INFO_NEEDED)
    sheet["call_script"] = (
        "Hello, this is [Carrier] calling about your order " + sheet["order_id"].astype(str) +
        ". We attempted delivery but couldn't complete it (" + sheet["ndr_reason"].str.lower() + "). "
        + sheet["info_needed"] + " so we can schedule the next attempt."
    )
    sheet["contact_lookup_key"] = sheet["shipment_id"]
    sheet["contact_note"] = CONTACT_NOTE

    priority_rank = {"P1 - Urgent": 0, "P2 - High": 1, "P3 - Standard": 2}
    sheet["_rank"] = sheet["priority"].map(priority_rank)
    sheet = sheet.sort_values(["_rank", "attempt_number"], ascending=[True, False]).drop(columns="_rank")
    return sheet[columns]


def _push_title(reason: str) -> str:
    return "We couldn't deliver your order" if reason != "Customer cancelled order" else "About your cancelled order"


def _push_body(row) -> str:
    info = INFO_NEEDED_MAP.get(row["ndr_reason"], DEFAULT_INFO_NEEDED)
    return (
        f"Order #{row['order_id']}: our delivery partner couldn't complete your delivery "
        f"({row['ndr_reason'].lower()}). {info} — tap here to help us get it to you faster."
    )


def _email_subject(row) -> str:
    return f"Action needed: help us deliver order #{row['order_id']}"


def _email_body(row) -> str:
    info = INFO_NEEDED_MAP.get(row["ndr_reason"], DEFAULT_INFO_NEEDED)
    return (
        f"Hi,\n\nWe attempted to deliver your order #{row['order_id']} but were unable to "
        f"({row['ndr_reason']}). {info}, and we'll schedule the next attempt right away.\n\n"
        f"Reply to this email or use the app to update your details.\n\n"
        f"Thanks,\nCustomer Care — AI Logistics Control Tower"
    )


def build_customer_outreach(ndr_queue: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "shipment_id", "order_id", "ndr_reason", "info_requested", "push_notification_title",
        "push_notification_body", "email_subject", "email_body", "data_confidence",
    ]
    if ndr_queue.empty:
        return pd.DataFrame(columns=columns)

    out = ndr_queue.copy()
    out["info_requested"] = out["ndr_reason"].map(INFO_NEEDED_MAP).fillna(DEFAULT_INFO_NEEDED)
    out["push_notification_title"] = out["ndr_reason"].apply(_push_title)
    out["push_notification_body"] = out.apply(_push_body, axis=1)
    out["email_subject"] = out.apply(_email_subject, axis=1)
    out["email_body"] = out.apply(_email_body, axis=1)
    out["data_confidence"] = "MOCK (message content — no real push notification or email is actually sent)"
    return out[columns]


def build_care_team_digest(ndr_queue: pd.DataFrame, report: pd.DataFrame) -> str:
    snapshot = SNAPSHOT_DATE
    total = len(ndr_queue)
    if total == 0:
        body = "No open NDR shipments in the current snapshot — customer-care NDR queue is clear."
        return f"Subject: [NDR Digest {snapshot}] Queue clear — 0 open shipments\n\n{body}\n"

    p1 = int((ndr_queue["priority"] == "P1 - Urgent").sum())
    p2 = int((ndr_queue["priority"] == "P2 - High").sum())
    by_reason = report[report["dimension"] == "reason"].sort_values("open_count", ascending=False).head(3)
    by_lane = report[report["dimension"] == "lane_class"].sort_values("open_count", ascending=False).head(3)

    reason_lines = "\n".join(
        f"  - {r['value']}: {r['open_count']} shipments ({r['pct_of_open_queue']}%), "
        f"avg reattempt-success {r['avg_reattempt_success_probability']:.0%}"
        for _, r in by_reason.iterrows()
    )
    lane_lines = "\n".join(
        f"  - {r['value']}: {r['open_count']} shipments ({r['pct_of_open_queue']}%)"
        for _, r in by_lane.iterrows()
    )

    subject = f"[NDR Digest {snapshot}] {total} open shipments — {p1} urgent, {p2} high priority"
    body = (
        f"Dear Customer Care Team,\n\n"
        f"{total} shipments currently have an unresolved failed-delivery (NDR) event "
        f"({p1} P1-Urgent, {p2} P2-High). Full per-shipment queue: "
        f"outputs/customer_care_notifications.csv. Outbound call list: outputs/ivr_call_sheet.csv.\n\n"
        f"Top reasons:\n{reason_lines}\n\n"
        f"Top lanes:\n{lane_lines}\n\n"
        f"Customer-facing push/email outreach has been queued separately — see "
        f"outputs/ndr_customer_outreach.csv.\n\n"
        f"Regards,\nAI Logistics Control Tower"
    )
    return f"Subject: {subject}\n\n{body}\n"


def run():
    features = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    ndr_queue = pd.read_csv(NDR_QUEUE_PATH)

    report = consolidate_ndr_report(ndr_queue)
    ivr_sheet = build_ivr_call_sheet(ndr_queue, features)
    outreach = build_customer_outreach(ndr_queue)
    digest = build_care_team_digest(ndr_queue, report)

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    report.to_csv(NDR_CONSOLIDATED_REPORT_PATH, index=False)
    ivr_sheet.to_csv(IVR_CALL_SHEET_PATH, index=False)
    outreach.to_csv(NDR_CUSTOMER_OUTREACH_PATH, index=False)
    NDR_CARE_TEAM_DIGEST_PATH.write_text(digest)

    print(f"Wrote {len(report)} consolidated-report rows -> {NDR_CONSOLIDATED_REPORT_PATH}")
    print(f"Wrote {len(ivr_sheet)} IVR call-sheet rows -> {IVR_CALL_SHEET_PATH}")
    print(f"Wrote {len(outreach)} customer outreach rows -> {NDR_CUSTOMER_OUTREACH_PATH}")
    print(f"Wrote care-team digest email -> {NDR_CARE_TEAM_DIGEST_PATH}")
    return report, ivr_sheet, outreach


if __name__ == "__main__":
    run()
