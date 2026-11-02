"""Offline eval harness (backend/evals/harness.py) and LLM judge parsing; no network."""

import argparse
import copy
import json
from types import SimpleNamespace

import pytest

from backend.agent import MCP_TOOLS
from backend.evals import harness, judge
from backend.evals.checks import POKEMON_DATA_TOOLS, SCAFFOLDING_TOOLS

BULBASAUR = json.dumps(
    {
        "id": 1,
        "name": "bulbasaur",
        "genus": "Pokémon Semilla",
        "height_dm": 7,
        "weight_hg": 69,
        "base_stats": {"hp": 45, "attack": 49, "defense": 49, "special-attack": 65, "special-defense": 65, "speed": 45},
    },
    ensure_ascii=False,
)
REPLY = "BULBASAUR. El Pokémon Semilla. Escaneo completado. Velocidad 45, total 318: insuficiente para tu intelecto."


def _events(reply: str = REPLY, tools=(("skills", {}, "[Skill activado: 885 chars]"), ("get_pokedex_entry", {"pokemon": "bulbasaur"}, BULBASAUR)), ids=True):
    events = [{"type": "start"}, {"type": "agent_init"}]
    for i, (name, args, result) in enumerate(tools):
        events.append({"type": "tool_call", "index": i, "tool_id": f"t{i}", "tool": name, "args": args})
        events.append({"type": "tool_result", "index": i, "tool_id": f"t{i}", "tool": name, "result": result})
    events += [{"type": "text", "delta": reply[:10]}, {"type": "text", "delta": reply[10:]}]
    done = {"type": "done", "elapsed_ms": 1200, "ttft_ms": 800, "llm_calls": 3, "input_tokens": 7000, "total_tokens": 7600}
    if ids:
        done.update(trace_id="a" * 32, span_id="123")
    return [*events, done]


# --- SSE parsing --------------------------------------------------------------------


def test_parse_sse_handles_split_frames():
    frames = "".join(f"data: {json.dumps(e)}\n\n" for e in _events())
    chunks = [frames[i : i + 7] for i in range(0, len(frames), 7)]
    assert [e["type"] for e in harness._parse_sse(chunks)] == [e["type"] for e in _events()]


def test_record_from_events():
    rec = harness.record_from_events("háblame de bulbasaur", _events(), {"ttft": 900, "elapsed": 1300})
    assert rec["status"] == "done"
    assert rec["reply"] == REPLY
    assert [t["name"] for t in rec["tools"]] == ["skills", "get_pokedex_entry"]
    assert rec["tools"][1]["args"] == {"pokemon": "bulbasaur"}
    assert rec["tools"][1]["result"] == BULBASAUR
    assert (rec["trace_id"], rec["span_id"]) == ("a" * 32, "123")
    assert (rec["llm_calls"], rec["elapsed_ms"], rec["client_ttft_ms"]) == (3, 1200, 900)


def test_record_marks_mcp_errors_and_missing_done():
    events = _events(tools=(("get_pokedex_entry", {"pokemon": "x"}, "❌ Pokémon 'x' not found."),))[:-1]
    rec = harness.record_from_events("háblame de x", events, {})
    assert rec["tools"][0]["error"] is True
    assert rec["status"] == "error"


# --- grading --------------------------------------------------------------------------


def _record(**kw):
    return harness.record_from_events(kw.pop("query", "háblame de bulbasaur"), _events(**kw), {})


def test_grade_required_tools_ignore_skills():
    m = harness.grade_turn({"q": "", "required_tools": ["get_pokedex_entry"]}, _record(), [])
    assert m["tool_expectation"]["passed"]
    assert m["completed"]["passed"]
    # Without `skills` (#16) the same expectation still holds.
    m = harness.grade_turn({"q": "", "required_tools": ["get_pokedex_entry"]}, _record(tools=(("get_pokedex_entry", {"pokemon": "bulbasaur"}, BULBASAUR),)), [])
    assert m["tool_expectation"]["passed"]
    assert {"pokemon_tool_used", "dexter_persona", "grounded_facts"} <= set(m)


