"""
Thumbs feedback (👍/👎) on Dexter's replies → Datadog LLM Observability and/or Phoenix.

``done`` carries the agent span's ``trace_id``/``span_id``; ``POST /api/feedback``
only accepts spans this process issued, so it cannot be aimed at arbitrary spans,
and each reply gets one vote. The registry lives in memory: the app runs a single
uvicorn worker (Dockerfile ``--workers 1``); after a restart, older replies get 404.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

TTL_S = 24 * 3600  # same as the session snapshots
MAX_SPANS = 5000


@dataclass
class _Issued:
    trace_id: str
    conversation_id: str
    issued_at: float
    voted: bool = False


class FeedbackRegistry:
    """Spans issued in ``done``, oldest first. Thread-safe."""

    def __init__(self, ttl_s: float = TTL_S, max_size: int = MAX_SPANS, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._max_size = max_size
        self._clock = clock
        self._spans: OrderedDict[str, _Issued] = OrderedDict()
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        while self._spans:
            oldest = next(iter(self._spans.values()))
            if now - oldest.issued_at < self._ttl_s and len(self._spans) <= self._max_size:
                break
            self._spans.popitem(last=False)

    def register(self, span_id: str, trace_id: str, conversation_id: str) -> None:
        with self._lock:
            now = self._clock()
            self._spans[span_id] = _Issued(trace_id, conversation_id, now)
            self._spans.move_to_end(span_id)
            self._evict(now)

    def claim(self, span_id: str, trace_id: str) -> tuple[str, str | None]:
        """``("ok", conversation_id)`` once per span; then ``("voted", None)``; ``("unknown", None)`` otherwise."""
        with self._lock:
            self._evict(self._clock())
            issued = self._spans.get(span_id)
            if issued is None or issued.trace_id != trace_id:
                return "unknown", None
            if issued.voted:
                return "voted", None
            issued.voted = True
            return "ok", issued.conversation_id

    def __len__(self) -> int:
        return len(self._spans)


registry = FeedbackRegistry()
