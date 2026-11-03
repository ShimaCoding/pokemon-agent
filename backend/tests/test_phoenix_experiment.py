"""The eval harness as Phoenix datasets/experiments (backend/evals/phoenix_experiment.py); no network."""

import json
from types import SimpleNamespace

import pytest

from backend.evals import harness
from backend.evals import phoenix_experiment as px

BULBASAUR = json.dumps(
    {"id": 1, "name": "bulbasaur", "genus": "Pokémon Semilla", "base_stats": {"speed": 45}}, ensure_ascii=False
)
REPLY = "BULBASAUR. El Pokémon Semilla. Escaneo completado. Velocidad 45: insuficiente para tu intelecto."

CASES = [
    {
        "id": "followup",
        "turns": [
            {"q": "háblame de bulbasaur", "required_tools": ["get_pokedex_entry"], "expect_args": {"get_pokedex_entry": "bulbasaur"}},
            {"q": "¿y la de charmander?", "required_tools": ["get_pokedex_entry"], "expect_args": {"get_pokedex_entry": "charmander"}},
        ],
    },
    {"id": "offtopic", "turns": [{"q": "¿qué opinas del clima?", "forbidden_tools": ["get_pokedex_entry"]}]},
]


def _record(query, *, pokemon="bulbasaur", status="done"):
    tools = [{"name": "get_pokedex_entry", "args": {"pokemon": pokemon}, "result": BULBASAUR, "error": False}] if pokemon else []
    return {"query": query, "status": status, "error": None, "reply": REPLY if pokemon else "Consulta irrelevante.", "tools": tools}


def test_dataset_examples_one_per_case():
    cols = px.dataset_examples(CASES)
    assert cols["inputs"] == [
        {"case": "followup", "turns": ["háblame de bulbasaur", "¿y la de charmander?"]},
        {"case": "offtopic", "turns": ["¿qué opinas del clima?"]},
    ]
    assert cols["outputs"][0] == {"turns": CASES[0]["turns"]}
    assert cols["metadata"] == [{"case": "followup", "n_turns": 2}, {"case": "offtopic", "n_turns": 1}]


def test_every_harness_metric_has_an_evaluator():
    names = set(px.make_evaluators())
    assert set(harness.RATE_GATES) | set(harness.MEAN_GATES) == names


def test_rate_evaluation_scores_the_share_of_passing_turns():
    expected = {"turns": CASES[0]["turns"]}
    # Second turn looked up bulbasaur again instead of charmander.
    output = {"turns": [_record("háblame de bulbasaur"), _record("¿y la de charmander?")]}

    args = px.rate_evaluation("followup_args", output, expected)
    assert (args["score"], args["label"]) == (0.5, "fail")
    assert args["explanation"].startswith("t2: get_pokedex_entry sin 'charmander'")
    assert args["metadata"] == {"passed": 1, "total": 2}

    tools = px.rate_evaluation("tool_expectation", output, expected)
    assert (tools["score"], tools["label"], tools["explanation"]) == (1.0, "pass", "ok")


def test_rate_evaluation_not_applicable():
    output = {"turns": [_record("¿qué opinas del clima?", pokemon=None)]}
    result = px.rate_evaluation("followup_args", output, {"turns": CASES[1]["turns"]})
    assert result["score"] is None and result["label"] == "n/a"


def test_persona_evaluation_is_the_judge_mean():
    verdict = lambda p: {"persona": p, "grounded": True, "reasoning": f"r{p}", "unsupported_claims": []}  # noqa: E731
    output = {"turns": [{**_record("a"), "judge": verdict(5)}, {**_record("b"), "judge": verdict(2)}, _record("c")]}
    result = px.persona_evaluation(output)
    assert (result["score"], result["label"]) == (3.5, "fail")
    assert result["explanation"] == "t1: 5 — r5 | t2: 2 — r2"
    assert px.persona_evaluation({"turns": [_record("a")]})["label"] == "n/a"


