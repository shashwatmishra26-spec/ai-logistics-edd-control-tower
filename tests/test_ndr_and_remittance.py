"""Tests for the NDR Recovery Agent and COD Remittance Agent, including
notification generation and the remittance-trigger date rule."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.ndr_agent.ndr_recovery import build_ndr_queue
from src.remittance_agent.cod_remittance import build_remittance_queue
from config.config import REMITTANCE_DAYS_AFTER_DELIVERY, SNAPSHOT_DATE


def _ndr_row(order_id, reason, attempt, is_open=True, delivered=False, awb=None, carrier="Carrier A", lane="Metro"):
    return {
        "order_id": order_id, "shipment_uid": f"UID{order_id}", "awb": awb or f"AWB{order_id}",
        "carrier": carrier, "lane_class": lane, "ndr_reason": reason,
        "ndr_category": "Contact Issue", "attempt_number": attempt,
        "has_ndr": reason != "Not Applicable", "is_open": is_open, "is_delivered": delivered,
    }


class TestNdrRecoveryAgent(unittest.TestCase):
    def test_open_ndr_shipment_produces_notification(self):
        # historical closed shipments establish the empirical base rate
        hist = [_ndr_row(1000 + i, "Phone not reachable", 2, is_open=False, delivered=(i % 2 == 0))
                for i in range(20)]
        open_ndr = [_ndr_row(1, "Phone not reachable", 2, is_open=True, delivered=False)]
        df = pd.DataFrame(hist + open_ndr)
        queue = build_ndr_queue(df)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.iloc[0]["ndr_reason"], "Phone not reachable")
        self.assertIn(queue.iloc[0]["priority"], {"P1 - Urgent", "P2 - High", "P3 - Standard"})

    def test_low_recovery_reason_forces_urgent_priority(self):
        hist = [_ndr_row(2000 + i, "Customer refused delivery", 3, is_open=False, delivered=False) for i in range(15)]
        open_ndr = [_ndr_row(2, "Customer refused delivery", 3, is_open=True, delivered=False)]
        df = pd.DataFrame(hist + open_ndr)
        queue = build_ndr_queue(df)
        self.assertEqual(queue.iloc[0]["priority"], "P1 - Urgent")
        self.assertEqual(queue.iloc[0]["customer_action_required"], "Yes")

    def test_no_open_ndr_produces_empty_queue(self):
        hist = [_ndr_row(3000 + i, "Not Applicable", 1, is_open=False, delivered=True) for i in range(10)]
        df = pd.DataFrame(hist)
        queue = build_ndr_queue(df)
        self.assertEqual(len(queue), 0)

    def test_deadline_is_in_the_future_relative_to_snapshot(self):
        hist = [_ndr_row(4000 + i, "Landmark missing", 1, is_open=False, delivered=(i % 3 != 0)) for i in range(15)]
        open_ndr = [_ndr_row(4, "Landmark missing", 1, is_open=True, delivered=False)]
        df = pd.DataFrame(hist + open_ndr)
        queue = build_ndr_queue(df)
        deadline = pd.Timestamp(queue.iloc[0]["deadline"])
        self.assertGreater(deadline, pd.Timestamp(SNAPSHOT_DATE))


def _cod_row(order_id, delivery_date, amount, cod=True, carrier="Carrier A", city="Mumbai"):
    return {
        "order_id": order_id, "awb": f"AWB{order_id}", "is_delivered": True, "is_cod": cod,
        "delivery_date": pd.Timestamp(delivery_date), "package_amount": amount,
        "carrier": carrier, "customer_city": city,
    }


class TestRemittanceAgent(unittest.TestCase):
    def test_remittance_due_date_is_delivery_plus_two_days(self):
        df = pd.DataFrame([_cod_row(1, "2026-03-01", 500)])
        queue = build_remittance_queue(df)
        self.assertEqual(queue.iloc[0]["remittance_due_date"], "2026-03-03")

    def test_remittance_days_config_matches_rule(self):
        self.assertEqual(REMITTANCE_DAYS_AFTER_DELIVERY, 2)

    def test_overdue_status_when_due_date_before_snapshot(self):
        df = pd.DataFrame([_cod_row(1, "2026-01-01", 500)])  # due 2026-01-03, well before snapshot
        queue = build_remittance_queue(df)
        self.assertEqual(queue.iloc[0]["status"], "Overdue")

    def test_prepaid_shipments_excluded(self):
        df = pd.DataFrame([_cod_row(1, "2026-03-01", 500, cod=False)])
        queue = build_remittance_queue(df)
        self.assertEqual(len(queue), 0)

    def test_high_value_overdue_is_urgent(self):
        df = pd.DataFrame([_cod_row(1, "2026-01-01", 5000)])
        queue = build_remittance_queue(df)
        self.assertEqual(queue.iloc[0]["priority"], "P1 - Urgent")

    def test_email_fields_populated(self):
        df = pd.DataFrame([_cod_row(1, "2026-03-01", 500)])
        queue = build_remittance_queue(df)
        self.assertIn("AWB1", queue.iloc[0]["email_subject"])
        self.assertIn("Carrier A", queue.iloc[0]["email_body"])


if __name__ == "__main__":
    unittest.main()
