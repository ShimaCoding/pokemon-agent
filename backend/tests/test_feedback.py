"""👍/👎 feedback: in-memory registry of issued spans and POST /api/feedback."""

import pytest
from fastapi.testclient import TestClient

from backend import feedback, main, telemetry
from backend.feedback import FeedbackRegistry

TRACE = "ab" * 16
SPAN = "1234567890"


# --- registry --------------------------------------------------------------------------


def test_claim_once_per_span():
    reg = FeedbackRegistry()
    reg.register(SPAN, TRACE, "conv-12345678")
    assert reg.claim(SPAN, TRACE) == ("ok", "conv-12345678")
    assert reg.claim(SPAN, TRACE) == ("voted", None)


def test_unknown_span_or_mismatched_trace():
    reg = FeedbackRegistry()
    reg.register(SPAN, TRACE, "conv-12345678")
    assert reg.claim("999", TRACE) == ("unknown", None)
    assert reg.claim(SPAN, "cd" * 16) == ("unknown", None)
    assert reg.claim(SPAN, TRACE)[0] == "ok"  # a wrong guess doesn't burn the vote


def test_spans_expire_after_ttl():
    now = [1000.0]
    reg = FeedbackRegistry(ttl_s=60, clock=lambda: now[0])
    reg.register(SPAN, TRACE, "conv-12345678")
    now[0] += 61
    assert reg.claim(SPAN, TRACE) == ("unknown", None)
    assert len(reg) == 0


def test_oldest_span_evicted_at_max_size():
    reg = FeedbackRegistry(max_size=2)
    for span in ("1", "2", "3"):
        reg.register(span, TRACE, f"conv-{span}")
    assert len(reg) == 2
    assert reg.claim("1", TRACE) == ("unknown", None)
    assert reg.claim("3", TRACE) == ("ok", "conv-3")


# --- endpoint ----------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(main, "init_db", lambda: None)
    reg = FeedbackRegistry()
    monkeypatch.setattr(main, "feedback_registry", reg)
    sent = []
    monkeypatch.setattr(telemetry, "submit_feedback", lambda ids, **kw: sent.append((ids, kw)))
    main.limiter.reset()
    with TestClient(main.app) as c:
        c.registry, c.sent = reg, sent
        yield c


def _vote(client, **overrides):
    return client.post("/api/feedback", json={"trace_id": TRACE, "span_id": SPAN, "value": "down", **overrides})


@pytest.mark.parametrize(
    "bad",
    [{"trace_id": "nothex" * 5}, {"trace_id": "AB" * 16}, {"span_id": "12a"}, {"span_id": "1" * 21}, {"value": "meh"}],
)
def test_rejects_malformed_bodies(client, bad):
    assert _vote(client, **bad).status_code == 422
    assert client.sent == []


def test_unknown_reply_is_404(client):
    assert _vote(client).status_code == 404
    assert client.sent == []


def test_vote_is_forwarded_once(client):
    client.registry.register(SPAN, TRACE, "conv-12345678")
    res = _vote(client)
    assert res.status_code == 204
    assert res.content == b""
    assert client.sent == [({"span_id": SPAN, "trace_id": TRACE}, {"value": "down", "submitter_id": "conv-12345678"})]
    assert _vote(client, value="up").status_code == 409
    assert len(client.sent) == 1


def test_feedback_is_rate_limited(client):
    codes = [_vote(client).status_code for _ in range(11)]
    assert codes[:10] == [404] * 10
    assert codes[10] == 429


def test_module_registry_is_the_one_main_uses():
    assert main.feedback_registry is feedback.registry
