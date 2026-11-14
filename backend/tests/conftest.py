import pytest

from backend.routing import CooldownFallbackStrategy


@pytest.fixture(autouse=True)
def _reset_model_cooldowns():
    """The cooldown table is process-wide: never let one test's failures route another's."""
    CooldownFallbackStrategy.reset()
    yield
    CooldownFallbackStrategy.reset()
