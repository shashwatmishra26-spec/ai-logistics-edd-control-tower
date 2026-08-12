"""
Data ingestion layer.

Reads the raw "Logistics_workbook" Excel file (Raw Data + Validation sheets)
and returns a pandas DataFrame with normalized column names. This is the only
module that touches the raw .xlsx file — every downstream module works off
the cleaned/processed CSV so the pipeline can be re-run cheaply.
"""

from pathlib import Path
import sys

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import RAW_DATA_PATH  # noqa: E402

# Raw column name -> normalized snake_case column name.
COLUMN_MAP = {
    "Order Number": "order_id",
    "Customer Name": "customer_name",
    "Customer Phone": "customer_phone",
    "Customer Address": "customer_address",
    "Customer City": "customer_city",
    "Customer Pincode": "customer_pincode",
    "Package amount": "package_amount",
    "Product SKU": "product_sku",
    "Warehouse Address": "warehouse_address",
    "warehouse Pincode": "warehouse_pincode",
    "Warehouse City": "warehouse_city",
    "Order Date": "order_date",
    "Pickup date": "pickup_date",
    "First Attempt date": "first_attempt_date",
    "Delivery date": "delivery_date",
    "EDD": "edd",
    "NRD reason": "ndr_reason",
    "Payment Mode": "payment_mode",
    "Current Status": "current_status",
}

DATE_COLUMNS = ["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"]


def load_raw_shipments(path: Path = RAW_DATA_PATH) -> pd.DataFrame:
    """Load the 'Raw Data' sheet and normalize column names."""
    df = pd.read_excel(path, sheet_name="Raw Data")
    df = df.rename(columns=COLUMN_MAP)
    missing = set(COLUMN_MAP.values()) - set(df.columns)
    if missing:
        raise ValueError(f"Raw file is missing expected columns: {missing}")
    return df


def load_validation_sheet(path: Path = RAW_DATA_PATH) -> pd.DataFrame:
    """Load the 'Validation' sheet used to sanity-check the pipeline output."""
    return pd.read_excel(path, sheet_name="Validation")


if __name__ == "__main__":
    df = load_raw_shipments()
    print(f"Loaded {len(df)} raw shipment rows, {df.shape[1]} columns")
    print(df.dtypes)
