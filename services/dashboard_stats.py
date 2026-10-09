"""Chart series shared by the two process dashboards."""
import json
from datetime import datetime, timedelta

from me2m_pipeline import REPORT_TIMEZONE


def run_metrics(run, kind, group="", codes=None):
    if run["status"] != "success":
        return 0, 0
    if kind == "me2m":
        if group:
            stats = json.loads(run.get("group_stats") or "{}").get(group, {})
            return stats.get("rows", 0), stats.get("net_order_value", 0)
        return run.get("total_rows") or 0, run.get("net_order_value") or 0
    per_code = json.loads(run.get("code_stats") or "{}")
    if codes is not None and per_code:
        selected = [stats for name, stats in per_code.items() if name in codes]
        return sum(s.get("rows", 0) for s in selected), sum(s.get("action_items", 0) for s in selected)
    return run.get("total_rows") or 0, run.get("action_items") or 0


def chart_data(runs, period, kind, group="", codes=None, today=None):
    today = today or datetime.now(REPORT_TIMEZONE).date()
    weekly = period == 0
    buckets = {}
    if not weekly:
        for i in range(period - 1, -1, -1):
            day = today - timedelta(days=i)
            buckets[day.isoformat()] = {"label": day.strftime("%d %b"), "runs": 0, "rows": 0, "value": 0}
    outcomes = {"success": 0, "failed": 0, "running": 0, "stopped": 0}
    for run in runs:
        day = datetime.fromisoformat(run["started_at"]).date()
        key = day.strftime("%G-W%V") if weekly else day.isoformat()
        label = "Week " + day.strftime("%V, %Y") if weekly else day.strftime("%d %b")
        bucket = buckets.setdefault(key, {"label": label, "runs": 0, "rows": 0, "value": 0})
        rows, value = run_metrics(run, kind, group, codes)
        bucket["runs"] += 1
        bucket["rows"] += rows
        bucket["value"] += value
        status = run["status"] if run["status"] in outcomes else "stopped"
        outcomes[status] += 1
    series = [buckets[key] for key in sorted(buckets)]
    return {"series": series, "outcomes": outcomes,
            "metric_label": "Net order value" if kind == "me2m" else "Action items",
            "bucket_label": "per week" if weekly else "per day", "kind": kind}
