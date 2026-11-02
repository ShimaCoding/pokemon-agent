"""observability/grafana/: the Phoenix-backed Grafana alerts and dashboard, pinned to the code.

Grafana loads these files from /root/monitoring/grafana/provisioning on the VPS. The alert
rules mirror observability/datadog/monitors.json (see test_monitors.py); these checks keep
them in sync with what telemetry_phoenix.py emits and with the issue #22 thresholds.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from backend import telemetry

GRAFANA = Path(__file__).resolve().parents[2] / "observability" / "grafana"
DATASOURCE_UID = "phoenix-pg"
PROD_PROJECT = f"{telemetry.DEFAULT_PHOENIX_PROJECT}-prod"

# Issue #22 thresholds, as SQL over the agent root spans: latency > 5 s, error, tokens > 15k.
BAD_FILTERS = {
    "mcpokedex-latency": "s.end_time - s.start_time > interval '5 seconds'",
    "mcpokedex-errors": "s.status_code = 'ERROR'",
    "mcpokedex-tokens": "s.cumulative_llm_token_count_prompt + s.cumulative_llm_token_count_completion > 15000",
}


def _rules() -> list[dict]:
    doc = yaml.safe_load((GRAFANA / "alert-rules-mcpokedex.yml").read_text(encoding="utf-8"))
    [group] = doc["groups"]
    return group["rules"]


@pytest.fixture(params=sorted(BAD_FILTERS))
def rule(request) -> dict:
    return next(r for r in _rules() if r["uid"] == request.param)


def test_three_rules_one_per_threshold():
    assert [r["uid"] for r in _rules()] == list(BAD_FILTERS)


def test_query_is_share_of_bad_agent_requests_in_the_last_hour(rule):
    query, threshold = rule["data"]
    assert query["datasourceUid"] == DATASOURCE_UID
    sql = " ".join(query["model"]["rawSql"].split())
    # Same as Datadog's clamp_min(all, 20): one bad request in a quiet hour is 5%, not > 5.
    bad = re.escape(BAD_FILTERS[rule["uid"]])
    assert re.match(rf"SELECT 100\.0 \* count\(\*\) FILTER \(WHERE {bad}\) / greatest\(count\(\*\), 20\) AS value ", sql)
    assert f"p.name = '{PROD_PROJECT}'" in sql
    # Root agent spans only (setup failures included); llm/tool children are not requests.
    assert "s.parent_id IS NULL AND s.span_kind = 'AGENT'" in sql
    assert "s.start_time > now() - interval '1 hour'" in sql

    [condition] = threshold["model"]["conditions"]
    assert threshold["model"]["type"] == "threshold"
    assert condition["evaluator"] == {"params": [5], "type": "gt"}
    assert rule["condition"] == "C"


def test_low_traffic_policy(rule):
    # Zero traffic is normal for this site: it must not page.
    assert rule["noDataState"] == "OK"
    assert rule["labels"] == {"app": "mcpokedex", "env": "prod", "managed_by": "repo"}
    assert "grafana.shimadev.xyz/d/mcpokedex-phoenix" in rule["annotations"]["dashboard"]


def test_dashboard_uses_the_phoenix_datasource():
    dash = json.loads((GRAFANA / "dashboards" / "mcpokedex-phoenix.json").read_text(encoding="utf-8"))
    assert dash["uid"] == "mcpokedex-phoenix"
    queries = [t for p in dash["panels"] for t in p.get("targets", [])]
    assert queries and {t["datasource"]["uid"] for t in queries} == {DATASOURCE_UID}
    assert all("$project" in t["rawSql"] and "$__timeFilter" in t["rawSql"] for t in queries)


def test_datasource_is_read_only_and_keeps_the_password_out():
    [ds] = yaml.safe_load((GRAFANA / "datasource-phoenix.yml").read_text(encoding="utf-8"))["datasources"]
    assert (ds["uid"], ds["user"]) == (DATASOURCE_UID, "grafana_ro")
    assert ds["secureJsonData"]["password"] == "${PHOENIX_DB_RO_PASSWORD}"
    grants = (GRAFANA / "grafana-ro.sql").read_text(encoding="utf-8")
    assert "default_transaction_read_only = on" in grants
    assert "ALL TABLES" not in grants  # the database also holds users, API keys and secrets
