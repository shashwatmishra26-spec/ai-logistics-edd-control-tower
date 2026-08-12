"""
End-to-end pipeline runner for the AI Logistics EDD Control Tower.

    python run_pipeline.py

Runs every stage in order: ingest -> clean -> features -> train model ->
predict -> EDD breach alerts -> lane engine (incl. padding recs) -> carrier
engine -> NDR agent -> NDR consolidated report/IVR/outreach -> COD agent ->
decision engine -> root-cause -> simulation -> dashboard export -> dashboard
build. Each stage writes its own CSV/JSON to data/processed/ or outputs/, so
you can also run any single module independently (`python -m src.data.clean`)
once its upstream dependency has been generated at least once.
"""

import time


def main():
    stages = [
        ("Clean raw data", "src.data.clean"),
        ("Build features", "src.features.build_features"),
        ("Train EDD risk + NDR models", "src.models.edd_risk_model"),
        ("Score predictions", "src.predictions.predict"),
        ("EDD breach alert agent", "src.alerts_agent.edd_breach_alerts"),
        ("Lane intelligence (incl. padding recs)", "src.lane_engine.lane_intelligence"),
        ("Carrier optimization", "src.carrier_engine.carrier_optimization"),
        ("NDR recovery agent", "src.ndr_agent.ndr_recovery"),
        ("NDR consolidated report + IVR + outreach", "src.ndr_agent.ndr_consolidated_report"),
        ("COD remittance agent", "src.remittance_agent.cod_remittance"),
        ("Central decision engine", "src.decision_engine.central_decision_engine"),
        ("Root-cause engine", "src.decision_engine.root_cause"),
        ("Intervention simulator", "src.models.intervention_simulator"),
        ("Dashboard data export", "src.decision_engine.dashboard_export"),
    ]
    import importlib
    t0 = time.time()
    for label, module in stages:
        print(f"\n{'='*70}\n{label}  ({module})\n{'='*70}")
        mod = importlib.import_module(module)
        mod.run()

    print(f"\n{'='*70}\nBuilding dashboard HTML\n{'='*70}")
    import subprocess, sys
    subprocess.run([sys.executable, "dashboard/build_dashboard.py"], check=True)

    print(f"\nPipeline complete in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
