#!/usr/bin/env python3
"""
grafana.py — Query a Grafana dashboard panel and check values against thresholds.

Usage:
    python3 grafana.py

Configuration:
    Edit the CONFIG block below, or set environment variables:
        GRAFANA_URL   — base URL of your Grafana instance
        GRAFANA_TOKEN — API token or service account token
"""

import json
import os
import time
import sys
import requests

# ---------------------------------------------------------------------------
# Configuration — edit these or set environment variables
# ---------------------------------------------------------------------------

CONFIG = {
    "url":           os.getenv("GRAFANA_URL",   "http://localhost:3000"),
    "token":         os.getenv("GRAFANA_TOKEN", "your-api-token-here"),
    "dashboard_uid": "abc123",       # found in the Grafana dashboard URL
    "panel_title":   "CPU Usage",    # exact panel title to query (or set panel_id below)
    "panel_id":      None,           # set to an int to skip title lookup
    "datasource_id": 1,              # Grafana datasource ID (Settings → Data Sources)
    "promql":        'rate(node_cpu_seconds_total{mode="user"}[5m]) * 100',
    "range_from":    "now-1h",       # Grafana time range shorthand
    "range_to":      "now",
    "max_points":    100,
}

# Thresholds — each entry: (label, operator, value)
#   operator: ">"  ">="  "<"  "<="  "=="
#
# Example: alert if any CPU value exceeds 80%, or if it drops below 5%
THRESHOLDS = [
    ("CPU too high",  ">",  80.0),
    ("CPU very low",  "<",   5.0),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {CONFIG['token']}",
        "Content-Type":  "application/json",
    }


def _time_ms(shorthand: str) -> str:
    """Convert 'now-1h' / 'now' to epoch milliseconds string."""
    if shorthand == "now":
        return str(int(time.time() * 1000))
    if shorthand.startswith("now-"):
        suffix = shorthand[4:]
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit = suffix[-1]
        amount = int(suffix[:-1])
        delta = amount * units.get(unit, 60)
        return str(int((time.time() - delta) * 1000))
    return shorthand  # already a raw ms value


def _check(value: float, operator: str, threshold: float) -> bool:
    return {
        ">":  value >  threshold,
        ">=": value >= threshold,
        "<":  value <  threshold,
        "<=": value <= threshold,
        "==": value == threshold,
    }.get(operator, False)

# ---------------------------------------------------------------------------
# Step 1 — fetch dashboard, find panel
# ---------------------------------------------------------------------------

def get_panel_id(session: requests.Session) -> int:
    """Return the panel ID for the configured panel title, or CONFIG['panel_id']."""
    if CONFIG["panel_id"] is not None:
        return CONFIG["panel_id"]

    url = f"{CONFIG['url']}/api/dashboards/uid/{CONFIG['dashboard_uid']}"
    resp = session.get(url)
    resp.raise_for_status()
    dashboard = resp.json()["dashboard"]

    print(f"\nDashboard: {dashboard['title']}")
    print(f"{'ID':>4}  Title")
    print("-" * 40)

    panel_id = None
    for panel in dashboard.get("panels", []):
        pid   = panel.get("id", "?")
        title = panel.get("title", "(no title)")
        match = "  ← matched" if title == CONFIG["panel_title"] else ""
        print(f"{pid:>4}  {title}{match}")
        if title == CONFIG["panel_title"]:
            panel_id = pid

    if panel_id is None:
        raise ValueError(
            f"Panel '{CONFIG['panel_title']}' not found. "
            "Set panel_id in CONFIG or fix panel_title."
        )
    return panel_id


# ---------------------------------------------------------------------------
# Step 2 — query panel data
# ---------------------------------------------------------------------------

def query_panel(session: requests.Session) -> list[dict]:
    """
    Query the datasource and return a list of series dicts:
        {"name": str, "values": [float, ...], "timestamps": [int, ...]}
    """
    payload = {
        "queries": [
            {
                "datasourceId": CONFIG["datasource_id"],
                "expr":         CONFIG["promql"],
                "refId":        "A",
                "maxDataPoints": CONFIG["max_points"],
                "intervalMs":   60_000,
            }
        ],
        "from": _time_ms(CONFIG["range_from"]),
        "to":   _time_ms(CONFIG["range_to"]),
    }

    url = f"{CONFIG['url']}/api/ds/query"
    resp = session.post(url, json=payload)
    resp.raise_for_status()
    raw = resp.json()

    series = []
    for frame in raw.get("results", {}).get("A", {}).get("frames", []):
        fields = frame["schema"]["fields"]
        values_data = frame["data"]["values"]

        # fields[0] is usually timestamps, remaining are value series
        timestamps = values_data[0] if values_data else []
        for i, field in enumerate(fields[1:], start=1):
            name = field.get("labels", {}) or field.get("name", f"series_{i}")
            series.append({
                "name":       str(name),
                "timestamps": timestamps,
                "values":     values_data[i] if i < len(values_data) else [],
            })

    return series


# ---------------------------------------------------------------------------
# Step 3 — check thresholds
# ---------------------------------------------------------------------------

def check_thresholds(series: list[dict]) -> bool:
    """
    Print latest value per series and evaluate each threshold rule.
    Returns True if any threshold is breached.
    """
    if not THRESHOLDS:
        print("\n(No thresholds configured.)")
        return False

    breached = False

    print(f"\n{'Series':<45} {'Latest':>10}  Threshold checks")
    print("-" * 80)

    for s in series:
        vals = [v for v in s["values"] if v is not None]
        if not vals:
            print(f"  {s['name']:<43}  (no data)")
            continue

        latest = vals[-1]
        results = []
        for label, op, threshold in THRESHOLDS:
            hit = _check(latest, op, threshold)
            if hit:
                breached = True
            icon = "🔴 BREACH" if hit else "🟢 ok"
            results.append(f"{icon} [{label}: {latest:.2f} {op} {threshold}]")

        print(f"  {s['name']:<43} {latest:>10.2f}  " + "  ".join(results))

    return breached


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    base_url = CONFIG["url"].rstrip("/")
    CONFIG["url"] = base_url

    print(f"Grafana: {base_url}")
    print(f"Dashboard UID: {CONFIG['dashboard_uid']}")

    session = requests.Session()
    session.headers.update(_headers())

    # Health check
    health = session.get(f"{base_url}/api/health")
    health.raise_for_status()
    print(f"Health: {health.json().get('database', 'ok')}")

    # Step 1 — find the panel
    panel_id = get_panel_id(session)
    print(f"\nQuerying panel ID {panel_id}  ({CONFIG['range_from']} → {CONFIG['range_to']})")
    print(f"Query: {CONFIG['promql']}")

    # Step 2 — fetch data
    series = query_panel(session)
    if not series:
        print("\nNo data returned. Check datasource_id and promql in CONFIG.")
        sys.exit(1)

    print(f"\nGot {len(series)} series.")

    # Step 3 — threshold check
    any_breached = check_thresholds(series)

    print()
    if any_breached:
        print("⚠️  One or more thresholds BREACHED.")
        sys.exit(2)   # non-zero exit so CI/scripts can detect it
    else:
        print("✅  All values within thresholds.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\nHTTP error: {e.response.status_code} — {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
