"""
The eval harness as Phoenix datasets and experiments.

    uv run python -m backend.evals.phoenix_experiment upload [--dataset mcpokedex-cases-v2]
    uv run python -m backend.evals.phoenix_experiment run --label dev-base [--url http://127.0.0.1:8765]
        [--description "REASONING_EFFORT_FINAL=low"] [--repeat 3] [--pace 21]
        [--judge [--judge-model groq/openai/gpt-oss-120b]] [--dataset mcpokedex-cases-v2]

``upload`` turns ``cases.json`` into a Phoenix dataset, one example per case: input = the
user turns, expected output = each turn's expectations. ``run`` (which uploads the dataset
if it is missing) runs a Phoenix experiment over it:

- the task plays the case as one new conversation against a backend with the harness's
  SSE client (``harness.run_turn``), ``--pace`` seconds between requests (``--pace 0``
  with a local backend started with ``RATE_LIMIT_DISABLED=1``), and with
  ``--judge`` asks the LLM judge about each turn, exactly like ``harness run``;
- every harness metric is an evaluator (``EVALUATORS``): score = share of the case's
  turns where it passed, label pass/fail, the failing turns' reasons as explanation;
  ``n/a`` (no score) when no turn of the case is graded by it. ``judge_persona`` is the
  mean 1–5 score.

The adversarial cases (#41) are a dataset of their own, so their rates never mix with
the functional ones: ``--cases backend/evals/cases_adversarial.json --dataset mcpokedex-adversarial-v2`` (``mcpokedex-adversarial`` is the frozen first draft, without ``reply_excludes``).

Phoenix keeps every experiment with its outputs, scores and task traces, so two runs can
be compared side by side in the UI (Datasets → mcpokedex-cases-v2 → Experiments). The same
run is also saved as ``<out>/<label>.json`` in the harness format, so the statistical
gate is unchanged:

    uv run python -m backend.evals.harness compare .context/evals/base.json .context/evals/cand.json

Phoenix is ``--phoenix-url`` (default ``PHOENIX_COLLECTOR_ENDPOINT``, else
http://localhost:6006) with ``PHOENIX_API_KEY`` when it has auth on. The backend under
test keeps tracing wherever its own env says: point it at the same Phoenix to open each
turn's trace (the harness JSON keeps ``trace_id``).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from backend.evals import checks, harness
from backend.evals.judge import DEFAULT_JUDGE_MODEL, JUDGE_VERSION, judge_turn

DEFAULT_DATASET = "mcpokedex-cases-v2"
DEFAULT_PHOENIX_URL = "http://localhost:6006"
# A case is a whole conversation: up to 6 turns, ``--pace`` seconds apart.
TASK_TIMEOUT_S = 900

# Harness metrics graded per turn (``harness.grade_conversation``), one evaluator each.
RATE_METRICS = (*harness.RATE_GATES,)
JUDGE_KIND = {"judge_grounded", "judge_persona"}


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def dataset_examples(cases: list[dict]) -> dict[str, list[dict]]:
    """``cases.json`` cases → the inputs/outputs/metadata columns of a Phoenix dataset. Pure."""
    return {
        "inputs": [{"case": c["id"], "turns": [t["q"] for t in c["turns"]]} for c in cases],
        "outputs": [{"turns": c["turns"]} for c in cases],
        "metadata": [{"case": c["id"], "n_turns": len(c["turns"])} for c in cases],
    }


def ensure_dataset(client: Any, name: str, cases: list[dict]) -> Any:
    """The dataset ``name``; created from ``cases`` when missing, refused when it no longer matches them."""
    columns = dataset_examples(cases)
    try:
        dataset = client.datasets.get_dataset(dataset=name)
    except Exception:  # noqa: BLE001 — the client raises httpx/ValueError variants for "not found"
        dataset = None
    if dataset is None:
        return client.datasets.create_dataset(
            name=name,
            dataset_description="MCPokédex labeled conversations (backend/evals/cases.json)",
            **columns,
        )
    remote = sorted(json.dumps([e["input"], e["output"]], sort_keys=True) for e in dataset.examples)
    local = sorted(json.dumps([i, o], sort_keys=True) for i, o in zip(columns["inputs"], columns["outputs"]))
    if remote != local:
        raise SystemExit(
            f"Phoenix dataset {name!r} differs from cases.json; upload the new cases under another name "
            f"(--dataset {name}-v2) so earlier experiments stay comparable"
        )
    return dataset


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class Pacer:
    """At least ``pace`` seconds between requests across all tasks (anonymous limit: 3/min)."""

    def __init__(self, pace: float, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._pace, self._sleep, self._clock = pace, sleep, clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None and self._pace:
                remaining = self._pace - (self._clock() - self._last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()


def make_task(
    url: str,
    *,
    pacer: Pacer,
    judge_model: str | None = None,
    send: Callable[..., dict] = harness.run_turn,
    judge: Callable[..., dict | None] = judge_turn,
    runs: list[dict] | None = None,
) -> Callable[[dict, dict], dict]:
    """The experiment task: one case played as a new conversation. Appends each run to ``runs``."""
    http = httpx.Client()

    def task(input: dict, expected: dict) -> dict:  # noqa: A002 — Phoenix binds by parameter name
        conversation_id = str(uuid.uuid4())
        records: list[dict] = []
        tool_texts: list[str] = []  # data tool results only (judge input), as in harness.run
        for query, spec in zip(input["turns"], expected["turns"]):
            pacer.wait()
            record = send(http, url, conversation_id, query)
            if record["status"] == "done":
                tool_texts.extend(harness.evidence_texts(record)[1:])
                if judge_model:
                    record["judge"] = judge(
                        query, record["reply"], [t for t in tool_texts if t], model=judge_model, focus=spec.get("judge_focus")
                    )
            records.append(record)
        output = {"case": input["case"], "conversation_id": conversation_id, "turns": records}
        if runs is not None:
            runs.append(output)
        return output

    return task


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


def rate_evaluation(name: str, output: dict, expected: dict) -> dict:
    """One harness metric over a case's turns, as a Phoenix evaluation result. Pure."""
    graded = harness.grade_conversation(expected["turns"], output["turns"])
    results = [(i, m[name]) for i, m in enumerate(graded, 1) if name in m]
    if not results:
        return {"score": None, "label": "n/a", "explanation": "no aplica a este caso"}
    passed = sum(r["passed"] for _, r in results)
    failures = [f"t{i}: {r['reasoning']}" for i, r in results if not r["passed"]]
    return {
        "score": passed / len(results),
        "label": "pass" if not failures else "fail",
        "explanation": "; ".join(failures) or "ok",
        "metadata": {"passed": passed, "total": len(results)},
    }


