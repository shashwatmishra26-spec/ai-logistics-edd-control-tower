"""Tests for ingestion + cleaning (src/data/)."""
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.data.clean import clean_shipments
from src.data.ingest import load_raw_shipments, COLUMN_MAP


def _base_row(**overrides):
    row = {
        "order_id": 100000000001,
        "customer_name": "Test User",
        "customer_phone": 9876543210,
        "customer_address": "123 Test St",
        "customer_city": "Mumbai",
        "customer_pincode": "400001",
        "package_amount": 500,
        "product_sku": "SKU-1",
        "warehouse_address": "WH",
        "warehouse_pincode": 400016,
        "warehouse_city": "Mumbai",
        "order_date": pd.Timestamp("2026-01-01"),
        "pickup_date": pd.Timestamp("2026-01-02"),
        "first_attempt_date": pd.Timestamp("2026-01-05"),
        "delivery_date": pd.Timestamp("2026-01-05"),
        "edd": pd.Timestamp("2026-01-06"),
        "ndr_reason": None,
        "payment_mode": "COD",
        "current_status": "Delivered",
    }
    row.update(overrides)
    return row


class TestIngestion(unittest.TestCase):
    def test_raw_file_loads_and_has_expected_columns(self):
        df = load_raw_shipments()
        self.assertGreater(len(df), 0)
        for col in COLUMN_MAP.values():
            self.assertIn(col, df.columns)


class TestCleaning(unittest.TestCase):
    def test_duplicate_order_id_dropped(self):
        df = pd.DataFrame([_base_row(order_id=1), _base_row(order_id=1)])
        cleaned = clean_shipments(df)
        self.assertEqual(len(cleaned), 1)
        self.assertIn("duplicate_order_id_dropped", cleaned["dq_flags"].iloc[0])

    def test_invalid_pincode_flagged_and_nulled(self):
        df = pd.DataFrame([_base_row(customer_pincode="Rajkot")])
        cleaned = clean_shipments(df)
        self.assertIn("invalid_customer_pincode", cleaned["dq_flags"].iloc[0])
        self.assertTrue(pd.isna(cleaned["customer_pincode"].iloc[0]))

    def test_valid_pincode_preserved(self):
        df = pd.DataFrame([_base_row(customer_pincode="400001")])
        cleaned = clean_shipments(df)
        self.assertEqual(cleaned["customer_pincode"].iloc[0], "400001")

    def test_extreme_amount_flagged_not_altered(self):
        df = pd.DataFrame([_base_row(package_amount=360002)])
        cleaned = clean_shipments(df)
        self.assertIn("likely_data_entry_error_amount", cleaned["dq_flags"].iloc[0])
        # value must NOT be silently corrected/fabricated
        self.assertEqual(cleaned["package_amount"].iloc[0], 360002)

    def test_delivery_before_pickup_flagged(self):
        df = pd.DataFrame([_base_row(
            pickup_date=pd.Timestamp("2026-01-10"),
            delivery_date=pd.Timestamp("2026-01-05"),
        )])
        cleaned = clean_shipments(df)
        self.assertIn("delivery_before_pickup_date", cleaned["dq_flags"].iloc[0])

    def test_pii_masked_and_dropped(self):
        df = pd.DataFrame([_base_row(customer_name="Jane Doe", customer_phone=9876543210)])
        cleaned = clean_shipments(df)
        self.assertNotIn("customer_name", cleaned.columns)
        self.assertNotIn("customer_phone", cleaned.columns)
        self.assertNotIn("customer_address", cleaned.columns)
        self.assertTrue(cleaned["customer_phone_masked"].iloc[0].startswith("XXXXXX"))
        self.assertTrue(cleaned["customer_phone_masked"].iloc[0].endswith("3210"))

    def test_null_ndr_reason_becomes_not_applicable(self):
        df = pd.DataFrame([_base_row(ndr_reason=None)])
        cleaned = clean_shipments(df)
        self.assertEqual(cleaned["ndr_reason"].iloc[0], "Not Applicable")

    def test_edge_case_empty_dataframe(self):
        cols = list(_base_row().keys())
        df = pd.DataFrame(columns=cols)
        cleaned = clean_shipments(df)
        self.assertEqual(len(cleaned), 0)


if __name__ == "__main__":
    unittest.main()
