"""
Conversation sessions: id validation, per-conversation locks and TTL cleanup.

The harness snapshots each conversation under ``SESSIONS_DIR/<...>/<id>``. The
id comes from the browser (localStorage), so it is validated strictly before
it reaches the filesystem, and two requests on the same conversation are
serialized so they don't overwrite each other's snapshot.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

SESSIONS_DIR = os.environ.get("SESSIONS_DIR", "data/sessions")
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(24 * 3600)))

CONVERSATION_ID_RE = re.compile(r"^[a-z0-9-]{8,64}$")

_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def is_valid_conversation_id(value: str) -> bool:
    return bool(CONVERSATION_ID_RE.fullmatch(value))


@asynccontextmanager
async def conversation_lock(conversation_id: str):
    lock = _locks[conversation_id]
    try:
        async with lock:
            yield
    finally:
        # Drop idle locks so the dict doesn't grow with every conversation ever seen.
        if not lock.locked() and not getattr(lock, "_waiters", None):
            _locks.pop(conversation_id, None)


def _session_dirs(root: Path) -> list[Path]:
    """One directory per conversation and subsystem.

    The harness storage namespaces each subsystem: ``session/<id>/…`` holds the
    message snapshot and ``context/<id>/…`` the context manager's offloaded data.
    """
    return [
        p
        for subsystem in ("session", "context")
        if (root / subsystem).is_dir()
        for p in (root / subsystem).iterdir()
        if p.is_dir() and is_valid_conversation_id(p.name)
    ]


def prune_sessions(max_age_seconds: int = SESSION_TTL_SECONDS, root: str = SESSIONS_DIR) -> int:
    """Delete conversations not touched in ``max_age_seconds``. Returns how many."""
    base = Path(root)
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for session_dir in _session_dirs(base):
        newest = max(
            (f.stat().st_mtime for f in session_dir.rglob("*") if f.is_file()),
            default=session_dir.stat().st_mtime,
        )
        if newest < cutoff:
            shutil.rmtree(session_dir, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("[SESSIONS] %d conversaciones expiradas eliminadas", removed)
    return removed


async def prune_periodically(interval_seconds: int = 3600) -> None:
    while True:
        try:
            await asyncio.to_thread(prune_sessions)
        except Exception:
            logger.exception("[SESSIONS] Error limpiando sesiones")
        await asyncio.sleep(interval_seconds)
