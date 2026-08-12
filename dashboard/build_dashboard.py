"""
Builds the self-contained dashboard/index.html by embedding
dashboard/dashboard_data.json directly into dashboard/template.html
(replacing __DASHBOARD_DATA_JSON__ and __SNAPSHOT_DATE__ / __BASELINE_PCT__ /
__TARGET_PCT__ placeholders). Run this after dashboard_export.py.

Embedding the data (rather than fetch()-ing the JSON) means the dashboard
works when opened directly as a local file (file://), with no HTTP server
required — a genuinely self-contained artifact.
"""

from pathlib import Path
import json

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = DASHBOARD_DIR / "template.html"
DATA_PATH = DASHBOARD_DIR / "dashboard_data.json"
OUTPUT_PATH = DASHBOARD_DIR / "index.html"


def run():
    with open(DATA_PATH) as f:
        data = json.load(f)
    template = TEMPLATE_PATH.read_text()

    baseline_pct = round(data["kpis"]["actual"]["edd_adherence"] * 100, 1)
    target_pct = round(data["meta"]["edd_target"] * 100)
    snapshot = data["meta"]["snapshot_date"]

    html = template.replace("__DASHBOARD_DATA_JSON__", json.dumps(data))
    html = html.replace("__SNAPSHOT_DATE__", str(snapshot))
    html = html.replace("__BASELINE_PCT__", str(baseline_pct))
    html = html.replace("__TARGET_PCT__", str(target_pct))

    OUTPUT_PATH.write_text(html)
    print(f"Built dashboard -> {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    run()
