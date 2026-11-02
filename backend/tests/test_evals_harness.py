"""Offline eval harness (backend/evals/harness.py) and LLM judge parsing; no network."""

import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent import MCP_TOOLS
from backend.evals import checks, harness, judge
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


# Real fake/1 turns from the epic-15 harness runs, hand-labeled (see the fixture's _doc).
FAKE_TURNS = json.loads((Path(__file__).parent / "fixtures" / "fake_case_turns.json").read_text(encoding="utf-8"))["turns"]
FAKE_SPEC = {"q": "háblame de pikachuzard", "any_of_tools": ["get_pokedex_entry", "get_pokemon_info", "search_pokemon"], "unknown_pokemon": "pikachuzard"}


@pytest.mark.parametrize("turn", FAKE_TURNS, ids=[t["run"] for t in FAKE_TURNS])
def test_no_invented_entry_on_real_fake_turns(turn):
    m = harness.grade_turn(FAKE_SPEC, turn, [turn["query"]])
    assert m["no_invented_entry"]["passed"] is not turn["invented"], m["no_invented_entry"]["reasoning"]


def test_no_invented_entry_catches_a_lowercase_opening():
    # arm 3 r1 passed the uppercase-only check: "Pikachu. El Pokémon Ratón. …"
    [turn] = [t for t in FAKE_TURNS if t["run"] == "perf-17-arm3-low-low r1"]
    assert turn["reply"].startswith("Pikachu. El Pokémon Ratón.")
    assert "narró una ficha" in harness.grade_turn(FAKE_SPEC, turn, [])["no_invented_entry"]["reasoning"]


PIKACHU = next(t["tools"][0]["result"] for t in FAKE_TURNS if t["run"] == "perf-18 r1")


@pytest.mark.parametrize(
    ("reply", "invented"),
    [
        # Narrates the substitute without the opening: genus, or its name with no denial.
        ("Escaneo completado. El Pokémon Ratón almacena electricidad en sus mejillas.", True),
        ("Pikachu, de tipo eléctrico, almacena electricidad en sus mejillas.", True),
        ("**Pikachu**. *El Pokémon Ratón.* Escaneo completado.", True),
        # Says the asked-for name does not exist and only suggests the substitute.
        ("No existe registro de un Pokémon denominado “pikachuzard”. Si la intención era Pikachu, consulte su entrada.", False),
        ("Pikachuzard no aparece registrado. No dispongo de datos verificables.", False),
        ("Pikachuzard. No existe tal registro en la base de datos oficial.", False),
    ],
)
def test_no_invented_entry_substituted_lookup(reply, invented):
    rec = {"query": "háblame de pikachuzard", "status": "done", "reply": reply, "tools": [{"name": "get_pokedex_entry", "args": {"pokemon": "pikachu"}, "result": PIKACHU, "error": False}]}
    assert harness.grade_turn(FAKE_SPEC, rec, [])["no_invented_entry"]["passed"] is not invented


def test_substituted_entry_ignores_the_asked_name_and_errors():
    asked = [checks.ToolObservation("get_pokedex_entry", {"pokemon": "pikachuzard"}, PIKACHU.replace('"pikachu"', '"pikachuzard"', 1))]
    assert harness.substituted_entry("pikachuzard", asked, "Pikachuzard, el Pokémon Ratón.") is None
    failed = [checks.ToolObservation("get_pokedex_entry", {"pokemon": "pikachu"}, "❌ Pokémon 'pikachu' not found.", error=True)]
    assert harness.substituted_entry("pikachuzard", failed, "Pikachu es eléctrico.") is None


def test_entry_opening_any_case_is_not_greedy():
    assert checks.entry_opening("Mr. Mime. El Pokémon Barrera.").group("name") == "Mr. Mime"
    assert checks.entry_opening("pikachu. Pokémon Ratón.").group("genus") == "Ratón"
    for reply in (
        "Pokémon “pikachuzard” no encontrado. Verifica la ortografía.",
        "No se encontró el Pokémon solicitado.  \nEl registro indica que no existe.",
        "Error: El registro indica que no existe ningún Pokémon.",
        "El escaneo terminó. Pokémon Ratón no es un nombre.",
    ):
        assert checks.entry_opening(reply) is None, reply
    # The strict persona opening stays uppercase-only.
    assert checks.pokedex_opening("Pikachu. El Pokémon Ratón.") is None


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
    assert doc["summary"]["means"]["judge_persona"] == {"mean": 4, "n": 1, "sd": None}
    assert doc["summary"]["rates"]["judge_grounded"] == {"passed": 1, "total": 1, "rate": 100.0}


