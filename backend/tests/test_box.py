"""Tests for backend/box.py: the SQLite storage of trainer boxes."""

import sqlite3
import threading

import pytest

from backend import box

ASH = "a" * 36
MISTY = "b" * 36


def _entry(name: str, pokemon_id: int = 1) -> box.Entry:
    return box.Entry(
        name=name,
        pokemon_id=pokemon_id,
        types=["electric"],
        base_stats={"hp": 35, "speed": 90},
        defense={"weak_to": {"ground": 2}, "resists": {"flying": 0.5}, "immune_to": []},
    )


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    path = tmp_path / "data" / "box.db"
    monkeypatch.setattr(box, "DB_PATH", str(path))
    return path


def test_store_list_and_release():
    assert box.list_box(ASH) == {"count": 0, "capacity": box.MAX_POKEMON, "pokemon": []}

    result = box.store(ASH, [_entry("pikachu", 25), _entry("giratina-altered", 487)])
    assert result == {
        "stored": ["pikachu", "giratina-altered"],
        "already": [],
        "full": [],
        "count": 2,
        "capacity": box.MAX_POKEMON,
    }

    listed = box.list_box(ASH)
    assert listed["count"] == 2
    assert [p["name"] for p in listed["pokemon"]] == ["pikachu", "giratina-altered"]
    assert listed["pokemon"][0] == {
        "name": "pikachu",
        "id": 25,
        "types": ["electric"],
        "base_stats": {"hp": 35, "speed": 90},
        "defense": {"weak_to": {"ground": 2}, "resists": {"flying": 0.5}, "immune_to": []},
    }

    result = box.release(ASH, ["pikachu", "mew"])
    assert result == {"released": ["pikachu"], "not_in_box": ["mew"], "count": 1, "capacity": box.MAX_POKEMON}
    assert [p["name"] for p in box.list_box(ASH)["pokemon"]] == ["giratina-altered"]


def test_store_and_release_are_idempotent():
    box.store(ASH, [_entry("pikachu")])
    again = box.store(ASH, [_entry("pikachu"), _entry("pikachu")])
    assert (again["stored"], again["already"], again["count"]) == ([], ["pikachu"], 1)

    assert box.release(ASH, ["pikachu", "pikachu"])["released"] == ["pikachu"]
    gone = box.release(ASH, ["pikachu"])
    assert (gone["released"], gone["not_in_box"], gone["count"]) == ([], ["pikachu"], 0)


def test_entry_without_defense():
    box.store(ASH, [box.Entry(name="ditto", pokemon_id=132, types=["normal"], base_stats={"hp": 48})])
    assert box.list_box(ASH)["pokemon"][0]["defense"] is None


def test_a_full_box_takes_no_more(monkeypatch):
    monkeypatch.setattr(box, "MAX_POKEMON", 3)
    result = box.store(ASH, [_entry(f"mon-{i}") for i in range(5)])
    assert result["stored"] == ["mon-0", "mon-1", "mon-2"]
    assert result["full"] == ["mon-3", "mon-4"]
    assert (result["count"], result["capacity"]) == (3, 3)
    # One already there is reported as such, not as left out.
    result = box.store(ASH, [_entry("mon-0"), _entry("mon-9")])
    assert (result["already"], result["full"]) == (["mon-0"], ["mon-9"])
    # Room again after a release.
    box.release(ASH, ["mon-0"])
    assert box.store(ASH, [_entry("mon-9")])["stored"] == ["mon-9"]


def test_capacity_holds_under_concurrent_writers(monkeypatch):
    # The conversation lock is per conversation: two requests of one trainer write at once.
    monkeypatch.setattr(box, "MAX_POKEMON", 10)
    start = threading.Barrier(2)
    results: list[dict] = []

    def writer(prefix: str) -> None:
        start.wait()
        for i in range(10):
            results.append(box.store(ASH, [_entry(f"{prefix}-{i}")]))

    threads = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(len(r["stored"]) for r in results) == 10
    assert sum(len(r["full"]) for r in results) == 10
    assert box.list_box(ASH)["count"] == 10
    assert all(r["count"] <= 10 for r in results)


def test_a_trainer_never_sees_another_box():
    box.store(ASH, [_entry("pikachu")])
    box.store(MISTY, [_entry("staryu"), _entry("pikachu")])

    assert [p["name"] for p in box.list_box(ASH)["pokemon"]] == ["pikachu"]
    assert [p["name"] for p in box.list_box(MISTY)["pokemon"]] == ["staryu", "pikachu"]

    assert box.release(ASH, ["staryu"])["not_in_box"] == ["staryu"]
    assert box.list_box(MISTY)["count"] == 2
    box.release(ASH, ["pikachu"])
    assert [p["name"] for p in box.list_box(MISTY)["pokemon"]] == ["staryu", "pikachu"]


def test_no_result_carries_the_trainer_id():
    results = [
        box.store(ASH, [_entry("pikachu")]),
        box.list_box(ASH),
        box.release(ASH, ["pikachu", "mew"]),
    ]
    assert ASH not in repr(results)


def test_purge_removes_only_inactive_boxes():
    day = 86_400
    box.store(ASH, [_entry("pikachu")], now=0)
    box.store(MISTY, [_entry("staryu")], now=0)
    box.list_box(MISTY, now=80 * day)  # reading the box is activity

    assert box.purge_inactive(now=100 * day) == 1
    assert box.list_box(ASH, now=100 * day)["count"] == 0
    assert box.list_box(MISTY, now=100 * day)["count"] == 1
    assert box.purge_inactive(now=100 * day) == 0
    assert box.purge_inactive(days=10, now=120 * day) == 1


def test_occupancy_counts_without_counting_as_activity():
    day = 86_400
    assert box.occupancy(ASH) == (0, box.MAX_POKEMON)
    box.store(ASH, [_entry("pikachu"), _entry("eevee")], now=0)
    assert box.occupancy(ASH) == (2, box.MAX_POKEMON)
    assert box.occupancy(MISTY) == (0, box.MAX_POKEMON)
    # Unlike list_box, it does not keep an idle box alive.
    assert box.purge_inactive(now=100 * day) == 1


def test_listing_an_unknown_trainer_creates_no_row(db):
    box.list_box(ASH)
    box.release(ASH, ["pikachu"])
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM box_trainers").fetchone()[0] == 0


def test_purge_without_a_database_is_a_noop(db):
    assert box.purge_inactive() == 0
    assert not db.exists()


def test_purge_periodically_purges_and_survives_errors(monkeypatch):
    import asyncio

    calls = []

    def purge():
        calls.append(len(calls))
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return 0

    monkeypatch.setattr(box, "purge_inactive", purge)

    async def run():
        task = asyncio.create_task(box.purge_periodically(interval_seconds=0))
        while len(calls) < 3:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) >= 3  # the first failure did not stop the loop


def test_invalid_env_values_fall_back(monkeypatch):
    monkeypatch.setenv("BOX_MAX_POKEMON", "muchos")
    assert box._env_int("BOX_MAX_POKEMON", 30) == 30
    monkeypatch.setenv("BOX_MAX_POKEMON", "0")
    assert box._env_int("BOX_MAX_POKEMON", 30) == 30
    monkeypatch.setenv("BOX_MAX_POKEMON", "12")
    assert box._env_int("BOX_MAX_POKEMON", 30) == 12
