"""Tests for src/features/build_features.py — date math, EDD/NDR/RTO logic,
lane classification, attempt derivation. Includes edge cases."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.features.build_features import build_features


def _clean_row(**overrides):
    row = {
        "order_id": 1,
        "customer_city": "Mumbai",
        "customer_pincode": "400001",
        "package_amount": 500,
        "product_sku": "SKU-1",
        "warehouse_pincode": "400016",
        "warehouse_city": "Mumbai",
        "order_date": pd.Timestamp("2026-01-01"),
        "pickup_date": pd.Timestamp("2026-01-02"),
        "first_attempt_date": pd.Timestamp("2026-01-05"),
        "delivery_date": pd.Timestamp("2026-01-05"),
        "edd": pd.Timestamp("2026-01-06"),
        "ndr_reason": "Not Applicable",
        "payment_mode": "COD",
        "current_status": "Delivered",
        "customer_name_masked": "T*** C***",
        "customer_phone_masked": "XXXXXX0000",
        "customer_address_masked": "[REDACTED]",
        "shipment_uid": "ABC1234567",
        "dq_flags": "",
        "data_confidence_core": "ACTUAL",
    }
    row.update(overrides)
    return row


def _df(*rows):
    return pd.DataFrame(list(rows))


class TestEddCalculation(unittest.TestCase):
    def test_delivered_on_time_meets_edd(self):
        df = build_features(_df(_clean_row(delivery_date=pd.Timestamp("2026-01-06"), edd=pd.Timestamp("2026-01-06"))))
        self.assertTrue(df["edd_met"].iloc[0])
        self.assertFalse(df["edd_missed"].iloc[0])

    def test_delivered_after_edd_is_a_miss(self):
        df = build_features(_df(_clean_row(delivery_date=pd.Timestamp("2026-01-08"), edd=pd.Timestamp("2026-01-06"))))
        self.assertFalse(df["edd_met"].iloc[0])
        self.assertTrue(df["edd_missed"].iloc[0])

    def test_rto_is_not_edd_met(self):
        df = build_features(_df(_clean_row(
            current_status="RTO", delivery_date=pd.NaT, ndr_reason="Customer refused delivery",
        )))
        self.assertFalse(df["edd_met"].iloc[0])
        self.assertFalse(df["edd_missed"].iloc[0])  # not "missed", it's RTO
        self.assertTrue(df["is_rto"].iloc[0])
        self.assertEqual(df["outcome_label"].iloc[0], "RTO")

    def test_lost_shipment_outcome(self):
        df = build_features(_df(_clean_row(current_status="Lost", delivery_date=pd.NaT)))
        self.assertTrue(df["is_lost"].iloc[0])
        self.assertEqual(df["outcome_label"].iloc[0], "Lost")

    def test_open_shipment_not_counted_as_hit_or_miss(self):
        df = build_features(_df(_clean_row(current_status="In-Transit", delivery_date=pd.NaT, first_attempt_date=pd.NaT)))
        self.assertFalse(df["edd_met"].iloc[0])
        self.assertFalse(df["edd_missed"].iloc[0])
        self.assertTrue(df["is_open"].iloc[0])
        self.assertEqual(df["outcome_label"].iloc[0], "Open")


class TestNdrRtoClassification(unittest.TestCase):
    def test_ndr_reason_maps_to_category(self):
        df = build_features(_df(_clean_row(
            current_status="Out of delivery", ndr_reason="Phone not reachable", delivery_date=pd.NaT,
        )))
        self.assertTrue(df["has_ndr"].iloc[0])
        self.assertEqual(df["ndr_category"].iloc[0], "Contact Issue")

    def test_not_applicable_reason_is_no_ndr(self):
        df = build_features(_df(_clean_row(ndr_reason="Not Applicable")))
        self.assertFalse(df["has_ndr"].iloc[0])
        self.assertEqual(df["ndr_category"].iloc[0], "N/A")

    def test_rto_reason_only_set_for_rto_status(self):
        df = build_features(_df(
            _clean_row(order_id=1, current_status="RTO", ndr_reason="Customer cancelled order", delivery_date=pd.NaT),
            _clean_row(order_id=2, current_status="Delivered", ndr_reason="Not Applicable"),
        ))
        self.assertEqual(df.loc[df["order_id"] == 1, "rto_reason"].iloc[0], "Customer cancelled order")
        self.assertEqual(df.loc[df["order_id"] == 2, "rto_reason"].iloc[0], "Not Applicable")

    def test_unrecognized_ndr_reason_falls_back_to_other(self):
        df = build_features(_df(_clean_row(
            current_status="Out of delivery", ndr_reason="Some new reason nobody has seen", delivery_date=pd.NaT,
        )))
        self.assertEqual(df["ndr_category"].iloc[0], "Other")


class TestAttemptNumberDerivation(unittest.TestCase):
    def test_delivered_no_ndr_is_first_attempt(self):
        df = build_features(_df(_clean_row(ndr_reason="Not Applicable")))
        self.assertEqual(df["attempt_number"].iloc[0], 1)
        self.assertTrue(df["first_attempt_success"].iloc[0])

    def test_delivered_with_ndr_is_multi_attempt(self):
        df = build_features(_df(_clean_row(
            ndr_reason="Customer not available",
            first_attempt_date=pd.Timestamp("2026-01-03"),
            delivery_date=pd.Timestamp("2026-01-05"),
        )))
        self.assertGreaterEqual(df["attempt_number"].iloc[0], 2)
        self.assertFalse(df["first_attempt_success"].iloc[0])

    def test_not_yet_attempted_is_zero(self):
        df = build_features(_df(_clean_row(current_status="Order Packed", first_attempt_date=pd.NaT, delivery_date=pd.NaT)))
        self.assertEqual(df["attempt_number"].iloc[0], 0)


class TestLaneClassification(unittest.TestCase):
    def test_mumbai_is_local(self):
        df = build_features(_df(_clean_row(customer_city="Mumbai", customer_pincode="400001")))
        self.assertEqual(df["lane_class"].iloc[0], "Local")

    def test_bangalore_is_metro(self):
        df = build_features(_df(_clean_row(customer_city="Bangalore", customer_pincode="560001")))
        self.assertEqual(df["lane_class"].iloc[0], "Metro")

    def test_same_zone_non_metro_is_regional(self):
        df = build_features(_df(_clean_row(customer_city="Nagpur", customer_pincode="440001")))
        self.assertEqual(df["lane_class"].iloc[0], "Regional")

    def test_far_zone_is_national(self):
        # Guwahati is zone 7 (far from origin zone 4, Mumbai) and not in the
        # metro list, so it must fall through to National.
        df = build_features(_df(_clean_row(customer_city="Guwahati", customer_pincode="781001")))
        self.assertEqual(df["lane_class"].iloc[0], "National")

    def test_metro_city_classified_metro_even_in_far_zone(self):
        # Kolkata is in the metro list even though its pincode zone (7) is
        # far from the origin zone (4) — metro status takes precedence.
        df = build_features(_df(_clean_row(customer_city="Kolkata", customer_pincode="700001")))
        self.assertEqual(df["lane_class"].iloc[0], "Metro")

    def test_missing_pincode_does_not_crash(self):
        df = build_features(_df(_clean_row(customer_city="Unknown Town", customer_pincode=pd.NA)))
        self.assertIn(df["lane_class"].iloc[0], {"Local", "Metro", "Regional", "National"})

    def test_distance_is_deterministic(self):
        df1 = build_features(_df(_clean_row(customer_city="Kolkata", customer_pincode="700001")))
        df2 = build_features(_df(_clean_row(customer_city="Kolkata", customer_pincode="700001")))
        self.assertEqual(df1["distance_km"].iloc[0], df2["distance_km"].iloc[0])


class TestCarrierAssignmentDeterminism(unittest.TestCase):
    def test_same_order_id_always_gets_same_carrier(self):
        df1 = build_features(_df(_clean_row(order_id=55555)))
        df2 = build_features(_df(_clean_row(order_id=55555)))
        self.assertEqual(df1["carrier"].iloc[0], df2["carrier"].iloc[0])


if __name__ == "__main__":
    unittest.main()
