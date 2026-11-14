"""Tests for backend/sessions.py: conversation id validation and TTL cleanup."""

import asyncio
import os
import time

import pytest

from backend.sessions import conversation_lock, is_valid_conversation_id, prune_sessions


@pytest.mark.parametrize("value", ["3f2b9c1e-8a4d-4c2b-9e1f-0a1b2c3d4e5f", "abcdefgh", "a" * 64])
def test_valid_conversation_ids(value):
    assert is_valid_conversation_id(value)


@pytest.mark.parametrize(
    "value",
    ["", "short", "a" * 65, "../../etc/passwd", "UPPERCASE-ID", "with space", "with_underscore", "abc/defgh"],
)
def test_invalid_conversation_ids(value):
    assert not is_valid_conversation_id(value)


def _make_session(root, conversation_id: str, age_seconds: float) -> None:
    stamp = time.time() - age_seconds
    for subsystem in ("session", "context"):
        leaf = root / subsystem / conversation_id / "scopes" / "agent" / "default"
        leaf.mkdir(parents=True)
        f = leaf / "snapshot_latest.json"
        f.write_text("{}")
        os.utime(f, (stamp, stamp))


def test_prune_removes_only_expired_conversations(tmp_path):
    _make_session(tmp_path, "old-conversation", age_seconds=2 * 3600)
    _make_session(tmp_path, "new-conversation", age_seconds=60)

    removed = prune_sessions(max_age_seconds=3600, root=str(tmp_path))

    assert removed == 2  # session/ + context/ of the old conversation
    for subsystem in ("session", "context"):
        assert not (tmp_path / subsystem / "old-conversation").exists()
        assert (tmp_path / subsystem / "new-conversation").exists()


def test_prune_missing_dir_is_noop(tmp_path):
    assert prune_sessions(root=str(tmp_path / "nope")) == 0


def test_conversation_lock_serializes_same_conversation():
    order: list[str] = []

    async def run(tag: str, conversation_id: str):
        async with conversation_lock(conversation_id):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-out")

    async def main():
        await asyncio.gather(run("a", "same-conversation"), run("b", "same-conversation"))

    asyncio.run(main())
    assert order == ["a-in", "a-out", "b-in", "b-out"]