def test_judge_grounded_drop_gates_only_with_gate_judge(monkeypatch):
    def judged(grounded):
        monkeypatch.setattr(harness, "judge_turn", lambda *a, **k: {"persona": 4, "grounded": grounded, "unsupported_claims": ["x"], "reasoning": "r"})
        return harness.run(_args(only=["pokedex"], judge=True, repeat=10), send=lambda *a: _record(), sleep=lambda s: None)

    base, cand = judged(True), judged(False)
    assert cand["runs"][0]["turns"][0]["metrics"]["judge_grounded"]["metadata"] == {"unsupported_claims": ["x"]}
    assert harness.compare(base, cand) == []  # reported, not gating
    [row] = [r for r in harness.evaluate(base, cand) if r.metric == "judge_grounded"]
    assert not row.gating and not row.regression and "not gating" in row.detail
    assert [r.metric for r in harness.compare(base, cand, gate_judge=True)] == ["judge_grounded"]
    monkeypatch.setattr(harness, "judge_turn", lambda *a, **k: None)  # judge error: no metric, not a fail
    errored = harness.run(_args(only=["pokedex"], judge=True), send=lambda *a: _record(), sleep=lambda s: None)
    assert "judge_grounded" not in errored["summary"]["rates"]
    assert errored["summary"]["judge_errors"] == 1
    assert harness.compare(base, errored, gate_judge=True) == []


# --- compare ------------------------------------------------------------------------------


def _doc(label="base", n=100, **rates):
    defaults = {"completed": 100.0, "tool_expectation": 100.0, "pokemon_tool_used": 100.0, "dexter_persona": 90.0, "grounded_facts": 90.0}
    merged = {**defaults, **rates}
    return {
        "label": label,
        "summary": {
            "rates": {k: {"passed": round(v * n / 100), "total": n, "rate": v} for k, v in merged.items() if v is not None},
            "means": {},
            "judge_errors": 0,
            "perf_median": {"elapsed_ms": 2000},
            "perf_median_by_turn": {},
        },
    }


def test_compare_equal_runs_pass():
    assert harness.compare(_doc(), _doc("cand")) == []


def test_two_proportion_z():
    assert harness.two_proportion_z(23, 35, 18, 36) == pytest.approx(1.34, abs=0.01)  # dev-base → perf-20 judge_grounded
    assert harness.two_proportion_z(10, 10, 10, 10) == 0.0
    assert harness.two_proportion_z(5, 10, 9, 10) < 0  # improvement


def test_compare_tool_rate_drop_needs_significance():
    # 1 of 27 follow-ups (perf-20) or 100 → 97 % at n = 100 is within sampling noise.
    [row] = [r for r in harness.evaluate(_doc(), _doc("cand", tool_expectation=97.0)) if r.metric == "tool_expectation"]
    assert not row.regression and "noise" in row.detail and row.z == pytest.approx(1.75, abs=0.01)
    [reg] = harness.compare(_doc(), _doc("cand", tool_expectation=90.0))
    assert reg.metric == "tool_expectation"
    assert reg.allowed_drop == 0
    assert reg.z > harness.Z_CRITICAL


def test_compare_heuristic_tolerance():
    assert harness.compare(_doc(), _doc("cand", dexter_persona=85.0)) == []
    [reg] = harness.compare(_doc(), _doc("cand", dexter_persona=75.0))
    assert reg.metric == "dexter_persona"


def test_compare_drop_beyond_tolerance_but_noise_passes():
    # n = 36 (harness --repeat 3): 32/36 → 27/36 is −13.9 pp, beyond the 10 pp tolerance, but z ≈ 1.53.
    base, cand = _doc(n=36, grounded_facts=90.0), _doc("cand", n=36, grounded_facts=75.0)
    [row] = [r for r in harness.evaluate(base, cand) if r.metric == "grounded_facts"]
    assert not row.regression and row.detail.startswith("-13.9 pp, noise")


@pytest.mark.parametrize("metric", sorted(harness.STRICT_METRICS))
def test_compare_strict_metrics_flag_any_drop(metric):
    base, cand = _doc(n=3, **{metric: 100.0}), _doc("cand", n=3, **{metric: 66.7})
    [reg] = harness.compare(base, cand)
    assert reg.metric == metric and "strict" in reg.detail
    assert reg.z < harness.Z_CRITICAL  # flagged although not significant


