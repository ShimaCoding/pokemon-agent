"""CooldownFallbackStrategy: failover within a request, cooldown across requests.

Runs a real Strands Agent on fake models, so no API keys or network are needed.
"""

import asyncio

import httpx
import litellm
import pytest
from strands import Agent
from strands.models import Model, ModelRouter
from strands.models.routing import RoutingCandidate

from backend.providers import build_model
from backend.routing import (
    CANDIDATE_DESCRIPTIONS,
    CLASSIFIER_POLICY,
    ClassifiedCooldownStrategy,
    CooldownFallbackStrategy,
    is_health_error,
)


def _rate_limit(model_id: str) -> Exception:
    return litellm.RateLimitError(
        message="rate limited",
        llm_provider="groq",
        model=model_id,
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
    )


def _bad_request(message: str) -> Exception:
    return litellm.BadRequestError(message=message, model="groq/x", llm_provider="groq")


class FakeModel(Model):
    """Answers with fixed text, or raises ``error`` on every call."""

    def __init__(self, model_id: str, error: Exception | None = None) -> None:
        self.config = {"model_id": model_id}
        self.error = error
        self.calls = 0

    def update_config(self, **model_config):
        self.config.update(model_config)

    def get_config(self):
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": f"hola desde {self.config['model_id']}"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }


def _run(*models: FakeModel) -> str:
    """One request: a fresh router over ``models`` (as build_model does), one agent call."""
    router = ModelRouter(
        [RoutingCandidate(model=m, name=m.config["model_id"]) for m in models],
        strategy=CooldownFallbackStrategy(cooldown_seconds=60),
    )
    agent = Agent(model=router, callback_handler=None)
    return str(asyncio.run(agent.invoke_async("¿Quién es Pikachu?")))


