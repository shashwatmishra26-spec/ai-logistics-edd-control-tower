"""Tests for src.alerts_agent.daily_edd_tracker — the Daily EDD Breach
Tracker (carrier-wise breach, top breach lanes, yesterday/today counts)."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.alerts_agent.daily_edd_tracker import (
    _is_breached,
    carrier_edd_breach_summary,
    lane_edd_breach_top,
)
from config.config import EDD_TRACKING_AS_OF_DATE


AS_OF = pd.Timestamp(EDD_TRACKING_AS_OF_DATE)
YESTERDAY = AS_OF - pd.Timedelta(days=1)
TOMORROW = AS_OF + pd.Timedelta(days=1)


def _row(edd, is_delivered=False, delivery_date=None, is_rto=False, is_lost=False,
         is_open=False, carrier="Carrier A", customer_city="Mumbai", lane_class="Local",
         attempt_number=1, order_id=1, awb="AWB1", shipment_uid="UID1",
         current_status="Delivered", has_ndr=False, ndr_reason="Not Applicable"):
    return {
        "edd": pd.Timestamp(edd), "is_delivered": is_delivered,
        "delivery_date": pd.Timestamp(delivery_date) if delivery_date else pd.NaT,
        "is_rto": is_rto, "is_lost": is_lost, "is_open": is_open, "carrier": carrier,
        "customer_city": customer_city, "lane_class": lane_class,
        "attempt_number": attempt_number, "order_id": order_id, "awb": awb,
        "shipment_uid": shipment_uid, "current_status": current_status,
        "has_ndr": has_ndr, "ndr_reason": ndr_reason,
    }


class TestIsBreached(unittest.TestCase):
    def test_on_time_delivery_not_breached(self):
        df = pd.DataFrame([_row(YESTERDAY, is_delivered=True, delivery_date=YESTERDAY)])
        self.assertFalse(bool(_is_breached(df, AS_OF).iloc[0]))

    def test_late_delivery_is_breached(self):
        df = pd.DataFrame([_row(YESTERDAY, is_delivered=True, delivery_date=AS_OF)])
        self.assertTrue(bool(_is_breached(df, AS_OF).iloc[0]))

    def test_rto_is_breached(self):
        df = pd.DataFrame([_row(YESTERDAY, is_rto=True)])
        self.assertTrue(bool(_is_breached(df, AS_OF).iloc[0]))

    def test_lost_is_breached(self):
        df = pd.DataFrame([_row(YESTERDAY, is_lost=True)])
        self.assertTrue(bool(_is_breached(df, AS_OF).iloc[0]))

    def test_still_open_with_edd_passed_is_breached(self):
        df = pd.DataFrame([_row(YESTERDAY, is_open=True, current_status="In-Transit")])
        self.assertTrue(bool(_is_breached(df, AS_OF).iloc[0]))

    def test_still_open_with_edd_in_future_not_yet_breached(self):
        df = pd.DataFrame([_row(TOMORROW, is_open=True, current_status="In-Transit")])
        self.assertFalse(bool(_is_breached(df, AS_OF).iloc[0]))


class TestCarrierEddBreachSummary(unittest.TestCase):
    def test_breach_pct_computed_per_carrier(self):
        rows = [
            _row(YESTERDAY, is_delivered=True, delivery_date=YESTERDAY, carrier="Carrier A"),
            _row(YESTERDAY, is_rto=True, carrier="Carrier A"),
            _row(YESTERDAY, is_delivered=True, delivery_date=YESTERDAY, carrier="Carrier B"),
        ]
        df = pd.DataFrame(rows)
        df["breached"] = _is_breached(df, AS_OF)
        summary = carrier_edd_breach_summary(df)
        a = summary[summary["carrier"] == "Carrier A"].iloc[0]
        b = summary[summary["carrier"] == "Carrier B"].iloc[0]
        self.assertEqual(a["shipment_volume"], 2)
        self.assertEqual(a["breached_shipments"], 1)
        self.assertAlmostEqual(a["breach_pct"], 50.0)
        self.assertEqual(b["breached_shipments"], 0)
        self.assertAlmostEqual(b["edd_adherence_pct"], 100.0)

    def test_sorted_worst_carrier_first(self):
        rows = [
            _row(YESTERDAY, is_delivered=True, delivery_date=YESTERDAY, carrier="Carrier Good"),
            _row(YESTERDAY, is_rto=True, carrier="Carrier Bad"),
        ]
        df = pd.DataFrame(rows)
        df["breached"] = _is_breached(df, AS_OF)
        summary = carrier_edd_breach_summary(df)
        self.assertEqual(summary.iloc[0]["carrier"], "Carrier Bad")


class TestLaneEddBreachTop(unittest.TestCase):
    def test_below_min_volume_lane_excluded(self):
        rows = [_row(YESTERDAY, is_rto=True, customer_city="TinyTown", lane_class="Local")]
        df = pd.DataFrame(rows)
        df["breached"] = _is_breached(df, AS_OF)
        top = lane_edd_breach_top(df, top_n=20, min_volume=3)
        self.assertTrue(top.empty)

    def test_ranked_by_breach_count_desc(self):
        rows = (
            [_row(YESTERDAY, is_rto=True, customer_city="BigCity", lane_class="Metro", order_id=i) for i in range(5)]
            + [_row(YESTERDAY, is_delivered=True, delivery_date=YESTERDAY, customer_city="BigCity", lane_class="Metro", order_id=100 + i) for i in range(3)]
            + [_row(YESTERDAY, is_rto=True, customer_city="SmallCity", lane_class="Local", order_id=200 + i) for i in range(4)]
        )
        df = pd.DataFrame(rows)
        df["breached"] = _is_breached(df, AS_OF)
        top = lane_edd_breach_top(df, top_n=20, min_volume=3)
        self.assertEqual(top.iloc[0]["customer_city"], "BigCity")
        self.assertEqual(top.iloc[0]["breached_shipments"], 5)
        self.assertEqual(top.iloc[0]["rank"], 1)
        self.assertEqual(top.iloc[1]["customer_city"], "SmallCity")

    def test_respects_top_n(self):
        rows = []
        for i in range(25):
            rows += [_row(YESTERDAY, is_rto=True, customer_city=f"City{i}", lane_class="Local", order_id=i * 10 + j) for j in range(3)]
        df = pd.DataFrame(rows)
        df["breached"] = _is_breached(df, AS_OF)
        top = lane_edd_breach_top(df, top_n=20, min_volume=3)
        self.assertEqual(len(top), 20)


if __name__ == "__main__":
    unittest.main()