def test_grade_missing_and_forbidden_tools():
    rec = _record(tools=(("search_pokemon", {}, "[]"),))
    m = harness.grade_turn({"q": "", "required_tools": ["get_pokedex_entry"], "forbidden_tools": ["search_pokemon"]}, rec, [])
    assert not m["tool_expectation"]["passed"]
    assert "get_pokedex_entry" in m["tool_expectation"]["reasoning"]
    assert "search_pokemon" in m["tool_expectation"]["reasoning"]


def test_grade_followup_args_and_reply_contains():
    rec = _record(query="¿y la de charmander?")
    spec = {"q": "", "expect_args": {"get_pokedex_entry": "charmander"}, "reply_contains": ["BULBASAUR", "intelécto"]}
    m = harness.grade_turn(spec, rec, [])
    assert not m["followup_args"]["passed"]
    assert m["reply_contains"]["passed"]  # accent/case-insensitive


def test_grade_unknown_pokemon():
    error = (("get_pokedex_entry", {"pokemon": "pikachuzard"}, "❌ Pokémon 'pikachuzard' not found."),)
    spec = {"q": "", "any_of_tools": ["get_pokedex_entry", "search_pokemon"], "unknown_pokemon": "pikachuzard"}
    honest = _record(query="háblame de pikachuzard", reply="Pikachuzard no figura en mis registros.", tools=error)
    m = harness.grade_turn(spec, honest, [])
    assert m["no_invented_entry"]["passed"]
    assert m["tool_expectation"]["passed"]  # attempted lookup counts for any_of
    invented = _record(query="háblame de pikachuzard", reply="PIKACHUZARD. El Pokémon Fusión. Ataque 130.", tools=error)
    assert not harness.grade_turn(spec, invented, [])["no_invented_entry"]["passed"]
    # Bold name, no numbers: the opening alone must fail the gate.
    bold = _record(query="háblame de pikachuzard", reply="**PIKACHUZARD**. El Pokémon Ratón Llama. Tipo Eléctrico.", tools=error)
    m = harness.grade_turn(spec, bold, [])
    assert not m["no_invented_entry"]["passed"]
    assert not m["grounded_facts"]["passed"]


def test_grade_failed_request_only_counts_completed():
    rec = {"query": "x", "status": "http_429", "error": "rate", "reply": "", "tools": []}
    assert harness.grade_turn({"q": "x", "required_tools": ["get_pokedex_entry"]}, rec, []) == {
        "completed": {"passed": False, "reasoning": "rate"}
    }


def _args(**kw):
    defaults = dict(label="t", url="http://x", repeat=1, pace=0.0, only=None, cases=str(harness.CASES_FILE), judge=False, judge_model="m", submit=False)
    return argparse.Namespace(**{**defaults, **kw})


def test_run_accumulates_evidence_across_turns(tmp_path):
    cases = {
        "cases": [
            {
                "id": "followup",
                "turns": [
                    {"q": "háblame de bulbasaur", "required_tools": ["get_pokedex_entry"]},
                    {"q": "¿cuál es su velocidad?", "reply_contains": ["45"]},
                ],
            }
        ]
    }
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    replies = iter([_record(), _record(query="¿cuál es su velocidad?", reply="Su velocidad es 45. Previsible.", tools=())])
    sleeps = []
    doc = harness.run(_args(cases=str(path), repeat=2, pace=21), send=lambda *a: copy.deepcopy(next(replies, None) or _record()), sleep=sleeps.append)

    assert [r["case"] for r in doc["runs"]] == ["followup", "followup"]
    second = doc["runs"][0]["turns"][1]
    assert second["metrics"]["grounded_facts"]["passed"]  # 45 came from turn 1's tool result
    assert sleeps == [21, 21, 21]  # paced between the 4 requests
    rates = doc["summary"]["rates"]
    assert rates["completed"] == {"passed": 4, "total": 4, "rate": 100.0}
    assert doc["summary"]["perf_median_by_turn"]["followup/1"]["llm_calls"] == 3


