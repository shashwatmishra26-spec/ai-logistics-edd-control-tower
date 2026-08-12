"""Tests for lane scoring and carrier scoring (src/lane_engine, src/carrier_engine)."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.lane_engine.lane_intelligence import compute_lane_scorecard
from src.carrier_engine.carrier_optimization import (
    compute_carrier_scorecard,
    _two_proportion_z,
)


def _shipment(order_id, city, lane_class, carrier, edd_met, is_rto=False, is_lost=False,
              has_ndr=False, is_open=False, transit_days=2, cod=True, order_date="2026-01-01"):
    is_delivered = not is_rto and not is_lost and not is_open
    return {
        "order_id": order_id, "customer_city": city, "lane_class": lane_class, "carrier": carrier,
        "is_delivered": is_delivered, "edd_met": edd_met and is_delivered, "is_rto": is_rto,
        "is_lost": is_lost, "is_open": is_open, "has_ndr": has_ndr,
        "transit_actual_days": transit_days, "is_cod": cod, "carrier_sla_breach": False,
        "order_date": pd.Timestamp(order_date),
    }


class TestLaneScoring(unittest.TestCase):
    def test_insufficient_sample_flagged(self):
        rows = [_shipment(i, "TestCity", "Local", "Carrier A", True) for i in range(5)]
        df = pd.DataFrame(rows)
        scorecard = compute_lane_scorecard(df)
        row = scorecard[scorecard["customer_city"] == "TestCity"].iloc[0]
        self.assertEqual(row["lane_status"], "Insufficient Sample")

    def test_perfect_lane_is_best_performing(self):
        rows = [_shipment(i, "GoodCity", "Metro", "Carrier A", True, transit_days=1) for i in range(30)]
        df = pd.DataFrame(rows)
        scorecard = compute_lane_scorecard(df)
        row = scorecard[scorecard["customer_city"] == "GoodCity"].iloc[0]
        self.assertEqual(row["edd_adherence_pct"], 100.0)
        self.assertEqual(row["lane_status"], "Best Performing")

    def test_high_rto_lane_flagged_intervention(self):
        rows = []
        for i in range(30):
            is_rto = i < 15  # 50% RTO
            rows.append(_shipment(i, "BadCity", "Regional", "Carrier B", not is_rto, is_rto=is_rto))
        df = pd.DataFrame(rows)
        scorecard = compute_lane_scorecard(df)
        row = scorecard[scorecard["customer_city"] == "BadCity"].iloc[0]
        self.assertEqual(row["rto_pct"], 50.0)
        self.assertEqual(row["lane_status"], "Intervention Required")

    def test_open_shipments_excluded_from_edd_denominator(self):
        rows = [_shipment(i, "MixCity", "Local", "Carrier A", True) for i in range(20)]
        rows += [_shipment(100 + i, "MixCity", "Local", "Carrier A", False, is_open=True) for i in range(10)]
        df = pd.DataFrame(rows)
        scorecard = compute_lane_scorecard(df)
        row = scorecard[scorecard["customer_city"] == "MixCity"].iloc[0]
        self.assertEqual(row["edd_adherence_pct"], 100.0)  # all 20 CLOSED delivered met EDD


class TestCarrierScoring(unittest.TestCase):
    def test_carrier_scorecard_computes_edd_rate(self):
        rows = [_shipment(i, "City1", "Metro", "Carrier A", i % 2 == 0) for i in range(20)]
        df = pd.DataFrame(rows)
        scorecard = compute_carrier_scorecard(df)
        row = scorecard[scorecard["carrier"] == "Carrier A"].iloc[0]
        self.assertEqual(row["edd_adherence_pct"], 50.0)
        self.assertEqual(row["shipment_volume"], 20)

    def test_two_proportion_z_identical_rates_not_significant(self):
        z, p = _two_proportion_z(0.85, 100, 0.85, 100)
        self.assertAlmostEqual(z, 0.0)
        self.assertGreater(p, 0.05)

    def test_two_proportion_z_large_gap_significant(self):
        z, p = _two_proportion_z(0.95, 200, 0.60, 200)
        self.assertLess(p, 0.01)

    def test_two_proportion_z_handles_zero_volume(self):
        z, p = _two_proportion_z(0.9, 0, 0.5, 50)
        self.assertIsNone(z)
        self.assertIsNone(p)


if __name__ == "__main__":
    unittest.main()
