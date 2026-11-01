"""setup_telemetry: off without config, Datadog defaults with DD_API_KEY, OTEL_* overrides win."""

import os

import pytest

from backend import telemetry

OTEL_VARS = [
    "DD_API_KEY",
    "DD_SITE",
    "DD_LLMOBS_ML_APP",
    "OTEL_SEMCONV_STABILITY_OPT_IN",
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
]


@pytest.fixture
def exporters(monkeypatch):
    """Clear tracing env and record exporter setups instead of starting a real one."""
    for name in OTEL_VARS:
        # setenv first so monkeypatch restores the original state, removing
        # whatever setup_telemetry writes with os.environ.setdefault.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    calls: list[str] = []

    class FakeTelemetry:
        def setup_otlp_exporter(self):
            calls.append("otlp")
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", FakeTelemetry)
    return calls


def test_disabled_without_config(exporters):
    assert telemetry.setup_telemetry() is False
    assert exporters == []


def test_datadog_defaults(monkeypatch, exporters):
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setenv("DD_SITE", "us5.datadoghq.com")

    assert telemetry.setup_telemetry() is True
    assert exporters == ["otlp"]
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "https://otlp.us5.datadoghq.com/v1/traces"
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == "dd-api-key=k,dd-otlp-source=llmobs"
    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "gen_ai_latest_experimental"
    assert os.environ["OTEL_SERVICE_NAME"] == telemetry.DEFAULT_ML_APP


def test_custom_endpoint_wins(monkeypatch, exporters):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://phoenix:6006/v1/traces")

    assert telemetry.setup_telemetry() is True
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://phoenix:6006/v1/traces"
    assert "OTEL_EXPORTER_OTLP_TRACES_HEADERS" not in os.environ