def test_run_with_judge_records_verdicts(monkeypatch, tmp_path):
    seen = {}

    def fake_judge(query, reply, tool_texts, **kw):
        seen["tool_texts"] = tool_texts
        return {"persona": 4, "grounded": True, "unsupported_claims": [], "reasoning": "ok"}

    monkeypatch.setattr(harness, "judge_turn", fake_judge)
    doc = harness.run(_args(only=["pokedex"], judge=True), send=lambda *a: _record(), sleep=lambda s: None)
    assert seen["tool_texts"] == [BULBASAUR]  # skills summary is not evidence
    assert doc["summary"]["means"]["judge_persona"] == {"mean": 4, "n": 1}
    assert doc["summary"]["rates"]["judge_grounded"] == {"passed": 1, "total": 1, "rate": 100.0}


def test_judge_grounded_drop_is_a_regression(monkeypatch):
    def judged(grounded):
        monkeypatch.setattr(harness, "judge_turn", lambda *a, **k: {"persona": 4, "grounded": grounded, "unsupported_claims": ["x"], "reasoning": "r"})
        return harness.run(_args(only=["pokedex"], judge=True), send=lambda *a: _record(), sleep=lambda s: None)

    base, cand = judged(True), judged(False)
    assert cand["runs"][0]["turns"][0]["metrics"]["judge_grounded"]["metadata"] == {"unsupported_claims": ["x"]}
    assert [r.metric for r in harness.compare(base, cand)] == ["judge_grounded"]
    monkeypatch.setattr(harness, "judge_turn", lambda *a, **k: None)  # judge error: no metric, not a fail
    errored = harness.run(_args(only=["pokedex"], judge=True), send=lambda *a: _record(), sleep=lambda s: None)
    assert "judge_grounded" not in errored["summary"]["rates"]
    assert errored["summary"]["judge_errors"] == 1
    assert harness.compare(base, errored) == []


# --- compare ------------------------------------------------------------------------------


def _doc(label="base", **rates):
    defaults = {"completed": 100.0, "tool_expectation": 100.0, "pokemon_tool_used": 100.0, "dexter_persona": 90.0, "grounded_facts": 90.0}
    merged = {**defaults, **rates}
    return {
        "label": label,
        "summary": {
            "rates": {k: {"passed": int(v), "total": 100, "rate": v} for k, v in merged.items() if v is not None},
            "means": {},
            "judge_errors": 0,
            "perf_median": {"elapsed_ms": 2000},
            "perf_median_by_turn": {},
        },
    }


def test_compare_equal_runs_pass():
    assert harness.compare(_doc(), _doc("cand")) == []


def test_compare_tool_rate_drop_is_regression():
    [reg] = harness.compare(_doc(), _doc("cand", tool_expectation=97.0))
    assert reg.metric == "tool_expectation"
    assert reg.allowed_drop == 0


def test_compare_heuristic_tolerance():
    assert harness.compare(_doc(), _doc("cand", dexter_persona=85.0)) == []
    [reg] = harness.compare(_doc(), _doc("cand", dexter_persona=75.0))
    assert reg.metric == "dexter_persona"


def test_compare_judge_metrics():
    base, cand = _doc(), _doc("cand")
    base["summary"]["means"]["judge_persona"] = {"mean": 4.5, "n": 12}
    assert harness.compare(base, cand) == []  # judge only in base → skipped
    cand["summary"]["means"]["judge_persona"] = {"mean": 3.9, "n": 12}
    assert [r.metric for r in harness.compare(base, cand)] == ["judge_persona"]


def test_compare_missing_required_metric_is_regression():
    assert [r.metric for r in harness.compare(_doc(), _doc("cand", grounded_facts=None))] == ["grounded_facts"]


