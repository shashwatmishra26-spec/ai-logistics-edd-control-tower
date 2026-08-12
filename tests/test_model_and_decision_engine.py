"""Integration tests for the trained EDD risk model, the prediction pipeline,
and the central decision engine. These run against the actual pipeline
outputs (data/processed/, outputs/) — run `python run_pipeline.py` first if
they are missing; the tests themselves will skip gracefully if so."""
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.config import (
    ACTION_QUEUE_PATH,
    FEATURED_SHIPMENTS_PATH,
    MODEL_METRICS_PATH,
    PREDICTIONS_PATH,
    SIMULATION_PATH,
)


def _require(path: Path):
    if not path.exists():
        raise unittest.SkipTest(f"{path} not found — run `python run_pipeline.py` first")


class TestModelEvaluation(unittest.TestCase):
    def test_model_beats_random_baseline(self):
        _require(MODEL_METRICS_PATH)
        with open(MODEL_METRICS_PATH) as f:
            metrics = json.load(f)
        # 4-class problem: random guessing ~25% accuracy. The model must
        # clear a meaningfully higher bar.
        self.assertGreater(metrics["outcome_model"]["accuracy"], 0.5)

    def test_feature_importance_sums_reasonably(self):
        _require(MODEL_METRICS_PATH)
        with open(MODEL_METRICS_PATH) as f:
            metrics = json.load(f)
        importances = metrics["outcome_model"]["feature_importance"]
        total = sum(importances.values())
        self.assertAlmostEqual(total, 1.0, delta=0.05)

    def test_baseline_matches_validation_sheet(self):
        _require(MODEL_METRICS_PATH)
        with open(MODEL_METRICS_PATH) as f:
            metrics = json.load(f)
        # Validation sheet states 1546/1819 = 84.99%
        self.assertAlmostEqual(metrics["baseline_edd_adherence_actual"], 0.8499, delta=0.001)


class TestPredictions(unittest.TestCase):
    def test_predictions_cover_all_shipments(self):
        _require(PREDICTIONS_PATH)
        _require(FEATURED_SHIPMENTS_PATH)
        preds = pd.read_csv(PREDICTIONS_PATH)
        features = pd.read_csv(FEATURED_SHIPMENTS_PATH)
        self.assertEqual(len(preds), len(features))

    def test_risk_score_bounded_0_100(self):
        _require(PREDICTIONS_PATH)
        preds = pd.read_csv(PREDICTIONS_PATH)
        self.assertTrue((preds["edd_risk_score"] >= 0).all())
        self.assertTrue((preds["edd_risk_score"] <= 100).all())

    def test_every_row_has_a_reason_and_action(self):
        _require(PREDICTIONS_PATH)
        preds = pd.read_csv(PREDICTIONS_PATH)
        self.assertTrue(preds["risk_reason"].notna().all())
        self.assertTrue(preds["recommended_action"].notna().all())

    def test_closed_shipments_marked_no_action(self):
        _require(PREDICTIONS_PATH)
        preds = pd.read_csv(PREDICTIONS_PATH)
        delivered = preds[preds["current_status"] == "Delivered"]
        self.assertTrue((delivered["recommended_action"] == "No action — shipment closed").all())


class TestDecisionEngine(unittest.TestCase):
    def test_action_queue_sorted_by_priority(self):
        _require(ACTION_QUEUE_PATH)
        actions = pd.read_csv(ACTION_QUEUE_PATH)
        if len(actions) == 0:
            self.skipTest("no actions generated")
        rank = {"P1 - Urgent": 0, "P2 - High": 1, "P3 - Standard": 2}
        ranks = actions["priority"].map(rank).tolist()
        self.assertEqual(ranks, sorted(ranks))

    def test_action_queue_has_unique_ids(self):
        _require(ACTION_QUEUE_PATH)
        actions = pd.read_csv(ACTION_QUEUE_PATH)
        self.assertEqual(actions["action_id"].nunique(), len(actions))


class TestSimulation(unittest.TestCase):
    def test_simulation_never_decreases(self):
        _require(SIMULATION_PATH)
        sim = pd.read_csv(SIMULATION_PATH)
        rates = sim["cumulative_edd_adherence"].tolist()
        self.assertEqual(rates, sorted(rates))

    def test_baseline_row_labeled_actual(self):
        _require(SIMULATION_PATH)
        sim = pd.read_csv(SIMULATION_PATH)
        self.assertEqual(sim.iloc[0]["label"], "ACTUAL")

    def test_later_rows_labeled_projected_or_simulated(self):
        _require(SIMULATION_PATH)
        sim = pd.read_csv(SIMULATION_PATH)
        self.assertTrue(sim.iloc[1:]["label"].isin(["PROJECTED", "SIMULATED"]).all())

    def test_final_projection_reaches_or_exceeds_target(self):
        _require(SIMULATION_PATH)
        sim = pd.read_csv(SIMULATION_PATH)
        self.assertGreaterEqual(sim.iloc[-1]["cumulative_edd_adherence"], 0.94)


if __name__ == "__main__":
    unittest.main()
