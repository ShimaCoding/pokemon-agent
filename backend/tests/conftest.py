import pytest

from backend.routing import CooldownFallbackStrategy


@pytest.fixture(autouse=True)
def _reset_model_cooldowns():
    """The cooldown table is process-wide: never let one test's failures route another's."""
    CooldownFallbackStrategy.reset()
    yield
    CooldownFallbackStrategy.reset()


@pytest.fixture(autouse=True)
def _box_in_a_temp_file(monkeypatch, tmp_path):
    """Trainer boxes never touch the developer's data/queries.db: the app's hourly purge opens it."""
    from backend import box

    monkeypatch.setattr(box, "DB_PATH", str(tmp_path / "box-default.db"))