def test_build_model_uses_cooldown_strategy_with_named_candidates(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_CLASSIFIER_ENABLED", raising=False)
    router = build_model(authenticated=True)
    assert isinstance(router._strategy, CooldownFallbackStrategy)
    assert [c.name for c in router.candidates] == [c.model.get_config()["model_id"] for c in router.candidates]


def test_rate_limited_model_fails_over_and_is_skipped_next_request():
    big = FakeModel("groq/openai/gpt-oss-120b", error=_rate_limit("120b"))
    small = FakeModel("groq/openai/gpt-oss-20b")

    assert "gpt-oss-20b" in _run(big, small)
    assert (big.calls, small.calls) == (1, 1)
    assert CooldownFallbackStrategy.cooling_down("groq/openai/gpt-oss-120b")

    # Next request goes straight to the healthy model: no wasted call on the 120b.
    assert "gpt-oss-20b" in _run(big, small)
    assert (big.calls, small.calls) == (1, 2)


def test_cooled_down_model_is_still_a_last_resort():
    big = FakeModel("groq/openai/gpt-oss-120b")
    small = FakeModel("groq/openai/gpt-oss-20b", error=_rate_limit("20b"))
    CooldownFallbackStrategy._cooldowns["groq/openai/gpt-oss-120b"] = float("inf")

    # The 120b is cooling down, so the 20b goes first; when it fails, the 120b still answers.
    assert "gpt-oss-120b" in _run(big, small)
    assert (big.calls, small.calls) == (1, 1)


def test_cooldown_expires(monkeypatch):
    import backend.routing as routing

    now = [1000.0]
    monkeypatch.setattr(routing.time, "monotonic", lambda: now[0])
    CooldownFallbackStrategy._cooldowns["m"] = now[0] + 60
    assert CooldownFallbackStrategy.cooling_down("m")
    now[0] += 61
    assert not CooldownFallbackStrategy.cooling_down("m")


def test_health_errors():
    assert is_health_error(_rate_limit("x"))
    # Groq reports a bad key as a 400.
    assert is_health_error(_bad_request('{"error":{"code":"invalid_api_key"}}'))
    # Request errors would fail on every model: they must not cool one down.
    assert not is_health_error(_bad_request("tool_use_failed: Tool call validation failed"))
    assert not is_health_error(ValueError("boom"))


def test_malformed_tool_call_does_not_cool_down():
    big = FakeModel("groq/openai/gpt-oss-120b", error=_bad_request("tool_use_failed"))
    small = FakeModel("groq/openai/gpt-oss-20b")

    assert "gpt-oss-20b" in _run(big, small)
    assert not CooldownFallbackStrategy.cooling_down("groq/openai/gpt-oss-120b")


# --- ClassifiedCooldownStrategy (#61) -------------------------------------------------

BIG, SMALL = "groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"


class FakeClassifier(Model):
    """Classifier model: picks ``index``, raises ``error``, or sleeps ``delay`` seconds first."""

    def __init__(self, index: int = 1, error: Exception | None = None, delay: float = 0.0) -> None:
        self.config = {"model_id": "fake/classifier"}
        self.index, self.error, self.delay = index, error, delay
        self.calls: list[dict] = []
        self.last: dict = {}

    def update_config(self, **model_config):
        self.config.update(model_config)

    def get_config(self):
        return self.config

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.last = {"reasoning": "saludo: chico", "input_tokens": 500, "output_tokens": 40, "total_tokens": 540}
        yield {"output": output_model(selected_candidate_index=self.index)}


def _run_classified(big, small, classifier, *, timeout: float = 2.0, system_prompt: str | None = None, query="hola"):
    """One request through ClassifiedCooldownStrategy; returns (reply, model_route events)."""
    routes: list[dict] = []
    strategy = ClassifiedCooldownStrategy(classifier, timeout=timeout, cooldown_seconds=60, on_route=routes.append)
    router = ModelRouter(
        [
            RoutingCandidate(model=big, name=BIG, description=CANDIDATE_DESCRIPTIONS["big"]),
            RoutingCandidate(model=small, name=SMALL, description=CANDIDATE_DESCRIPTIONS["small"]),
        ],
        strategy=strategy,
    )
    agent = Agent(model=router, callback_handler=None, system_prompt=system_prompt)
    reply = str(asyncio.run(agent.invoke_async(query)))
    assert strategy.route == (routes[-1] if routes else None)
    return reply, routes


def test_classifier_picks_the_small_model():
    big, small, clf = FakeModel(BIG), FakeModel(SMALL), FakeClassifier(index=1)
    reply, [route] = _run_classified(big, small, clf)
    assert "gpt-oss-20b" in reply
    assert (big.calls, small.calls, len(clf.calls)) == (0, 1, 1)
    assert route["source"] == "classifier"
    assert route["model"] == SMALL
    assert route["reason"] == "saludo: chico"
    assert route["total_tokens"] == 540


def test_classifier_picks_the_big_model():
    big, small = FakeModel(BIG), FakeModel(SMALL)
    reply, [route] = _run_classified(big, small, FakeClassifier(index=0), query="háblame de Gengar")
    assert "gpt-oss-120b" in reply
    assert (big.calls, small.calls) == (1, 0)
    assert (route["source"], route["model"]) == ("classifier", BIG)


def test_chosen_small_model_rate_limited_fails_over_to_big():
    big, small = FakeModel(BIG), FakeModel(SMALL, error=_rate_limit("20b"))
    clf = FakeClassifier(index=1)
    reply, routes = _run_classified(big, small, clf)
    assert "gpt-oss-120b" in reply
    assert (big.calls, small.calls) == (1, 1)
    assert len(routes) == 1  # failover is not a new routing decision
    assert CooldownFallbackStrategy.cooling_down(SMALL)

    # Next request: a model is cooling down, so no classifier call and the healthy one opens.
    reply, [route] = _run_classified(big, small, clf)
    assert "gpt-oss-120b" in reply
    assert len(clf.calls) == 1
    assert (route["source"], route["model"], route["classify_ms"]) == ("cooldown", BIG, 0)
    assert "reason" not in route


def test_classifier_health_error_opens_big_and_the_breaker():
    big, small = FakeModel(BIG), FakeModel(SMALL)
    clf = FakeClassifier(error=_rate_limit("classifier"))
    strategy_key = ClassifiedCooldownStrategy(clf, timeout=1).breaker_key

    reply, [route] = _run_classified(big, small, clf)
    assert "gpt-oss-120b" in reply
    assert route["source"] == "fallback"
    assert route["error"] == "classifier_error: RateLimitError"
    assert CooldownFallbackStrategy.cooling_down(strategy_key)

    # Breaker open: the next request does not call the classifier.
    _, [route] = _run_classified(big, small, clf)
    assert len(clf.calls) == 1
    assert route["source"] == "cooldown"


def test_classifier_request_error_falls_back_without_breaker():
    # Groq's json_validate_failed: the classifier's output, not its health.
    big, small = FakeModel(BIG), FakeModel(SMALL)
    clf = FakeClassifier(error=_bad_request("json_validate_failed"))
    reply, [route] = _run_classified(big, small, clf)
    assert "gpt-oss-120b" in reply
    assert route["source"] == "fallback"
    _, [route] = _run_classified(big, small, clf)
    assert len(clf.calls) == 2
    assert route["source"] == "fallback"


def test_classifier_timeout_is_bounded_and_opens_the_breaker():
    import time

    big, small = FakeModel(BIG), FakeModel(SMALL)
    clf = FakeClassifier(index=1, delay=5)
    started = time.monotonic()
    reply, [route] = _run_classified(big, small, clf, timeout=0.05)
    assert time.monotonic() - started < 2
    assert "gpt-oss-120b" in reply
    assert route["source"] == "fallback"
    assert route["error"].startswith("classifier_timeout")
    assert CooldownFallbackStrategy.cooling_down("classifier:fake/classifier")


def test_classifier_sees_the_user_text_and_not_the_agent_prompt():
    clf = FakeClassifier(index=0)
    _run_classified(FakeModel(BIG), FakeModel(SMALL), clf, system_prompt="SECRETO-DEL-AGENTE", query="¿y su evolución?")
    [call] = clf.calls
    assert call["prompt"][-1]["content"][0]["text"] == "¿y su evolución?"
    assert "SECRETO-DEL-AGENTE" not in call["system_prompt"]
    assert CLASSIFIER_POLICY in call["system_prompt"]
    assert CANDIDATE_DESCRIPTIONS["small"] in call["system_prompt"]


def test_build_model_with_classifier_flag(monkeypatch):
    from backend.providers import ClassifierLiteLLMModel

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_CLASSIFIER_ENABLED", "1")
    router = build_model(authenticated=False)
    assert isinstance(router._strategy, ClassifiedCooldownStrategy)
    assert isinstance(router._strategy._classifier_model, ClassifierLiteLLMModel)
    assert [c.description for c in router.candidates] == [CANDIDATE_DESCRIPTIONS["big"], CANDIDATE_DESCRIPTIONS["small"]]
    # Its own instance, not the 20b candidate's.
    assert all(c.model is not router._strategy._classifier_model for c in router.candidates)

    monkeypatch.setenv("MODEL_CLASSIFIER_ENABLED", "0")
    router = build_model(authenticated=False)
    assert type(router._strategy) is CooldownFallbackStrategy
    assert all(c.description is None for c in router.candidates)


def test_classifier_model_structured_output(monkeypatch):
    from types import SimpleNamespace

    from strands.models.routing.classifier_strategy import _ClassifierSelection

    from backend import providers

    sent: dict = {}

    async def fake_acompletion(**kwargs):
        sent.update(kwargs)
        message = SimpleNamespace(content='{"selected_candidate_index":1}', reasoning="Saludo. Chico.")
        usage = SimpleNamespace(prompt_tokens=560, completion_tokens=40, total_tokens=600)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    monkeypatch.setattr(providers.litellm, "acompletion", fake_acompletion)
    model = providers.ClassifierLiteLLMModel(model_id=SMALL, request_timeout=2.0)

    async def collect():
        return [
            e
            async for e in model.structured_output(
                _ClassifierSelection, [{"role": "user", "content": [{"text": "hola"}]}], system_prompt="política"
            )
        ]

    events = asyncio.run(collect())
    assert sent["reasoning_effort"] == "low"
    assert sent["response_format"] is _ClassifierSelection
    assert sent["max_tokens"] == providers.ClassifierLiteLLMModel.MAX_TOKENS
    assert sent["timeout"] == 2.0
    assert sent["messages"] == [{"role": "system", "content": "política"}, {"role": "user", "content": "hola"}]
    assert events[0]["output"].selected_candidate_index == 1
    assert events[1]["stop"][2] == {"inputTokens": 560, "outputTokens": 40, "totalTokens": 600}
    assert model.last == {"reasoning": "Saludo. Chico.", "input_tokens": 560, "output_tokens": 40, "total_tokens": 600}


def test_classifier_model_invalid_json_raises(monkeypatch):
    from types import SimpleNamespace

    from strands.models.routing.classifier_strategy import _ClassifierSelection

    from backend import providers

    async def fake_acompletion(**kwargs):
        message = SimpleNamespace(content="¡Hola!", reasoning="")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    monkeypatch.setattr(providers.litellm, "acompletion", fake_acompletion)
    model = providers.ClassifierLiteLLMModel(model_id=SMALL)

    async def collect():
        return [e async for e in model.structured_output(_ClassifierSelection, [{"role": "user", "content": [{"text": "x"}]}])]

    with pytest.raises(ValueError):
        asyncio.run(collect())
    assert model.last["error"] == "ValidationError"