def test_compare_judge_persona_mean():
    base, cand = _doc(), _doc("cand")
    base["summary"]["means"]["judge_persona"] = {"mean": 4.5, "n": 12, "sd": 0.5}
    assert harness.compare(base, cand, gate_judge=True) == []  # judge only in base → skipped
    cand["summary"]["means"]["judge_persona"] = {"mean": 3.9, "n": 12, "sd": 0.5}
    assert harness.compare(base, cand) == []  # not gating by default
    [reg] = harness.compare(base, cand, gate_judge=True)
    assert reg.metric == "judge_persona" and reg.z == pytest.approx(2.94, abs=0.01)
    cand["summary"]["means"]["judge_persona"] = {"mean": 3.9, "n": 12, "sd": 1.5}
    assert harness.compare(base, cand, gate_judge=True) == []  # z ≈ 1.31: noise


def test_compare_missing_required_metric_is_regression():
    assert [r.metric for r in harness.compare(_doc(), _doc("cand", grounded_facts=None))] == ["grounded_facts"]


def test_compare_cli_exit_codes(tmp_path, capsys):
    base, good, bad = tmp_path / "b.json", tmp_path / "g.json", tmp_path / "x.json"
    base.write_text(json.dumps(_doc()))
    good.write_text(json.dumps(_doc("good", tool_expectation=97.0)))
    bad.write_text(json.dumps(_doc("bad", pokemon_tool_used=90.0)))
    assert harness.main(["compare", str(base), str(good)]) == 0
    assert "noise: z 1.75" in capsys.readouterr().out
    assert harness.main(["compare", str(base), str(bad)]) == 1
    out = capsys.readouterr()
    assert "REGRESSION" in out.out
    assert "pokemon_tool_used" in out.err


def test_compare_cli_gate_judge(tmp_path, capsys):
    base, cand = tmp_path / "b.json", tmp_path / "c.json"
    base.write_text(json.dumps(_doc(judge_grounded=90.0)))
    cand.write_text(json.dumps(_doc("cand", judge_grounded=60.0)))
    assert harness.main(["compare", str(base), str(cand)]) == 0
    assert "info only (--gate-judge)" in capsys.readouterr().out
    assert harness.main(["compare", str(base), str(cand), "--gate-judge"]) == 1
    assert "judge_grounded" in capsys.readouterr().err


# --- --submit guard ----------------------------------------------------------------------------


@pytest.mark.parametrize("ml_app", ["mcpokedexagent", "McpokedexAgent", " MCPOKEDEXAGENT "])
def test_submit_refuses_prod_ml_app(ml_app):
    assert "prod" in harness.submit_refusal({"DD_LLMOBS_ML_APP": ml_app, "DD_API_KEY": "k"}, harness.DEFAULT_URL)


def test_submit_needs_dev_ml_app_and_key():
    url = harness.DEFAULT_URL
    assert harness.submit_refusal({"DD_API_KEY": "k"}, url)
    assert harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev"}, url)
    assert harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev", "DD_API_KEY": "k"}, url) is None


@pytest.mark.parametrize("url", ["http://localhost:8765", "http://[::1]:8765"])
def test_submit_allows_loopback_urls(url):
    assert harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev", "DD_API_KEY": "k"}, url) is None


@pytest.mark.parametrize("url", ["https://mcpokedex.com", "http://192.168.1.10:8000", "not a url"])
def test_submit_refuses_remote_backend(url):
    reason = harness.submit_refusal({"DD_LLMOBS_ML_APP": "mcpokedexagent-dev", "DD_API_KEY": "k"}, url)
    assert "local backend" in reason


def test_run_cli_refuses_prod_before_any_request(monkeypatch, capsys):
    monkeypatch.setenv("DD_LLMOBS_ML_APP", "McpokedexAgent")
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setattr(harness, "run", lambda *a, **k: pytest.fail("must not run"))
    assert harness.main(["run", "--label", "x", "--judge", "--submit"]) == 2
    assert "prod" in capsys.readouterr().err
    monkeypatch.setenv("DD_LLMOBS_ML_APP", "mcpokedexagent-dev")
    assert harness.main(["run", "--label", "x", "--judge", "--submit", "--url", "https://mcpokedex.com"]) == 2
    assert "local backend" in capsys.readouterr().err


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
