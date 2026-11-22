"""Build/deploy plumbing: the Datadog `env` and `version` tags (#21) and the CI workflow (#86)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _lines(name: str) -> list[str]:
    return [line.strip() for line in (ROOT / name).read_text().splitlines()]


def test_compose_pins_prod_env_and_never_a_static_version():
    yaml = pytest.importorskip("yaml")
    text = (ROOT / "docker-compose.yml").read_text()
    service = yaml.safe_load(text)["services"]["pokemon-agent"]
    assert service["environment"]["DD_ENV"] == "${DD_ENV:-prod}"
    assert "DD_VERSION:" not in text and "DD_VERSION=" not in text


def test_dockerignore_reincludes_only_git_refs():
    lines = _lines(".dockerignore")
    assert ".git" in lines
    git_exceptions = {line for line in lines if line.startswith("!.git")}
    # Never .git/config (a clone URL can embed credentials) nor the whole directory.
    assert git_exceptions == {"!.git/HEAD", "!.git/packed-refs", "!.git/shallow", "!.git/refs/heads"}
    assert lines.index(".git") < min(lines.index(line) for line in git_exceptions)


def test_dockerfile_copies_optional_git_slice_into_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "COPY --chown=appuser:appgroup .gi[t] ./.git/" in runtime


def test_ci_uses_the_toolchain_of_the_image():
    """CI must test what the image runs: same uv, same Python, same Node major."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    ci = CI.read_text()
    uv = re.search(r"ghcr\.io/astral-sh/uv:([\d.]+)", dockerfile).group(1)
    python = re.search(r"FROM python:(\d+\.\d+)", dockerfile).group(1)
    node = re.search(r"FROM node:(\d+)", dockerfile).group(1)
    assert re.findall(r'^\s+version: "([\d.]+)"$', ci, re.M) == [uv]
    assert re.findall(r'^\s+python-version: "([\d.]+)"$', ci, re.M) == [python]
    assert set(re.findall(r"^\s+node-version: (\d+)$", ci, re.M)) == {node}


def test_ci_has_no_secrets_no_live_evals_and_pinned_actions():
    ci = CI.read_text()
    # The evals call Groq (tokens, and the org blocks its key at a spend threshold).
    assert "secrets." not in ci and "backend.evals" not in ci and "GROQ_API_KEY" not in ci
    # Telemetry is off without these; CI must never export spans.
    assert "DD_API_KEY" not in ci and "PHOENIX_COLLECTOR_ENDPOINT" not in ci
    uses = re.findall(r"^\s+- uses: (\S+)", ci, re.M)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action) for action in uses)
    assert re.search(r"^permissions:\n  contents: read$", ci, re.M)
