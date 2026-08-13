"""Tests for the two newest additions on top of the primary-objective agents:

  - src.carrier_engine.carrier_optimization.compute_carrier_watchlist
    (Carrier Partner Improvement / Volume-Shift Watchlist)
  - src.ndr_agent.ndr_recovery.assign_ndr_channel
    (NDR outreach channel routing: IVR / WhatsApp / Manual Agent Call / Email)
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.carrier_engine.carrier_optimization import compute_carrier_watchlist
from src.ndr_agent.ndr_recovery import assign_ndr_channel
from config.config import (
    MAX_LANE_EDD_CEILING_DAYS,
    NDR_MANUAL_CALL_AGE_HOURS,
    NDR_MANUAL_CALL_HIGH_VALUE_INR,
    NDR_MANUAL_CALL_MIN_ATTEMPT,
    SNAPSHOT_DATE,
    WATCHLIST_MIN_VOLUME,
)


# ---------------------------------------------------------------------------
# Carrier Partner Improvement / Volume-Shift Watchlist
# ---------------------------------------------------------------------------
def _lane_row(city, lane_class, status, edd_pct, volume):
    return {"customer_city": city, "lane_class": lane_class, "lane_status": status,
            "edd_adherence_pct": edd_pct, "shipment_volume": volume}


def _padding_row(city, lane_class, watchlist_candidate):
    return {"customer_city": city, "lane_class": lane_class, "watchlist_candidate": watchlist_candidate}


def _shipment_row(city, lane_class, carrier, is_delivered=True, edd_met=True, is_open=False):
    return {"customer_city": city, "lane_class": lane_class, "carrier": carrier,
            "is_open": is_open, "is_delivered": is_delivered, "edd_met": edd_met}


class TestCarrierWatchlist(unittest.TestCase):
    def test_transit_ceiling_breach_flags_lane_from_padding_df(self):
        vol = WATCHLIST_MIN_VOLUME + 5
        lane_df = pd.DataFrame([_lane_row("BreachCity", "Metro", "Watch", 92.0, vol)])
        padding_df = pd.DataFrame([_padding_row("BreachCity", "Metro", True)])
        df = pd.DataFrame([_shipment_row("BreachCity", "Metro", "Carrier A") for _ in range(vol)])

        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertIn("TRANSIT_CEILING_BREACH", row["flag_reasons"])
        self.assertEqual(row["primary_carrier"], "Carrier A")
        self.assertIsNotNone(row["mock_carrier_notice"])
        self.assertIn("Carrier A", row["mock_carrier_notice"])

    def test_chronic_underperformance_flags_lane_independent_of_padding(self):
        vol = WATCHLIST_MIN_VOLUME + 5
        # Intervention Required + big EDD gap to target, no padding-driven flag at all.
        lane_df = pd.DataFrame([_lane_row("ChronicCity", "National", "Intervention Required", 70.0, vol)])
        padding_df = pd.DataFrame([_padding_row("ChronicCity", "National", False)])
        df = pd.DataFrame([_shipment_row("ChronicCity", "National", "Carrier D") for _ in range(vol)])

        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertIn("CHRONIC_UNDERPERFORMANCE", row["flag_reasons"])

    def test_healthy_lane_is_not_watchlisted(self):
        vol = WATCHLIST_MIN_VOLUME + 5
        lane_df = pd.DataFrame([_lane_row("HealthyCity", "Local", "Best Performing", 98.0, vol)])
        padding_df = pd.DataFrame([_padding_row("HealthyCity", "Local", False)])
        df = pd.DataFrame([_shipment_row("HealthyCity", "Local", "Carrier B") for _ in range(vol)])

        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(len(out), 0)

    def test_below_min_volume_chronic_lane_is_excluded(self):
        vol = WATCHLIST_MIN_VOLUME - 5
        lane_df = pd.DataFrame([_lane_row("TinyCity", "National", "Intervention Required", 60.0, vol)])
        padding_df = pd.DataFrame([_padding_row("TinyCity", "National", False)])
        df = pd.DataFrame([_shipment_row("TinyCity", "National", "Carrier D") for _ in range(vol)])

        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(len(out), 0)

    def test_dominant_carrier_is_by_volume_share_on_exact_lane(self):
        vol = WATCHLIST_MIN_VOLUME + 5
        lane_df = pd.DataFrame([_lane_row("MixedCity", "Metro", "Watch", 90.0, vol)])
        padding_df = pd.DataFrame([_padding_row("MixedCity", "Metro", True)])
        # Carrier A dominates (majority share); Carrier B is a minority.
        rows = [_shipment_row("MixedCity", "Metro", "Carrier A") for _ in range(vol - 3)]
        rows += [_shipment_row("MixedCity", "Metro", "Carrier B") for _ in range(3)]
        df = pd.DataFrame(rows)

        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(out.iloc[0]["primary_carrier"], "Carrier A")
        self.assertGreater(out.iloc[0]["primary_carrier_share_pct"], 50)

    def test_empty_watchlist_returns_correct_columns(self):
        lane_df = pd.DataFrame([_lane_row("QuietCity", "Local", "Best Performing", 99.0, 50)])
        padding_df = pd.DataFrame([_padding_row("QuietCity", "Local", False)])
        df = pd.DataFrame([_shipment_row("QuietCity", "Local", "Carrier A") for _ in range(50)])
        out = compute_carrier_watchlist(df, lane_df, padding_df)
        self.assertEqual(len(out), 0)
        for col in ["customer_city", "lane_class", "primary_carrier", "mock_carrier_notice", "notice_deadline"]:
            self.assertIn(col, out.columns)


# ---------------------------------------------------------------------------
# NDR outreach channel routing
# ---------------------------------------------------------------------------
def _ndr_row(reason, attempt=0, is_cod=False, package_amount=500, first_attempt_date=None):
    return pd.Series({
        "ndr_reason": reason,
        "attempt_number": attempt,
        "is_cod": is_cod,
        "package_amount": package_amount,
        "first_attempt_date": first_attempt_date if first_attempt_date is not None else pd.Timestamp(SNAPSHOT_DATE),
    })


class TestNdrChannelRouting(unittest.TestCase):
    def setUp(self):
        self.snapshot = pd.Timestamp(SNAPSHOT_DATE)

    def test_first_touch_simple_reason_routes_to_ivr(self):
        row = _ndr_row("Customer not available", attempt=0)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "IVR")
        self.assertFalse(result["also_whatsapp"])

    def test_action_needed_reason_gets_parallel_whatsapp(self):
        row = _ndr_row("Address issue", attempt=0)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "IVR")
        self.assertTrue(result["also_whatsapp"])

    def test_repeat_failure_escalates_to_manual_call(self):
        row = _ndr_row("Landmark missing", attempt=NDR_MANUAL_CALL_MIN_ATTEMPT)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "Manual Agent Call")

    def test_high_value_cod_dispute_escalates_to_manual_call(self):
        row = _ndr_row("COD payment declined", attempt=0, is_cod=True,
                        package_amount=NDR_MANUAL_CALL_HIGH_VALUE_INR + 500)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "Manual Agent Call")

    def test_low_value_cod_order_on_simple_reason_does_not_force_manual_call(self):
        # is_cod alone (without a high-risk reason or an actual COD-dispute reason)
        # must not trip the expensive channel -- severity gates it, not payment mode.
        row = _ndr_row("Address issue", attempt=0, is_cod=True,
                        package_amount=NDR_MANUAL_CALL_HIGH_VALUE_INR - 400)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertNotEqual(result["recommended_channel"], "Manual Agent Call")
        # Still gets the parallel WhatsApp since it's an action-needed reason.
        self.assertTrue(result["also_whatsapp"])

    def test_aged_case_escalates_to_manual_call(self):
        old_date = self.snapshot - pd.Timedelta(hours=NDR_MANUAL_CALL_AGE_HOURS + 10)
        row = _ndr_row("Customer not available", attempt=0, first_attempt_date=old_date)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "Manual Agent Call")

    def test_phone_unreachable_routes_to_email(self):
        row = _ndr_row("Phone not reachable", attempt=0)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "Email")

    def test_high_risk_reason_escalates_to_manual_call_even_on_first_attempt(self):
        row = _ndr_row("Customer refused delivery", attempt=0)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertEqual(result["recommended_channel"], "Manual Agent Call")

    def test_rationale_is_non_empty_string(self):
        row = _ndr_row("Delivery postponed by customer", attempt=0)
        result = assign_ndr_channel(row, self.snapshot)
        self.assertIsInstance(result["channel_rationale"], str)
        self.assertGreater(len(result["channel_rationale"]), 0)


if __name__ == "__main__":
    unittest.main()