def persona_evaluation(output: dict) -> dict:
    """Mean judge persona score (1–5) over the judged turns. Pure."""
    verdicts = [t["judge"] for t in output["turns"] if t.get("judge")]
    if not verdicts:
        return {"score": None, "label": "n/a", "explanation": "sin veredictos del juez (--judge)"}
    mean = statistics.mean(v["persona"] for v in verdicts)
    return {
        "score": mean,
        "label": "pass" if mean >= 4 else "fail",
        "explanation": " | ".join(f"t{i}: {v['persona']} — {v['reasoning']}" for i, v in enumerate(verdicts, 1))[:2000],
    }


def make_evaluators() -> dict[str, Callable[..., dict]]:
    """Phoenix evaluators by name: every harness rate metric plus the judge persona mean."""
    from phoenix.client.experiments import create_evaluator

    def rate_evaluator(name: str) -> Callable[[dict, dict], dict]:
        def evaluate(output: dict, expected: dict) -> dict:
            return rate_evaluation(name, output, expected)

        return evaluate

    evaluators: dict[str, Callable[..., dict]] = {}
    for name in RATE_METRICS:
        kind = "LLM" if name in JUDGE_KIND else "CODE"
        evaluators[name] = create_evaluator(kind=kind, name=name)(rate_evaluator(name))
    evaluators["judge_persona"] = create_evaluator(kind="LLM", name="judge_persona")(persona_evaluation)
    return evaluators


# ---------------------------------------------------------------------------
# Harness JSON
# ---------------------------------------------------------------------------


