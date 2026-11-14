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

``ClassifiedCooldownStrategy`` (#61, behind ``MODEL_CLASSIFIER_ENABLED``) picks the model that
opens a turn with a small classifier call (``ClassifierStrategy``) and keeps the cooldown failover
above for everything after it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import litellm.exceptions as _llm_exc
from strands.models import Model
from strands.models.routing import ClassifierStrategy, FallbackStrategy, RoutingCandidate, RoutingContext
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
    def cool_down(cls, name: str, seconds: float) -> None:
        """Skip ``name`` for ``seconds`` (also used as the classifier's circuit breaker)."""
        with cls._lock:
            cls._cooldowns[name] = time.monotonic() + seconds

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


# ---------------------------------------------------------------------------
# Classifier routing (#61)
# ---------------------------------------------------------------------------

# Routing policy for the classifier, sent verbatim by ClassifierStrategy, which appends its own
# isolation and output rules. Self-contained on purpose: the agent's system prompt is not sent
# (``system_prompt=None``), which saves ~1.1k tokens per turn. Candidate descriptions say which
# one is "grande" and which "chico".
CLASSIFIER_POLICY = """Eres el enrutador de Dexter, una Pokédex conversacional. Elige el modelo que abre el turno.
- Candidato "grande": el mensaje nombra o alude a un Pokémon, un tipo, un movimiento o un equipo, o pide una entrada, evolución, estadísticas, debilidades, comparación, análisis de equipo, búsqueda o cualquier dato que haya que consultar; también los seguimientos cortos que dependen de la conversación ("¿y su evolución?", "¿y el otro?").
- Candidato "chico": saludos, agradecimientos, despedidas, preguntas sobre quién es Dexter o qué puede hacer, temas ajenos a Pokémon y pedidos que debe rechazar.
- Ante la duda, elige "grande"."""

CANDIDATE_DESCRIPTIONS = {
    "big": "grande: consultas sobre Pokémon, tipos, equipos y cualquier dato que haya que consultar y narrar",
    "small": "chico: saludos, agradecimientos, preguntas sobre Dexter, temas ajenos a Pokémon y pedidos a rechazar",
}

RouteSink = Callable[[dict[str, Any]], None]


class _RecordingClassifierStrategy(ClassifierStrategy):
    """``ClassifierStrategy`` that remembers why it declined (it only logs it)."""

    failure: tuple[str, Exception] | None = None

    def _warn(self, reason: str, error: Exception) -> None:
        self.failure = (reason, error)
        super()._warn(reason, error)


class ClassifiedCooldownStrategy:
    """Classifier picks the opening model; ``CooldownFallbackStrategy`` handles the rest.

    - After a failure (``attempts`` non-empty) it is plain cooldown failover: the classifier never
      decides failover, so a 20b that fails hands the turn to the 120b and vice versa.
    - Opening ask with any candidate cooling down, or with the classifier's breaker open: no
      classifier call (there is no real choice, and a throttled classifier would fail anyway).
    - Opening ask otherwise: classify the latest user text. If the classifier fails, the opening
      goes to the first healthy candidate (the 120b); a health error or a timeout also opens the
      breaker (``classifier:<model_id>`` in the cooldown table) so the next requests do not pay
      the timeout again. A request error (e.g. Groq's ``json_validate_failed``) does not.

    Every opening reports one ``model_route`` event through ``on_route`` and keeps it in
    ``self.route``. One instance per request (``providers.build_model``).
    """

    def __init__(
        self,
        classifier: Model,
        *,
        timeout: float,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        on_route: RouteSink | None = None,
        policy: str = CLASSIFIER_POLICY,
    ) -> None:
        self._fallback = CooldownFallbackStrategy(cooldown_seconds)
        self._cooldown_seconds = cooldown_seconds
        self._classifier_model = classifier
        self._classifier = _RecordingClassifierStrategy(classifier, system_prompt=policy, timeout=timeout)
        self._classifier_id = str(classifier.get_config().get("model_id", type(classifier).__name__))
        self._on_route = on_route
        self.route: dict[str, Any] | None = None

    @property
    def breaker_key(self) -> str:
        return f"classifier:{self._classifier_id}"

    async def select(self, context: RoutingContext, **kwargs: Any) -> RoutingCandidate | None:
        if context.attempts:
            return await self._fallback.select(context)

        started = time.monotonic()
        error: str | None = None
        classified = False
        if CooldownFallbackStrategy.cooling_down(self.breaker_key) or any(
            CooldownFallbackStrategy.cooling_down(c.name) for c in context.candidates
        ):
            pick, source = await self._fallback.select(context), "cooldown"
        else:
            classified = True
            self._classifier.failure = None
            # Without the agent's system prompt: the policy is self-contained.
            pick = await self._classifier.select(replace(context, system_prompt=None))
            source = "classifier"
            if pick is None:
                source = "fallback"
                if self._classifier.failure is not None:
                    reason, exc = self._classifier.failure
                    error = f"{reason}: {type(exc).__name__}"
                    if reason == "classifier_timeout" or is_health_error(exc):
                        CooldownFallbackStrategy.cool_down(self.breaker_key, self._cooldown_seconds)
                        logger.warning("[ROUTER] clasificador en pausa %.0fs (%s)", self._cooldown_seconds, error)
                pick = await self._fallback.select(context)

        self._report(pick, source, classified, int((time.monotonic() - started) * 1000), error)
        return pick

    def _report(self, pick: RoutingCandidate | None, source: str, classified: bool, ms: int, error: str | None) -> None:
        route: dict[str, Any] = {
            "type": "model_route",
            "model": pick.name if pick is not None else None,
            "source": source,
            "classifier_model": self._classifier_id,
            "classify_ms": ms if classified else 0,
        }
        last = getattr(self._classifier_model, "last", None) if classified else None
        if isinstance(last, dict):
            if last.get("reasoning"):
                route["reason"] = last["reasoning"]
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                if key in last:
                    route[key] = last[key]
        if error:
            route["error"] = error
        self.route = route
        logger.info("[ROUTER] %s → %s (%s, %d ms)", source, route["model"], self._classifier_id, route["classify_ms"])
        if self._on_route is not None:
            try:
                self._on_route(route)
            except Exception:  # noqa: BLE001 - tracing never breaks a request
                logger.exception("[ROUTER] on_route falló")
