"""
EDD Risk Prediction Agent — the core ML component of the control tower.

Two explainable, lightweight models are trained:

1. `outcome_model` (multiclass RandomForest): predicts P(Delivered On-Time),
   P(Delivered Late), P(RTO), P(Lost) for a shipment, trained on CLOSED
   shipments only (the ones with a known final outcome). This answers
   "will this shipment deliver within EDD?" for any shipment — including
   in-flight ones — using only features that are available in real time
   (carrier, lane, distance, SLA, payment mode, calendar, attempts-so-far).

2. `ndr_model` (LogisticRegression): predicts P(shipment experiences at
   least one NDR event) from dispatch-time-available features (carrier,
   lane, distance, COD/prepaid, calendar). Used to proactively flag
   shipments worth a pre-emptive customer nudge before an NDR even happens.

Both are intentionally simple, well-understood, explainable models (tree
feature_importances_ / logistic coefficients) rather than a black box —
this is a decision-support tool for logistics ops, not a research model.
"""

from pathlib import Path
import json
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (  # noqa: E402
    FEATURED_SHIPMENTS_PATH,
    MODEL_METRICS_PATH,
    MODEL_PATH,
    OUTPUTS_DIR,
    RANDOM_SEED,
)

OUTCOME_CAT_FEATURES = ["carrier", "lane_class", "payment_mode", "order_dow"]
OUTCOME_NUM_FEATURES = [
    "distance_km",
    "transit_sla_days",
    "delivery_sla_days",
    "package_amount",
    "attempt_number",
    "order_to_pickup_days",
    "shipment_ageing_days",
    "is_weekend_order",
    "pickup_sla_breach",
    "carrier_hist_edd_rate",
    "lane_hist_edd_rate",
]
OUTCOME_TARGET = "outcome_label"
OUTCOME_CLASSES_ORDER = ["Delivered On-Time", "Delivered Late", "RTO", "Lost"]

NDR_CAT_FEATURES = ["carrier", "lane_class", "payment_mode", "order_dow"]
NDR_NUM_FEATURES = ["distance_km", "package_amount", "is_weekend_order", "carrier_hist_ndr_rate", "lane_hist_ndr_rate"]
NDR_TARGET = "has_ndr"


def _add_historical_rate_features(train_df: pd.DataFrame, full_df: pd.DataFrame) -> pd.DataFrame:
    """Compute carrier/lane historical EDD & NDR rates from the TRAIN split
    only (avoids leakage), then map them onto the full dataframe so open
    (in-flight) shipments also get a rate even though they have no outcome
    yet."""
    carrier_edd = train_df.groupby("carrier")["edd_met"].mean().rename("carrier_hist_edd_rate")
    lane_edd = train_df.groupby("lane_class")["edd_met"].mean().rename("lane_hist_edd_rate")
    carrier_ndr = train_df.groupby("carrier")["has_ndr"].mean().rename("carrier_hist_ndr_rate")
    lane_ndr = train_df.groupby("lane_class")["has_ndr"].mean().rename("lane_hist_ndr_rate")

    out = full_df.merge(carrier_edd, on="carrier", how="left")
    out = out.merge(lane_edd, on="lane_class", how="left")
    out = out.merge(carrier_ndr, on="carrier", how="left")
    out = out.merge(lane_ndr, on="lane_class", how="left")
    global_edd = train_df["edd_met"].mean()
    global_ndr = train_df["has_ndr"].mean()
    out["carrier_hist_edd_rate"] = out["carrier_hist_edd_rate"].fillna(global_edd)
    out["lane_hist_edd_rate"] = out["lane_hist_edd_rate"].fillna(global_edd)
    out["carrier_hist_ndr_rate"] = out["carrier_hist_ndr_rate"].fillna(global_ndr)
    out["lane_hist_ndr_rate"] = out["lane_hist_ndr_rate"].fillna(global_ndr)
    return out


def _build_pipeline(cat_features, num_features, estimator) -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat_features),
            ("num", "passthrough", num_features),
        ]
    )
    return Pipeline([("pre", pre), ("clf", estimator)])


