"""
Routing strategy for the Strands ``ModelRouter``.

``CooldownFallbackStrategy`` wraps the SDK's ``FallbackStrategy`` (ordered failover inside one
invocation) and adds process-wide memory: a candidate that just failed with a provider-health
error (rate limit, outage, bad key, timeout) is skipped by *later* requests for
``cooldown_seconds``. Without it, every request would rediscover that Groq is throttled by
waiting on it first.

``FallbackStrategy`` only sees the attempts of the current invocation, and the router is rebuilt
per request (``providers.build_model``), so the cooldown table lives at module level and is keyed
by candidate name (the LiteLLM model id), which is stable across rebuilds. It is per process.

Malformed tool calls (``tool_use_failed``) are request errors: ``RetryMalformedToolCalls``
retries them on the same model and they never cool a model down.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from typing import Any

import litellm.exceptions as _llm_exc
from strands.models.routing import FallbackStrategy, RoutingCandidate, RoutingContext
from strands.types.exceptions import ModelThrottledException

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = float(os.environ.get("MODEL_COOLDOWN_SECONDS", "60"))

# Errors that say "this provider/model is unhealthy right now", as opposed to errors caused by
# the request itself (bad request, context window), which would fail on every candidate and
# must not poison the cooldown table.
_HEALTH_ERRORS: tuple[type[BaseException], ...] = (
    ModelThrottledException,
    _llm_exc.RateLimitError,
    _llm_exc.ServiceUnavailableError,
    _llm_exc.InternalServerError,
    _llm_exc.BadGatewayError,
    _llm_exc.APIConnectionError,
    _llm_exc.Timeout,
    _llm_exc.AuthenticationError,
    _llm_exc.PermissionDeniedError,
    _llm_exc.NotFoundError,
)

# Some providers (Groq) report a bad API key as a 400 instead of a 401.
_BAD_KEY_MARKERS = ("invalid api key", "invalid_api_key", "api key not valid")


def is_health_error(exc: BaseException | None) -> bool:
    """Whether ``exc`` (or anything in its cause chain) marks the model as unhealthy."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, _HEALTH_ERRORS):
            return True
        if isinstance(exc, _llm_exc.BadRequestError) and any(m in str(exc).lower() for m in _BAD_KEY_MARKERS):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


class CooldownFallbackStrategy:
    """Ordered failover that also skips candidates cooled down by earlier requests."""

    _lock = threading.Lock()
    _cooldowns: dict[str, float] = {}  # candidate name -> monotonic deadline

    def __init__(self, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._inner = FallbackStrategy()

    # ---- process-wide cooldown table ----

    @classmethod
    def cooling_down(cls, name: str | None) -> bool:
        if not name:
            return False
        with cls._lock:
            deadline = cls._cooldowns.get(name)
            if deadline is None:
                return False
            if deadline <= time.monotonic():
                del cls._cooldowns[name]
                return False
            return True

    @classmethod
    def reset(cls) -> None:
        """Forget every cooldown (tests, admin)."""
        with cls._lock:
            cls._cooldowns.clear()

    def _record(self, context: RoutingContext) -> None:
        """Update the table from this invocation's attempts.

        The router only asks again after a failure, so the newest attempt is the failure that
        triggered this ask (recorded once, here). Earlier successes in the same invocation (e.g.
        previous cycles of a tool loop) prove those models healthy again. A success with no later
        failure is never reported to a strategy, so cooldowns otherwise expire by time.
        """
        if not context.attempts:
            return
        *earlier, latest = context.attempts
        with self._lock:
            for attempt in earlier:
                if attempt.exception is None and attempt.candidate.name:
                    self._cooldowns.pop(attempt.candidate.name, None)
            name = latest.candidate.name
            if name and latest.exception is not None and is_health_error(latest.exception):
                self._cooldowns[name] = time.monotonic() + self._cooldown_seconds
                logger.warning(
                    "[ROUTER] %s en cooldown %.0fs (%s)",
                    name,
                    self._cooldown_seconds,
                    type(latest.exception).__name__,
                )

    # ---- RoutingStrategy protocol ----

    async def select(self, context: RoutingContext, **kwargs: Any) -> RoutingCandidate | None:
        self._record(context)

        healthy = tuple(c for c in context.candidates if not self.cooling_down(c.name))
        if healthy and len(healthy) < len(context.candidates):
            pick = await self._inner.select(replace(context, candidates=healthy))
            if pick is not None:
                return pick
            # Every healthy candidate was already tried: fall through and let the cooled-down
            # ones have a go rather than failing the request.

        return await self._inner.select(context)
