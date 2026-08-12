"""Tests for the three "primary objective" modules added on top of the base
pipeline:

  - src.alerts_agent.edd_breach_alerts   (in-transit EDD breach detection)
  - src.lane_engine.lane_intelligence.compute_padding_recommendations
  - src.ndr_agent.ndr_consolidated_report (NDR consolidation, IVR, outreach)
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.alerts_agent.edd_breach_alerts import (
    build_breach_alert_queue,
    lane_breach_summary,
)
from src.lane_engine.lane_intelligence import compute_padding_recommendations
from src.ndr_agent.ndr_consolidated_report import (
    CONTACT_NOTE,
    build_care_team_digest,
    build_customer_outreach,
    build_ivr_call_sheet,
    consolidate_ndr_report,
)
from config.config import (
    MAX_RECOMMENDED_PADDING_DAYS,
    MIN_VOLUME_FOR_LANE_INTERVENTION,
    SNAPSHOT_DATE,
    TRANSIT_SLA_DAYS,
)


# ---------------------------------------------------------------------------
# EDD Breach Alert Agent
# ---------------------------------------------------------------------------
def _pred_row(order_id, risk_tier, edd, current_status="In-Transit", edd_risk_score=75,
              awb=None, carrier="Carrier A", lane_class="Metro", attempt_number=0,
              recommended_action="Monitor closely", risk_reason="Elevated risk score", shipment_uid=None):
    return {
        "order_id": order_id, "awb": awb or f"AWB{order_id}", "carrier": carrier,
        "lane_class": lane_class, "current_status": current_status, "edd": pd.Timestamp(edd),
        "edd_risk_score": edd_risk_score, "risk_tier": risk_tier, "attempt_number": attempt_number,
        "recommended_action": recommended_action, "risk_reason": risk_reason,
        "shipment_uid": shipment_uid or f"UID{order_id}",
    }


def _feature_row(order_id, city="Mumbai", is_open=True, lane_class="Metro"):
    return {"order_id": order_id, "customer_city": city, "is_open": is_open, "lane_class": lane_class}


class TestBreachAlertQueue(unittest.TestCase):
    def test_empty_predictions_returns_empty_queue(self):
        preds = pd.DataFrame(columns=["order_id", "risk_tier"])
        features = pd.DataFrame([_feature_row(1)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(len(queue), 0)

    def test_edd_already_passed_is_p1_urgent(self):
        preds = pd.DataFrame([_pred_row(1, "High", "2026-03-01")])  # 4 days before snapshot
        features = pd.DataFrame([_feature_row(1)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(len(queue), 1)
        self.assertTrue(bool(queue.iloc[0]["edd_already_breached"]))
        self.assertEqual(queue.iloc[0]["alert_priority"], "P1 - Urgent")

    def test_high_risk_edd_within_high_window_is_p2(self):
        # snapshot + 2 days -> beyond BREACH_ALERT_URGENT_DAYS(1) but within BREACH_ALERT_HIGH_DAYS(3)
        preds = pd.DataFrame([_pred_row(2, "High", "2026-03-07")])
        features = pd.DataFrame([_feature_row(2)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(queue.iloc[0]["alert_priority"], "P2 - High")
        self.assertFalse(bool(queue.iloc[0]["edd_already_breached"]))

    def test_medium_risk_far_from_edd_is_p3(self):
        preds = pd.DataFrame([_pred_row(3, "Medium", "2026-03-10")])  # 5 days out
        features = pd.DataFrame([_feature_row(3)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(queue.iloc[0]["alert_priority"], "P3 - Standard")

    def test_low_risk_shipments_excluded(self):
        preds = pd.DataFrame([_pred_row(4, "Low", "2026-03-01")])
        features = pd.DataFrame([_feature_row(4)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(len(queue), 0)

    def test_closed_shipments_excluded(self):
        preds = pd.DataFrame([_pred_row(5, "High", "2026-03-01")])
        features = pd.DataFrame([_feature_row(5, is_open=False)])
        queue = build_breach_alert_queue(preds, features)
        self.assertEqual(len(queue), 0)

    def test_message_content_labelled_mock(self):
        preds = pd.DataFrame([_pred_row(6, "High", "2026-03-01")])
        features = pd.DataFrame([_feature_row(6)])
        queue = build_breach_alert_queue(preds, features)
        self.assertIn("MOCK", queue.iloc[0]["data_confidence"])
        self.assertTrue(len(queue.iloc[0]["care_team_update"]) > 0)
        self.assertTrue(len(queue.iloc[0]["push_notification_body"]) > 0)


class TestLaneBreachSummary(unittest.TestCase):
    def test_lane_below_min_volume_is_insufficient_sample(self):
        features = pd.DataFrame([_feature_row(i, "TinyCity") for i in range(3)])
        alert_queue = pd.DataFrame(columns=["shipment_id", "customer_city", "lane_class",
                                             "edd_already_breached", "alert_priority"])
        summary = lane_breach_summary(alert_queue, features)
        row = summary[summary["customer_city"] == "TinyCity"].iloc[0]
        self.assertEqual(row["lane_status"], "Insufficient Sample")

    def test_lane_with_all_shipments_at_risk_is_breach_risk(self):
        features = pd.DataFrame([_feature_row(i, "RiskyCity") for i in range(10)])
        alert_rows = [{
            "shipment_id": f"S{i}", "customer_city": "RiskyCity", "lane_class": "Metro",
            "edd_already_breached": True, "alert_priority": "P1 - Urgent",
        } for i in range(10)]
        alert_queue = pd.DataFrame(alert_rows)
        summary = lane_breach_summary(alert_queue, features)
        row = summary[summary["customer_city"] == "RiskyCity"].iloc[0]
        self.assertEqual(row["at_risk_pct"], 100.0)
        self.assertEqual(row["lane_status"], "Breach Risk")

    def test_lane_with_no_at_risk_shipments_is_healthy(self):
        features = pd.DataFrame([_feature_row(i, "SafeCity") for i in range(10)])
        alert_queue = pd.DataFrame(columns=["shipment_id", "customer_city", "lane_class",
                                             "edd_already_breached", "alert_priority"])
        summary = lane_breach_summary(alert_queue, features)
        row = summary[summary["customer_city"] == "SafeCity"].iloc[0]
        self.assertEqual(row["at_risk_pct"], 0.0)
        self.assertEqual(row["lane_status"], "Healthy")


# ---------------------------------------------------------------------------
# Lane EDD Padding Recommender
# ---------------------------------------------------------------------------
def _lane_row(city, lane_class, status, edd_pct, volume):
    return {"customer_city": city, "lane_class": lane_class, "lane_status": status,
            "edd_adherence_pct": edd_pct, "shipment_volume": volume}


def _transit_row(city, lane_class, transit_days, is_open=False, is_delivered=True):
    return {"customer_city": city, "lane_class": lane_class, "transit_actual_days": transit_days,
            "is_open": is_open, "is_delivered": is_delivered}


class TestPaddingRecommendations(unittest.TestCase):
    def test_transit_driven_gap_recommends_padding(self):
        vol = MIN_VOLUME_FOR_LANE_INTERVENTION + 5
        lane_df = pd.DataFrame([_lane_row("PadCity", "Metro", "Intervention Required", 70.0, vol)])
        df = pd.DataFrame([_transit_row("PadCity", "Metro", 4) for _ in range(vol)])
        out = compute_padding_recommendations(df, lane_df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["current_transit_sla_days"], TRANSIT_SLA_DAYS["Metro"])
        self.assertGreater(row["recommended_padding_days"], 0)
        self.assertEqual(row["new_transit_sla_days"], row["current_transit_sla_days"] + row["recommended_padding_days"])
        self.assertGreater(row["projected_lift_pp"], 0)
        self.assertFalse(bool(row["manual_review_needed"]))

    def test_gap_not_transit_driven_recommends_zero_padding(self):
        vol = MIN_VOLUME_FOR_LANE_INTERVENTION + 5
        lane_df = pd.DataFrame([_lane_row("OkCity", "Metro", "Intervention Required", 70.0, vol)])
        # transit already well within SLA -> the EDD gap must be NDR/RTO driven, not padded
        df = pd.DataFrame([_transit_row("OkCity", "Metro", 1) for _ in range(vol)])
        out = compute_padding_recommendations(df, lane_df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["recommended_padding_days"], 0)
        self.assertIn("NDR/RTO", row["rationale"])

    def test_lane_already_hitting_target_is_excluded(self):
        vol = MIN_VOLUME_FOR_LANE_INTERVENTION + 5
        lane_df = pd.DataFrame([_lane_row("GoodCity", "Metro", "Best Performing", 95.0, vol)])
        df = pd.DataFrame([_transit_row("GoodCity", "Metro", 2) for _ in range(vol)])
        out = compute_padding_recommendations(df, lane_df)
        self.assertEqual(len(out), 0)

    def test_insufficient_sample_lane_is_excluded(self):
        lane_df = pd.DataFrame([_lane_row("TinyCity", "Metro", "Insufficient Sample", 60.0, 5)])
        df = pd.DataFrame([_transit_row("TinyCity", "Metro", 4) for _ in range(5)])
        out = compute_padding_recommendations(df, lane_df)
        self.assertEqual(len(out), 0)

    def test_low_actual_delivered_volume_is_excluded_despite_scorecard_volume(self):
        # lane_df claims enough volume, but the underlying delivered rows in df don't clear
        # MIN_VOLUME_FOR_LANE_INTERVENTION -- the function must check actual rows, not the label.
        lane_df = pd.DataFrame([_lane_row("SparseCity", "Metro", "Intervention Required", 70.0,
                                           MIN_VOLUME_FOR_LANE_INTERVENTION + 5)])
        df = pd.DataFrame([_transit_row("SparseCity", "Metro", 4) for _ in range(MIN_VOLUME_FOR_LANE_INTERVENTION - 5)])
        out = compute_padding_recommendations(df, lane_df)
        self.assertEqual(len(out), 0)

    def test_extreme_gap_is_capped_and_flagged_for_manual_review(self):
        vol = MIN_VOLUME_FOR_LANE_INTERVENTION + 5
        lane_df = pd.DataFrame([_lane_row("ExtremeCity", "Metro", "Intervention Required", 50.0, vol)])
        df = pd.DataFrame([_transit_row("ExtremeCity", "Metro", 20) for _ in range(vol)])
        out = compute_padding_recommendations(df, lane_df)
        row = out.iloc[0]
        self.assertEqual(row["recommended_padding_days"], MAX_RECOMMENDED_PADDING_DAYS)
        self.assertTrue(bool(row["manual_review_needed"]))


# ---------------------------------------------------------------------------
# NDR Consolidated Report + IVR + Outreach
# ---------------------------------------------------------------------------
def _ndr_q_row(shipment_id, order_id, reason, lane_class, priority, reattempt_prob=0.5,
               awb=None, carrier="Carrier A", ndr_category="Contact Issue", attempt=1):
    return {
        "shipment_id": shipment_id, "awb": awb or f"AWB{shipment_id}", "order_id": order_id,
        "carrier": carrier, "lane_class": lane_class, "ndr_reason": reason,
        "ndr_category": ndr_category, "attempt_number": attempt, "priority": priority,
        "reattempt_success_probability": reattempt_prob,
    }


class TestNdrConsolidatedReport(unittest.TestCase):
    def test_empty_queue_returns_empty_report(self):
        ndr_queue = pd.DataFrame(columns=["shipment_id", "ndr_reason", "lane_class",
                                           "reattempt_success_probability", "priority"])
        report = consolidate_ndr_report(ndr_queue)
        self.assertEqual(len(report), 0)

    def test_breakdown_by_reason_and_lane_counts(self):
        rows = [_ndr_q_row(f"S{i}", i, "Landmark missing", "Metro", "P2 - High") for i in range(3)]
        rows += [_ndr_q_row(f"S{i}", i, "Phone not reachable", "National", "P2 - High") for i in range(3, 5)]
        ndr_queue = pd.DataFrame(rows)
        report = consolidate_ndr_report(ndr_queue)
        reason_row = report[(report["dimension"] == "reason") & (report["value"] == "Landmark missing")].iloc[0]
        self.assertEqual(reason_row["open_count"], 3)
        self.assertEqual(reason_row["pct_of_open_queue"], 60.0)
        lane_row = report[(report["dimension"] == "lane_class") & (report["value"] == "National")].iloc[0]
        self.assertEqual(lane_row["open_count"], 2)


class TestIvrCallSheet(unittest.TestCase):
    def test_empty_queue_returns_empty_sheet(self):
        ndr_queue = pd.DataFrame(columns=["shipment_id", "awb", "order_id", "carrier", "lane_class",
                                           "ndr_reason", "ndr_category", "attempt_number", "priority"])
        features = pd.DataFrame(columns=["order_id", "customer_city"])
        sheet = build_ivr_call_sheet(ndr_queue, features)
        self.assertEqual(len(sheet), 0)

    def test_info_needed_mapped_by_reason(self):
        ndr_queue = pd.DataFrame([_ndr_q_row("S1", 1, "Landmark missing", "Metro", "P2 - High")])
        features = pd.DataFrame([{"order_id": 1, "customer_city": "Mumbai"}])
        sheet = build_ivr_call_sheet(ndr_queue, features)
        self.assertEqual(sheet.iloc[0]["info_needed"], "Ask for a nearby landmark")

    def test_unmapped_reason_gets_default_info_needed(self):
        ndr_queue = pd.DataFrame([_ndr_q_row("S1", 1, "Some unmapped reason", "Metro", "P3 - Standard")])
        features = pd.DataFrame([{"order_id": 1, "customer_city": "Mumbai"}])
        sheet = build_ivr_call_sheet(ndr_queue, features)
        self.assertEqual(sheet.iloc[0]["info_needed"], "General reattempt confirmation")

    def test_sorted_urgent_first(self):
        ndr_queue = pd.DataFrame([
            _ndr_q_row("S1", 1, "Address issue", "Metro", "P3 - Standard"),
            _ndr_q_row("S2", 2, "Address issue", "Metro", "P1 - Urgent"),
        ])
        features = pd.DataFrame([{"order_id": 1, "customer_city": "Mumbai"}, {"order_id": 2, "customer_city": "Pune"}])
        sheet = build_ivr_call_sheet(ndr_queue, features)
        self.assertEqual(sheet.iloc[0]["priority"], "P1 - Urgent")

    def test_no_pii_columns_and_contact_note_present(self):
        ndr_queue = pd.DataFrame([_ndr_q_row("S1", 1, "Address issue", "Metro", "P2 - High")])
        features = pd.DataFrame([{"order_id": 1, "customer_city": "Mumbai"}])
        sheet = build_ivr_call_sheet(ndr_queue, features)
        forbidden = {"customer_name", "phone", "address", "customer_phone", "customer_address"}
        self.assertFalse(forbidden & set(sheet.columns))
        self.assertEqual(sheet.iloc[0]["contact_note"], CONTACT_NOTE)
        self.assertEqual(sheet.iloc[0]["contact_lookup_key"], "S1")


class TestCustomerOutreach(unittest.TestCase):
    def test_empty_queue_returns_empty_outreach(self):
        ndr_queue = pd.DataFrame(columns=["shipment_id", "order_id", "ndr_reason"])
        outreach = build_customer_outreach(ndr_queue)
        self.assertEqual(len(outreach), 0)

    def test_outreach_fields_populated_and_labelled_mock(self):
        ndr_queue = pd.DataFrame([_ndr_q_row("S1", 1, "Phone not reachable", "Metro", "P2 - High")])
        outreach = build_customer_outreach(ndr_queue)
        row = outreach.iloc[0]
        self.assertIn("MOCK", row["data_confidence"])
        self.assertTrue(len(row["push_notification_body"]) > 0)
        self.assertIn("1", row["email_subject"])


class TestCareTeamDigest(unittest.TestCase):
    def test_empty_queue_gives_clear_message(self):
        ndr_queue = pd.DataFrame(columns=["priority"])
        report = pd.DataFrame(columns=["dimension", "value", "open_count", "pct_of_open_queue",
                                        "avg_reattempt_success_probability"])
        digest = build_care_team_digest(ndr_queue, report)
        self.assertIn("Queue clear", digest)

    def test_digest_reports_priority_counts(self):
        rows = [_ndr_q_row(f"S{i}", i, "Landmark missing", "Metro", "P1 - Urgent") for i in range(2)]
        rows += [_ndr_q_row(f"S{i}", i, "Phone not reachable", "National", "P2 - High") for i in range(2, 5)]
        ndr_queue = pd.DataFrame(rows)
        report = consolidate_ndr_report(ndr_queue)
        digest = build_care_team_digest(ndr_queue, report)
        self.assertIn("5 open shipments", digest)
        self.assertIn("2 urgent", digest)
        self.assertIn("3 high priority", digest)


if __name__ == "__main__":
    unittest.main()
