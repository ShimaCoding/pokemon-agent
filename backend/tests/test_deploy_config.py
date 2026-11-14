"""Build/deploy plumbing for the Datadog `env` and `version` tags (#21)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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
