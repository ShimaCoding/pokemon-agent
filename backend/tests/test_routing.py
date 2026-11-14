"""CooldownFallbackStrategy: failover within a request, cooldown across requests.

Runs a real Strands Agent on fake models, so no API keys or network are needed.
"""

import asyncio

import httpx
import litellm
from strands import Agent
from strands.models import Model, ModelRouter
from strands.models.routing import RoutingCandidate

from backend.providers import build_model
from backend.routing import CooldownFallbackStrategy, is_health_error


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