def test_compare_cli_exit_codes(tmp_path, capsys):
    base, good, bad = tmp_path / "b.json", tmp_path / "g.json", tmp_path / "x.json"
    base.write_text(json.dumps(_doc()))
    good.write_text(json.dumps(_doc("good")))
    bad.write_text(json.dumps(_doc("bad", pokemon_tool_used=90.0)))
    assert harness.main(["compare", str(base), str(good)]) == 0
    assert harness.main(["compare", str(base), str(bad)]) == 1
    out = capsys.readouterr()
    assert "REGRESSION" in out.out
    assert "pokemon_tool_used" in out.err


# --- --submit guard ----------------------------------------------------------------------------


@pytest.mark.parametrize("ml_app", ["mcpokedexagent", "McpokedexAgent", " MCPOKEDEXAGENT "])
def test_submit_refuses_prod_ml_app(ml_app):
    assert "prod" in harness.submit_refusal({"DD_LLMOBS_ML_APP": ml_app, "DD_API_KEY": "k"})


def test_submit_needs_dev_ml_app_and_key():
    assert harness.submit_refusal({"DD_API_KEY": "k"})
    assert harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev"})
    assert harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev", "DD_API_KEY": "k"}) is None


def test_run_cli_refuses_prod_before_any_request(monkeypatch, capsys):
    monkeypatch.setenv("DD_LLMOBS_ML_APP", "McpokedexAgent")
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setattr(harness, "run", lambda *a, **k: pytest.fail("must not run"))
    assert harness.main(["run", "--label", "x", "--judge", "--submit"]) == 2
    assert "prod" in capsys.readouterr().err


# --- cases.json ------------------------------------------------------------------------------


def test_cases_cover_the_guardrail_scenarios():
    cases = {c["id"]: c for c in harness.load_cases(harness.CASES_FILE)}
    assert {"pokedex", "followup", "session6", "offtopic", "fake"} <= set(cases)
    assert cases["pokedex"]["turns"][0]["q"] == "háblame de bulbasaur"
    assert cases["followup"]["turns"][1]["q"] == "¿y la de charmander?"
    assert len(cases["session6"]["turns"]) == 6
    assert any(t.get("unknown_pokemon") for c in cases.values() for t in c["turns"])


def test_cases_use_tool_sets_of_real_data_tools_only():
    assert POKEMON_DATA_TOOLS <= MCP_TOOLS
    for case in harness.load_cases(harness.CASES_FILE):
        for turn in case["turns"]:
            named = {*turn.get("required_tools", []), *turn.get("any_of_tools", []), *turn.get("forbidden_tools", []), *turn.get("expect_args", {})}
            assert not named & SCAFFOLDING_TOOLS, case["id"]
            assert named <= POKEMON_DATA_TOOLS, case["id"]


# --- judge ------------------------------------------------------------------------------------


VERDICT = {"persona": 4, "grounded": False, "unsupported_claims": ["débil a hielo"], "reasoning": "Pikachu no es débil a hielo."}


@pytest.mark.parametrize(
    "text",
    [json.dumps(VERDICT), f"```json\n{json.dumps(VERDICT)}\n```", f"Veredicto: {json.dumps(VERDICT)} fin"],
)
def test_parse_verdict_tolerant(text):
    assert judge.parse_verdict(text) == VERDICT


@pytest.mark.parametrize("text", [None, "", "no json", "{bad json}", json.dumps({**VERDICT, "persona": 9}), json.dumps({"grounded": True})])
def test_parse_verdict_rejects_garbage(text):
    assert judge.parse_verdict(text) is None


def test_judge_turn_with_mocked_litellm(monkeypatch):
    import litellm

    calls = []

    def fake_completion(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(VERDICT)))])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert judge.judge_turn("háblame de pikachu", "PIKACHU. ...", ["{}"], focus="ojo") == VERDICT
    [kw] = calls
    assert kw["model"] == judge.DEFAULT_JUDGE_MODEL
    assert kw["temperature"] == 0
    assert "ojo" in kw["messages"][-1]["content"]

    monkeypatch.setattr(litellm, "completion", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert judge.judge_turn("q", "r", []) is None
