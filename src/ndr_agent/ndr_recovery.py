"""
NDR/NRD Customer-Care Agent.

For every shipment carrying an NDR/NRD reason, determines:
  - reattempt success probability (empirical, from historical reason x
    attempt-number outcomes in this dataset)
  - recommended action
  - urgency / priority
  - whether Customer Care intervention is required
  - a deadline (SLA-driven)

Writes outputs/customer_care_notifications.csv — a mock actionable queue.
No real messages are sent.
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
    HIGH_RISK_NDR_REASONS,
    MAX_ATTEMPTS_BEFORE_RTO_RISK,
    NDR_EMAIL_REASONS,
    NDR_MANUAL_CALL_AGE_HOURS,
    NDR_MANUAL_CALL_HIGH_VALUE_INR,
    NDR_MANUAL_CALL_MIN_ATTEMPT,
    NDR_QUEUE_PATH,
    NDR_WHATSAPP_ACTION_REASONS,
    OUTPUTS_DIR,
    SNAPSHOT_DATE,
)

ACTION_MAP = {
    "Address issue": "Request updated address / landmark from customer",
    "Landmark missing": "Send landmark-capture link; call customer for directions",
    "Phone not reachable": "Send SMS/WhatsApp with reattempt slot; try alternate number",
    "Customer not available": "Offer delivery-slot rescheduling (customer picks a window)",
    "Customer requested re-attempt": "Confirm requested reattempt date/slot with carrier",
    "Delivery postponed by customer": "Confirm new delivery date; hold shipment at hub",
    "Customer refused delivery": "Escalate to Customer Care — confirm cancellation vs re-offer",
    "Customer cancelled order": "Initiate RTO — no further reattempt",
    "COD payment declined": "Offer prepaid/UPI alternative; escalate if repeated",
}


def assign_ndr_channel(r: pd.Series, snapshot: pd.Timestamp) -> dict:
    """NDR outreach channel routing — priority-ordered rule cascade.

    Business logic (see config.config for the full rationale + thresholds):
      1. MANUAL AGENT CALL (₹15-25/call, deliberately the expensive channel)
         — gated by severity, not blanket-applied: 2nd/3rd+ attempt, a
         high-value COD-dispute order, or a case aged past 24-48h.
      2. EMAIL — backup/documentation channel, used specifically when the
         phone itself is unreachable (paper trail for later disputes).
      3. IVR (automated call) — the default first-touch channel: near-zero
         cost, used for the bulk of Day-1 volume on simple reasons.
    WhatsApp runs IN PARALLEL with IVR (also_whatsapp=True, never a
    replacement) whenever the customer needs to actively DO something —
    confirm a slot, share a location pin, verify an address, confirm COD
    readiness — regardless of which channel is primary, since a written,
    actionable trail helps even alongside a call.

    Each shipment gets exactly ONE primary channel — this is what prevents
    bombarding the same customer with a call AND an SMS AND an email for the
    same unresolved case; WhatsApp is the only sanctioned parallel add-on.
    """
    reason = r["ndr_reason"]
    attempt = int(r["attempt_number"])
    is_cod = bool(r.get("is_cod", False))
    amount = float(r.get("package_amount", 0) or 0)
    fad = r.get("first_attempt_date")
    age_hours = None
    if pd.notna(fad):
        age_hours = (snapshot - pd.Timestamp(fad)).total_seconds() / 3600.0

    is_cod_dispute = is_cod and reason == "COD payment declined" and amount >= NDR_MANUAL_CALL_HIGH_VALUE_INR
    is_aged = age_hours is not None and age_hours >= NDR_MANUAL_CALL_AGE_HOURS
    is_repeat_failure = attempt >= NDR_MANUAL_CALL_MIN_ATTEMPT
    is_high_risk_reason = reason in HIGH_RISK_NDR_REASONS

    also_whatsapp = reason in NDR_WHATSAPP_ACTION_REASONS

    if is_repeat_failure or is_cod_dispute or is_aged or is_high_risk_reason:
        channel = "Manual Agent Call"
        bits = []
        if is_repeat_failure:
            bits.append(f"attempt #{attempt} (repeat failure)")
        if is_cod_dispute:
            bits.append(f"COD amount dispute >= INR {NDR_MANUAL_CALL_HIGH_VALUE_INR}")
        if is_aged:
            bits.append(f"aged {age_hours:.0f}h (>= {NDR_MANUAL_CALL_AGE_HOURS}h)")
        if is_high_risk_reason and not bits:
            bits.append(f"high-risk reason ({reason})")
        rationale = "Escalated to manual agent call — " + "; ".join(bits) + "."
    elif reason in NDR_EMAIL_REASONS:
        channel = "Email"
        rationale = f"Phone unreachable ({reason}) — email is the documentation/backup channel."
    else:
        channel = "IVR"
        rationale = "First-touch, low-complexity reason — routed to automated IVR (near-zero cost)."

    if also_whatsapp and channel != "Manual Agent Call":
        rationale += " Parallel WhatsApp sent — customer action required (confirm slot/address/location/COD readiness)."

    return {
        "recommended_channel": channel,
        "also_whatsapp": also_whatsapp,
        "channel_rationale": rationale,
    }


def _empirical_reattempt_success(df: pd.DataFrame) -> pd.DataFrame:
    """Empirical P(eventually delivered | reason, attempt_number) computed
    from CLOSED shipments that had an NDR event. This is the ACTUAL,
    data-driven basis for the AI_PREDICTED probability applied to currently
    OPEN NDR shipments."""
    closed_ndr = df[(~df["is_open"]) & (df["has_ndr"])]
    rates = (
        closed_ndr.groupby(["ndr_reason", "attempt_number"])["is_delivered"]
        .agg(["mean", "count"])
        .rename(columns={"mean": "reattempt_success_rate", "count": "n"})
        .reset_index()
    )
    return rates


def build_ndr_queue(df: pd.DataFrame) -> pd.DataFrame:
    rates = _empirical_reattempt_success(df)
    global_rate = df[(~df["is_open"]) & (df["has_ndr"])]["is_delivered"].mean()

    open_ndr = df[(df["is_open"]) & (df["has_ndr"])].copy()
    snapshot = pd.Timestamp(SNAPSHOT_DATE)

    rows = []
    for _, r in open_ndr.iterrows():
        match = rates[(rates["ndr_reason"] == r["ndr_reason"]) & (rates["attempt_number"] == r["attempt_number"])]
        if len(match) and match["n"].iloc[0] >= 10:
            p_success = round(float(match["reattempt_success_rate"].iloc[0]), 3)
            confidence = f"AI_PREDICTED (empirical, n={int(match['n'].iloc[0])})"
        else:
            reason_match = rates[rates["ndr_reason"] == r["ndr_reason"]]
            if len(reason_match):
                p_success = round(float((reason_match["reattempt_success_rate"] * reason_match["n"]).sum() / reason_match["n"].sum()), 3)
                confidence = f"AI_PREDICTED (reason-level fallback, n={int(reason_match['n'].sum())})"
            else:
                p_success = round(float(global_rate), 3)
                confidence = "AI_PREDICTED (global fallback)"

        high_risk = r["ndr_reason"] in HIGH_RISK_NDR_REASONS
        near_max_attempts = r["attempt_number"] >= MAX_ATTEMPTS_BEFORE_RTO_RISK

        if high_risk or near_max_attempts or p_success < 0.4:
            priority = "P1 - Urgent"
            care_required = True
            deadline_hours = 6
        elif p_success < 0.65:
            priority = "P2 - High"
            care_required = True
            deadline_hours = 24
        else:
            priority = "P3 - Standard"
            care_required = False
            deadline_hours = 48

        if r["ndr_reason"] == "Customer cancelled order":
            action = "RTO Prevention: confirm cancellation, initiate RTO if confirmed"
        elif near_max_attempts:
            action = "RTO Prevention: final reattempt + customer confirmation before auto-RTO"
        else:
            action = ACTION_MAP.get(r["ndr_reason"], "Customer contact required")

        deadline = snapshot + pd.Timedelta(hours=deadline_hours)
        channel_info = assign_ndr_channel(r, snapshot)

        rows.append({
            "shipment_id": r["shipment_uid"],
            "awb": r["awb"],
            "order_id": r["order_id"],
            "carrier": r["carrier"],
            "lane_class": r["lane_class"],
            "ndr_reason": r["ndr_reason"],
            "ndr_category": r["ndr_category"],
            "attempt_number": int(r["attempt_number"]),
            "customer_action_required": "Yes" if care_required else "No",
            "recommended_action": action,
            "reattempt_success_probability": p_success,
            "priority": priority,
            "deadline": deadline.strftime("%Y-%m-%d %H:%M"),
            "ai_confidence": confidence,
            "status": "Open",
            "recommended_channel": channel_info["recommended_channel"],
            "also_whatsapp": channel_info["also_whatsapp"],
            "channel_rationale": channel_info["channel_rationale"],
        })

    columns = [
        "shipment_id", "awb", "order_id", "carrier", "lane_class", "ndr_reason", "ndr_category",
        "attempt_number", "customer_action_required", "recommended_action",
        "reattempt_success_probability", "priority", "deadline", "ai_confidence", "status",
        "recommended_channel", "also_whatsapp", "channel_rationale",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["priority", "reattempt_success_probability"])


def run() -> pd.DataFrame:
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    queue = build_ndr_queue(df)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    queue.to_csv(NDR_QUEUE_PATH, index=False)
    print(f"Wrote {len(queue)} NDR notifications -> {NDR_QUEUE_PATH}")
    print(queue["priority"].value_counts())
    return queue


if __name__ == "__main__":
    run()