def test_task_plays_one_conversation_with_pace_and_judge():
    sent, judged, slept = [], [], []
    clock = iter(range(0, 1000, 5))

    def send(http, url, conversation_id, query):
        sent.append((url, conversation_id, query))
        return _record(query, pokemon="charmander" if "charmander" in query else "bulbasaur")

    def judge(query, reply, tool_texts, *, model, focus=None):
        judged.append((query, len(tool_texts), model))
        return {"persona": 4, "grounded": True, "reasoning": "ok", "unsupported_claims": []}

    runs = []
    pacer = px.Pacer(21, sleep=slept.append, clock=lambda: next(clock))
    task = px.make_task("http://backend", pacer=pacer, judge_model="judge-m", send=send, judge=judge, runs=runs)
    case = CASES[0]
    out = task({"case": case["id"], "turns": [t["q"] for t in case["turns"]]}, {"turns": case["turns"]})

    assert [q for _, _, q in sent] == ["háblame de bulbasaur", "¿y la de charmander?"]
    assert len({cid for _, cid, _ in sent}) == 1 and out["conversation_id"] == sent[0][1]
    assert slept == [16]  # 21 s pace, 5 s already elapsed on the fake clock
    # The judge sees the data tool results accumulated so far, as in harness.run.
    assert judged == [("háblame de bulbasaur", 1, "judge-m"), ("¿y la de charmander?", 2, "judge-m")]
    assert all(t["judge"]["persona"] == 4 for t in out["turns"])
    assert runs == [out]


def test_task_without_judge_and_failed_turns():
    def send(http, url, conversation_id, query):
        return {"query": query, "status": "http_500", "error": "boom", "reply": "", "tools": []}

    task = px.make_task("http://b", pacer=px.Pacer(0), send=send, judge=lambda *a, **k: pytest.fail("no judge"))
    out = task({"case": "offtopic", "turns": ["x"]}, {"turns": CASES[1]["turns"]})
    assert "judge" not in out["turns"][0]
    assert px.rate_evaluation("completed", out, {"turns": CASES[1]["turns"]})["label"] == "fail"


def test_harness_doc_feeds_the_compare_gate():
    runs = [
        {"case": "followup", "conversation_id": "c1", "turns": [_record("háblame de bulbasaur"), _record("¿y la de charmander?", pokemon="charmander")]},
        {"case": "followup", "conversation_id": "c2", "turns": [_record("háblame de bulbasaur"), _record("¿y la de charmander?")]},
    ]
    doc = px.harness_doc({"label": "cand", "repeat": 2}, CASES, runs)
    assert [(r["case"], r["repeat"]) for r in doc["runs"]] == [("followup", 1), ("followup", 2)]
    assert doc["summary"]["rates"]["followup_args"] == {"passed": 3, "total": 4, "rate": 75.0}
    assert "metrics" not in runs[0]["turns"][0]  # the task outputs stay as recorded

    base = px.harness_doc({"label": "base", "repeat": 2}, CASES, [runs[0], runs[0]])
    results = {r.metric: r for r in harness.evaluate(base, doc)}
    assert results["followup_args"].base == 100.0 and results["followup_args"].candidate == 75.0


class FakeDatasets:
    def __init__(self, existing=None):
        self.existing, self.created = existing, []

    def get_dataset(self, *, dataset):
        if self.existing is None:
            raise ValueError("not found")
        return self.existing

    def create_dataset(self, **kw):
        self.created.append(kw)
        return SimpleNamespace(name=kw["name"])


def test_ensure_dataset_creates_reuses_and_refuses_drift():
    client = SimpleNamespace(datasets=FakeDatasets())
    px.ensure_dataset(client, "mcpokedex-cases", CASES)
    [kw] = client.datasets.created
    assert kw["name"] == "mcpokedex-cases" and len(kw["inputs"]) == 2

    cols = px.dataset_examples(CASES)
    same = SimpleNamespace(examples=[{"input": i, "output": o} for i, o in zip(cols["inputs"], cols["outputs"])])
    client = SimpleNamespace(datasets=FakeDatasets(existing=same))
    assert px.ensure_dataset(client, "mcpokedex-cases", list(reversed(CASES))) is same

    with pytest.raises(SystemExit, match="mcpokedex-cases-v2"):
        px.ensure_dataset(client, "mcpokedex-cases", CASES[:1])