def train(df: pd.DataFrame):
    closed = df[~df["is_open"]].copy()
    closed = closed[closed["outcome_label"] != "Open"]

    train_raw, test_raw = train_test_split(
        closed, test_size=0.2, random_state=RANDOM_SEED, stratify=closed[OUTCOME_TARGET]
    )

    # Historical-rate features computed on TRAIN only, applied everywhere.
    full_with_rates = _add_historical_rate_features(train_raw, df)
    train_df = full_with_rates.loc[train_raw.index]
    test_df = full_with_rates.loc[test_raw.index]

    # --- Model 1: outcome / EDD risk model ---------------------------------
    outcome_pipe = _build_pipeline(
        OUTCOME_CAT_FEATURES,
        OUTCOME_NUM_FEATURES,
        RandomForestClassifier(
            n_estimators=300, max_depth=9, min_samples_leaf=5,
            class_weight="balanced", random_state=RANDOM_SEED, n_jobs=-1,
        ),
    )
    X_train = train_df[OUTCOME_CAT_FEATURES + OUTCOME_NUM_FEATURES]
    y_train = train_df[OUTCOME_TARGET]
    X_test = test_df[OUTCOME_CAT_FEATURES + OUTCOME_NUM_FEATURES]
    y_test = test_df[OUTCOME_TARGET]

    outcome_pipe.fit(X_train, y_train)
    y_pred = outcome_pipe.predict(X_test)
    y_proba = outcome_pipe.predict_proba(X_test)
    classes = outcome_pipe.named_steps["clf"].classes_.tolist()

    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, y_pred, labels=classes).tolist()

    # One-vs-rest AUC for "miss EDD" (i.e. not Delivered On-Time) as the
    # headline binary metric business stakeholders care about.
    ontime_idx = classes.index("Delivered On-Time")
    y_test_binary = (y_test != "Delivered On-Time").astype(int)
    miss_proba = 1 - y_proba[:, ontime_idx]
    try:
        auc = roc_auc_score(y_test_binary, miss_proba)
    except ValueError:
        auc = None

    # Feature importance (post-OHE, aggregated back to original feature names)
    ohe: OneHotEncoder = outcome_pipe.named_steps["pre"].named_transformers_["cat"]
    ohe_names = list(ohe.get_feature_names_out(OUTCOME_CAT_FEATURES))
    all_feature_names = ohe_names + OUTCOME_NUM_FEATURES
    importances = outcome_pipe.named_steps["clf"].feature_importances_
    imp_df = pd.DataFrame({"feature": all_feature_names, "importance": importances})
    # Aggregate one-hot columns back to their source categorical feature
    def _base_feature(name):
        for c in OUTCOME_CAT_FEATURES:
            if name.startswith(c + "_"):
                return c
        return name
    imp_df["base_feature"] = imp_df["feature"].apply(_base_feature)
    agg_importance = (
        imp_df.groupby("base_feature")["importance"].sum().sort_values(ascending=False)
    )

    # --- Model 2: NDR propensity model --------------------------------------
    ndr_pipe = _build_pipeline(
        NDR_CAT_FEATURES, NDR_NUM_FEATURES,
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )
    ndr_train = full_with_rates.loc[train_raw.index]
    ndr_test = full_with_rates.loc[test_raw.index]
    Xn_train = ndr_train[NDR_CAT_FEATURES + NDR_NUM_FEATURES]
    yn_train = ndr_train[NDR_TARGET]
    Xn_test = ndr_test[NDR_CAT_FEATURES + NDR_NUM_FEATURES]
    yn_test = ndr_test[NDR_TARGET]
    ndr_pipe.fit(Xn_train, yn_train)
    ndr_proba_test = ndr_pipe.predict_proba(Xn_test)[:, 1]
    try:
        ndr_auc = roc_auc_score(yn_test, ndr_proba_test)
    except ValueError:
        ndr_auc = None

    metrics = {
        "outcome_model": {
            "n_train": len(train_df),
            "n_test": len(test_df),
            "classes": classes,
            "accuracy": round(acc, 4),
            "roc_auc_edd_miss_binary": round(auc, 4) if auc else None,
            "classification_report": report,
            "confusion_matrix": cm,
            "feature_importance": agg_importance.round(4).to_dict(),
        },
        "ndr_model": {
            "n_train": len(ndr_train),
            "n_test": len(ndr_test),
            "roc_auc": round(ndr_auc, 4) if ndr_auc else None,
        },
        "baseline_edd_adherence_actual": round(df["edd_met"].sum() / df["is_delivered"].sum(), 4),
    }

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    bundle = {
        "outcome_pipe": outcome_pipe,
        "ndr_pipe": ndr_pipe,
        "outcome_classes": classes,
        "carrier_hist_edd_rate": train_df.groupby("carrier")["edd_met"].mean().to_dict(),
        "lane_hist_edd_rate": train_df.groupby("lane_class")["edd_met"].mean().to_dict(),
        "carrier_hist_ndr_rate": train_df.groupby("carrier")["has_ndr"].mean().to_dict(),
        "lane_hist_ndr_rate": train_df.groupby("lane_class")["has_ndr"].mean().to_dict(),
        "global_edd_rate": float(train_df["edd_met"].mean()),
        "global_ndr_rate": float(train_df["has_ndr"].mean()),
    }
    joblib.dump(bundle, MODEL_PATH)
    print(f"Outcome model accuracy={acc:.3f} AUC(miss-EDD)={auc}")
    print(f"NDR model AUC={ndr_auc}")
    print("Top feature importances:\n", agg_importance)
    print(f"Saved model bundle -> {MODEL_PATH}")
    print(f"Saved metrics -> {MODEL_METRICS_PATH}")
    return bundle, metrics


def run():
    df = pd.read_csv(
        FEATURED_SHIPMENTS_PATH,
        parse_dates=["order_date", "pickup_date", "first_attempt_date", "delivery_date", "edd"],
    )
    return train(df)


if __name__ == "__main__":
    run()