def harness_doc(meta: dict[str, Any], cases: list[dict], runs: list[dict]) -> dict:
    """The experiment's runs in ``harness run``'s JSON format (what ``harness compare`` reads). Pure."""
    by_id = {c["id"]: c for c in cases}
    repeats: dict[str, int] = {}
    doc = {**meta, "runs": []}
    for out in runs:
        repeats[out["case"]] = repeats.get(out["case"], 0) + 1
        turns = [dict(t) for t in out["turns"]]
        for record, metrics in zip(turns, harness.grade_conversation(by_id[out["case"]]["turns"], turns)):
            record["metrics"] = metrics
        doc["runs"].append(
            {"case": out["case"], "repeat": repeats[out["case"]], "conversation_id": out["conversation_id"], "turns": turns}
        )
    doc["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["summary"] = harness.summarize(doc)
    return doc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def phoenix_client(url: str) -> Any:
    from phoenix.client import Client

    api_key = os.environ.get("PHOENIX_API_KEY", "").strip() or None
    return Client(base_url=url.rstrip("/").removesuffix("/v1/traces"), api_key=api_key)


def backend_info(url: str) -> dict:
    """What the backend under test reports on /health (deployed commit, provider)."""
    res = httpx.get(f"{url}/health", timeout=10)
    res.raise_for_status()
    return res.json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.evals.phoenix_experiment", description=__doc__.split("\n\n")[0])
    ap.add_argument("--phoenix-url", default=os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or DEFAULT_PHOENIX_URL)
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--cases", default=str(harness.CASES_FILE))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("upload", help="create the Phoenix dataset from cases.json")
    r = sub.add_parser("run", help="run an experiment against a backend")
    r.add_argument("--label", required=True)
    r.add_argument("--description", default="", help="what this run changes, shown in Phoenix (e.g. REASONING_EFFORT_FINAL=low)")
    r.add_argument("--url", default=os.environ.get("EVALS_URL", harness.DEFAULT_URL))
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--pace", type=float, default=21.0, help="seconds between requests (anonymous limit = 3/min; 0 for a local backend with RATE_LIMIT_DISABLED=1)")
    r.add_argument("--judge", action="store_true", help="grade replies with an LLM judge (Groq, costs tokens)")
    r.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    r.add_argument("--out", default=str(harness.DEFAULT_OUT))
    args = ap.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()  # GROQ_API_KEY for the judge, PHOENIX_* for the client
    cases = harness.load_cases(Path(args.cases))
    client = phoenix_client(args.phoenix_url)
    dataset = ensure_dataset(client, args.dataset, cases)
    if args.command == "upload":
        print(f"dataset {args.dataset}: {len(cases)} examples")
        return 0
    if args.repeat < 1:
        ap.error("--repeat must be >= 1")

    info = backend_info(args.url)
    meta = {
        "label": args.label,
        "description": args.description,
        "url": args.url,
        "backend_version": info.get("version"),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repeat": args.repeat,
        "pace_s": args.pace,
        "checks_version": checks.CHECKS_VERSION,
        "judge_model": args.judge_model if args.judge else None,
        "judge_version": JUDGE_VERSION if args.judge else None,
        "cases": [c["id"] for c in cases],
    }
    runs: list[dict] = []
    task = make_task(args.url, pacer=Pacer(args.pace), judge_model=args.judge_model if args.judge else None, runs=runs)
    experiment = client.experiments.run_experiment(
        dataset=dataset,
        task=task,
        evaluators=make_evaluators(),
        experiment_name=args.label,
        experiment_description=" · ".join(filter(None, [args.description, f"backend {info.get('version')} at {args.url}"])),
        experiment_metadata={k: v for k, v in meta.items() if k != "cases"},
        repetitions=args.repeat,
        timeout=TASK_TIMEOUT_S,
    )

    doc = harness_doc(meta, cases, runs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.label}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + harness.summary_table(doc) + f"\n\nsaved {out}")
    try:
        print("Phoenix:", client.experiments.get_experiment_url(dataset_id=experiment["dataset_id"], experiment_id=experiment["experiment_id"]))
    except Exception:  # noqa: BLE001 — the link is a convenience
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
