"""observability/datadog/monitors.json: the Datadog monitors for issue #22, pinned to the code.

The file is the definition (created with the Datadog MCP / API); Datadog holds the state.
These checks keep it in sync with what telemetry.py emits and with the issue's thresholds.
"""

import json
import re
from pathlib import Path

import pytest

from backend import telemetry

MONITORS_PATH = Path(__file__).resolve().parents[2] / "observability" / "datadog" / "monitors.json"
DASHBOARD_ID = "795-e3v-q9e"
SCOPE = f"@ml_app:{telemetry.DEFAULT_ML_APP.lower()} env:prod @meta.span.kind:agent"

# "p95 > X" is "more than 5% of requests are > X"; clamp_min(all, 20) keeps one bad request
# out of a low-traffic hour from paging (1/20 = 5%, not > 5).
QUERY_RE = re.compile(r'formula\("(\w+) / clamp_min\(all, (\d+)\) \* 100"\)\.last\("1h"\) > 5')

# Issue #22 thresholds: agent latency p95 > 5 s, error % > 5%, tokens per query p95 > 15k.
BAD_FILTERS = {
    "@duration:>5000000000",  # nanoseconds
    "@status:error",
    "@metrics.total_tokens:>15000",
}


def _monitors() -> list[dict]:
    return json.loads(MONITORS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(params=range(3), ids=["latency", "errors", "tokens"])
def monitor(request) -> dict:
    return _monitors()[request.param]


def test_three_unique_llm_obs_monitors():
    monitors = _monitors()
    assert len(monitors) == 3
    assert len({m["name"] for m in monitors}) == 3
    assert {m["type"] for m in monitors} == {"llm-observability alert"}


def test_issue_thresholds_are_covered():
    bad = {m["options"]["variables"][0]["search"]["query"].removeprefix(SCOPE).strip() for m in _monitors()}
    assert bad == BAD_FILTERS


def test_query_is_share_of_bad_requests(monitor):
    match = QUERY_RE.fullmatch(monitor["query"])
    assert match, monitor["query"]
    bad_name, floor = match.groups()
    assert int(floor) == 20
    assert monitor["options"]["thresholds"]["critical"] == 5

    variables = {v["name"]: v for v in monitor["options"]["variables"]}
    assert set(variables) == {bad_name, "all"}
    assert variables["all"]["search"]["query"] == SCOPE
    assert variables[bad_name]["search"]["query"].startswith(SCOPE + " ")
    for v in variables.values():
        assert v["data_source"] == "llm_observability"
        assert v["compute"] == {"aggregation": "count"}
        assert v["group_by"] == []


def test_low_traffic_policy(monitor):
    # Zero traffic is normal for this site: it must neither page nor stick in No Data.
    options = monitor["options"]
    assert options["notify_no_data"] is False
    assert options["on_missing_data"] == "resolve"
    assert options["evaluation_delay"] >= 180  # LLM Obs ingestion lag is about 3 min


def test_message_has_links_and_no_handles(monitor):
    message = monitor["message"]
    assert DASHBOARD_ID in message
    assert "{{#is_alert}}" in message and "{{#is_recovery}}" in message
    # Recipients are added when the monitors are created, never committed (URL-encoded %40 is fine).
    assert re.search(r"(?<![\w%])@[\w.-]+", message) is None


def test_tags(monitor):
    assert {"env:prod", "epic:15", "managed_by:repo", f"ml_app:{telemetry.DEFAULT_ML_APP.lower()}"} <= set(
        monitor["tags"]
    )
