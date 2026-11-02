"""
Offline eval harness: the quality gate for prompt, model and context changes (#16–#18).

    uv run python -m backend.evals.harness run --label dev-base [--url http://127.0.0.1:8765]
        [--repeat 3] [--pace 21] [--only pokedex followup] [--out .context/evals]
        [--judge [--judge-model groq/openai/gpt-oss-120b]] [--submit]
    uv run python -m backend.evals.harness compare .context/evals/dev-base.json .context/evals/perf-16.json
        [--gate-judge] [--regrade]

``run`` is a plain SSE client of ``POST /api/agent/run``: start any branch's backend
(anonymous, so the 20b model as in prod) and point ``--url`` at it. Every case in
``cases.json`` runs ``--repeat`` times, each in a new conversation, with ``--pace``
seconds between requests (anonymous limit: 3 req/min). Each turn is graded by:

- the case expectations: ``tool_expectation`` (required tools must succeed, at least
  one any-of tool must run, no forbidden tool may run; ``skills``/``retrieve_context``
  ignored), ``followup_args``, ``reply_contains`` and
  ``no_invented_entry`` (unknown Pokémon: no Pokédex opening in any case, no narration
  of a different Pokémon looked up instead, numbers grounded);
- the online rule checks (``checks.evaluate_turn``) with this conversation's tool
  results and user texts as evidence (independent of server-side compaction);
- with ``--judge``, the LLM judge (``judge.py``): ``judge_persona`` 1–5, ``judge_grounded``.

LLM sampling is nondeterministic, so ``compare`` gates pass *rates*, not single turns
(see ``RATE_GATES``/``MEAN_GATES``), and exits 1 on any regression. A drop counts only
when it is beyond the tolerance *and* statistically meaningful (z > 1.96); the safety
metrics ``completed`` and ``no_invented_entry`` flag any drop. Judge metrics are
reported but gate only with ``--gate-judge``. Every metric prints its z and reason.
``--regrade`` re-applies the current checks to both stored runs first (e.g. after a
check fix); judge verdicts are kept as recorded.

``--submit`` sends the judge results to Datadog LLM Observability as evaluations on
each turn's agent span (``done.trace_id``/``span_id``, present when the backend runs
with ``DD_API_KEY``). It refuses the prod ml_app in the harness's own environment and
any non-loopback ``--url``. Evaluations join spans by id only, so the guard cannot see
which ml_app the target backend uses: start it with ``DD_LLMOBS_ML_APP=mcpokedexagent-dev``
too (without it the backend reports to the prod ml_app). Rule checks are already
submitted by the backend itself.

Output: ``<out>/<label>.json`` and a markdown summary on stdout.

``pokemon_species.txt`` (used by ``checks.species_in``) was generated with:
    curl -s 'https://pokeapi.co/api/v2/pokemon-species?limit=2000' | jq -r '.results[].name'
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from backend.evals import checks
from backend.evals.judge import DEFAULT_JUDGE_MODEL, judge_turn

CASES_FILE = Path(__file__).with_name("cases.json")
DEFAULT_OUT = Path(".context/evals")
DEFAULT_URL = "http://127.0.0.1:8765"

# Pass-rate metrics → allowed drop in percentage points before it is a regression.
# Deterministic expectations and tool use get no tolerance (#17's "no Pokédex answer
# without a tool"); heuristic rule checks and the judge get 10 pp.
RATE_GATES: dict[str, float] = {
    "completed": 0.0,
    "tool_expectation": 0.0,
    "followup_args": 0.0,
    "reply_contains": 0.0,
    "no_invented_entry": 0.0,
    "pokemon_tool_used": 0.0,
    "dexter_persona": 10.0,
    "grounded_facts": 10.0,
    "judge_grounded": 10.0,
}
# Mean metrics → allowed drop of the mean.
MEAN_GATES: dict[str, float] = {"judge_persona": 0.5}
# Only present when --judge ran; skipped (not failed) if either side lacks them.
OPTIONAL_METRICS = frozenset({"judge_grounded", "judge_persona"})
# A drop beyond the tolerance is a regression only if it is also beyond sampling noise:
# at n = 36 a pass rate has a standard error of about 8 pp, so 1 of 27 follow-ups or a
# 10 pp judge swing between two runs of the same code is noise. Two-proportion z-test
# (pooled) for rates, Welch z for means; one-sided drops beyond z = 1.96.
Z_CRITICAL = 1.96
# Safety metrics: any drop is a regression, however small the sample.
STRICT_METRICS = frozenset({"completed", "no_invented_entry"})
# The judge is an LLM with its own variance: reported, gating only with --gate-judge.
JUDGE_METRICS = frozenset({"judge_grounded", "judge_persona"})

PERF_FIELDS = ("elapsed_ms", "ttft_ms", "llm_calls", "input_tokens", "output_tokens", "total_tokens", "cached_tokens")

PROD_ML_APPS = frozenset({"mcpokedexagent"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


# ---------------------------------------------------------------------------
# SSE client
# ---------------------------------------------------------------------------


def _looks_like_error(text: str) -> bool:
    """SSE tool_result carries no status; MCP errors read like "❌ Pokémon 'x' not found"."""
    head = text.strip()[:200].lower()
    return head.startswith(("❌", "error")) or "not found" in head


def _parse_sse(lines: Any) -> Any:
    """Yield event dicts from an iterator of SSE text chunks."""
    buf = ""
    for chunk in lines:
        buf += chunk
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            frame = frame.strip()
            if not frame.startswith("data:"):
                continue
            try:
                yield json.loads(frame[5:].strip())
            except json.JSONDecodeError:
                continue


def record_from_events(query: str, events: list[dict], client_ms: dict[str, int | None]) -> dict:
    """One turn's record from its SSE events (pure; the client only adds timings)."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    reply: list[str] = []
    for evt in events:
        kind = evt.get("type")
        if kind == "tool_call":
            uid = evt.get("tool_id") or f"#{evt.get('index')}"
            calls[uid] = {"name": evt.get("tool", "unknown"), "args": evt.get("args") or {}, "result": "", "error": False}
            order.append(uid)
        elif kind == "tool_result":
            uid = evt.get("tool_id") or f"#{evt.get('index')}"
            call = calls.setdefault(uid, {"name": evt.get("tool", "unknown"), "args": {}, "result": "", "error": False})
            if uid not in order:
                order.append(uid)
            call["result"] = str(evt.get("result", ""))
            call["error"] = evt.get("status") == "error" or _looks_like_error(call["result"])
        elif kind == "text":
            reply.append(evt.get("delta", ""))
    done = next((e for e in events if e.get("type") == "done"), {})
    error = next((e for e in events if e.get("type") == "error"), None)
    status = "error" if error or not done else "done"
    record = {
        "query": query,
        "status": status,
        "error": (error or {}).get("message") or (None if done else "stream ended without done"),
        "reply": "".join(reply),
        "tools": [calls[uid] for uid in order],
        "trace_id": done.get("trace_id"),
        "span_id": done.get("span_id"),
        "models": done.get("models_tried"),
        "client_ttft_ms": client_ms.get("ttft"),
        "client_elapsed_ms": client_ms.get("elapsed"),
    }
    record.update({f: done.get(f) for f in PERF_FIELDS})
    return record


def run_turn(client: httpx.Client, url: str, conversation_id: str, query: str, *, retries: int = 2) -> dict:
    """POST one query and collect its SSE stream. Retries a 429 after Retry-After."""
    for attempt in range(retries + 1):
        events: list[dict] = []
        t0 = time.time()
        first_text: int | None = None
        with client.stream(
            "POST",
            f"{url}/api/agent/run",
            json={"query": query, "conversation_id": conversation_id},
            timeout=180,
        ) as res:
            if res.status_code == 429 and attempt < retries:
                wait = float(res.headers.get("Retry-After", "60"))
                print(f"  429 rate limited, waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            if res.status_code != 200:
                body = res.read().decode(errors="replace")
                return {"query": query, "status": f"http_{res.status_code}", "error": body[:300], "reply": "", "tools": []}
            for evt in _parse_sse(res.iter_text()):
                if evt.get("type") == "text" and first_text is None:
                    first_text = int((time.time() - t0) * 1000)
                events.append(evt)
        elapsed = int((time.time() - t0) * 1000)
        return record_from_events(query, events, {"ttft": first_text, "elapsed": elapsed})
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _metric(passed: bool, reasoning: str, **metadata: Any) -> dict:
    return {"passed": bool(passed), "reasoning": reasoning, **({"metadata": metadata} if metadata else {})}


def observations(record: dict) -> list[checks.ToolObservation]:
    return [
        checks.ToolObservation(name=t["name"], args=t.get("args") or {}, text=t.get("result", ""), error=bool(t.get("error")))
        for t in record.get("tools", [])
    ]


# The reply says the asked-for Pokémon does not exist ("no existe", "no se encuentra", …).
_DENIAL_RE = re.compile(
    r"\bno\s+(?:existe|se\s+encuentr|se\s+encontr|figura|aparece|corresponde|consta|reconoce|est[aá]\s+registrad|hay\s+(?:ning|registro))"
    r"|\binexistente|\bnot\s+found",
    re.I,
)


def substituted_entry(fake: str, tools: list[checks.ToolObservation], reply: str) -> str | None:
    """A real Pokémon looked up instead of ``fake`` (e.g. pikachuzard → pikachu) whose entry the reply narrates.

    Narrating means naming its genus ("el Pokémon Ratón") or talking about it without
    ever saying that ``fake`` does not exist. "No existe; ¿quizá Pikachu?" is fine.
    """
    norm_reply = checks.normalize(reply)
    for tool in tools:
        if tool.error or tool.name not in ("get_pokedex_entry", "get_pokemon_info"):
            continue
        try:
            data = json.loads(tool.text)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            continue
        name = checks.normalize(data["name"])
        if name == checks.normalize(fake):
            continue
        genus = checks.normalize(data.get("genus") or "")
        if genus and f" pokemon {genus.removeprefix('pokemon ')} " in f" {norm_reply} ":
            return name
        if f" {name} " in f" {norm_reply} " and not _DENIAL_RE.search(reply):
            return name
    return None


def grade_turn(spec: dict, record: dict, evidence: list[str]) -> dict[str, dict]:
    """Applicable metrics for one turn: case expectations + rule checks."""
    out = {"completed": _metric(record["status"] == "done", record.get("error") or "ok")}
    if record["status"] != "done":
        return out

    tools = observations(record)
    reply = record.get("reply", "")
    ran = {t.name for t in tools if t.name not in checks.SCAFFOLDING_TOOLS}
    ok_tools = {t.name for t in tools if not t.error and t.name not in checks.SCAFFOLDING_TOOLS}

    required = set(spec.get("required_tools", []))
    any_of = set(spec.get("any_of_tools", []))
    forbidden = set(spec.get("forbidden_tools", []))
    if required or any_of or forbidden:
        problems = []
        if missing := sorted(required - ok_tools):
            problems.append("faltó " + ", ".join(missing))
        if any_of and not any_of & ran:
            problems.append("ninguna de " + ", ".join(sorted(any_of)))
        if hit := sorted(forbidden & ran):
            problems.append("prohibidas " + ", ".join(hit))
        out["tool_expectation"] = _metric(not problems, "; ".join(problems) or "ok", tools=sorted(ran))

    for tool, needle in (spec.get("expect_args") or {}).items():
        args = [json.dumps(t.args, ensure_ascii=False) for t in tools if t.name == tool]
        passed = any(checks.normalize(needle) in checks.normalize(a) for a in args)
        out["followup_args"] = _metric(passed, "ok" if passed else f"{tool} sin '{needle}' en {args or 'ninguna llamada'}")

    if wanted := spec.get("reply_contains"):
        norm = checks.normalize(reply)
        missing = [w for w in wanted if checks.normalize(w) not in norm]
        out["reply_contains"] = _metric(not missing, "ok" if not missing else "falta " + ", ".join(missing))

    turn = checks.Turn(query=record["query"], reply=reply, tools=tools, evidence=evidence)
    for r in checks.evaluate_turn(turn):
        out[r.label] = _metric(r.passed, r.reasoning, **r.metadata)

    if fake := spec.get("unknown_pokemon"):
        opening = checks.entry_opening(reply)
        grounded = out.get("grounded_facts", {"passed": True})
        problems = []
        if opening:
            problems.append(f"narró una ficha ({opening.group(0)[:60]})")
        elif substitute := substituted_entry(fake, tools, reply):
            problems.append(f"narró la ficha de {substitute} como si fuera {fake}")
        if not grounded["passed"]:
            problems.append(grounded["reasoning"])
        out["no_invented_entry"] = _metric(not problems, "; ".join(problems) or f"no inventó datos de {fake}")
    return out


def judge_metric(verdict: dict) -> dict:
    """The judge's grounded verdict as a pass rate like the rule checks (the persona score is a mean)."""
    return _metric(verdict["grounded"], verdict["reasoning"], unsupported_claims=verdict["unsupported_claims"])


def regrade(doc: dict, cases: list[dict]) -> dict:
    """A stored run re-graded with the current checks and ``cases`` (judge verdicts kept as recorded). Pure.

    Lets ``compare`` apply a fixed check to runs recorded before the fix.
    """
    doc = copy.deepcopy(doc)
    by_id = {c["id"]: c for c in cases}
    for run_doc in doc["runs"]:
        if run_doc["case"] not in by_id:
            raise SystemExit(f"{doc['label']}: case {run_doc['case']!r} is not in the cases file")
        evidence: list[str] = []
        for spec, record in zip(by_id[run_doc["case"]]["turns"], run_doc["turns"]):
            if record["status"] == "done":
                evidence.extend(evidence_texts(record))
            record["metrics"] = grade_turn(spec, record, list(evidence))
            if verdict := record.get("judge"):
                record["metrics"]["judge_grounded"] = judge_metric(verdict)
    doc["summary"] = summarize(doc)
    return doc


def evidence_texts(record: dict) -> list[str]:
    """What this turn adds to the conversation's evidence: the user text and data tool results."""
    tool_texts = [t.get("result", "") for t in record.get("tools", []) if t["name"] not in checks.SCAFFOLDING_TOOLS]
    return [record["query"], *tool_texts]


# ---------------------------------------------------------------------------
# Summary and compare
# ---------------------------------------------------------------------------


def _turns(doc: dict) -> list[tuple[str, int, dict]]:
    return [(run["case"], i, turn) for run in doc.get("runs", []) for i, turn in enumerate(run["turns"], 1)]


def summarize(doc: dict) -> dict:
    rates: dict[str, dict[str, Any]] = {}
    means: dict[str, list[float]] = {}
    judge_errors = 0
    perf: dict[str, dict[str, list[float]]] = {}
    for case, i, turn in _turns(doc):
        for name, result in turn.get("metrics", {}).items():
            slot = rates.setdefault(name, {"passed": 0, "total": 0})
            slot["total"] += 1
            slot["passed"] += int(result["passed"])
        if "judge" in turn:
            verdict = turn["judge"]
            if verdict is None:
                judge_errors += 1
            else:
                means.setdefault("judge_persona", []).append(verdict["persona"])
        if turn.get("status") == "done":
            slot = perf.setdefault(f"{case}/{i}", {})
            for f in PERF_FIELDS:
                if isinstance(turn.get(f), (int, float)):
                    slot.setdefault(f, []).append(turn[f])
    for slot in rates.values():
        slot["rate"] = round(100 * slot["passed"] / slot["total"], 1) if slot["total"] else None
    all_perf: dict[str, list[float]] = {}
    for slot in perf.values():
        for f, values in slot.items():
            all_perf.setdefault(f, []).extend(values)
    median = lambda d: {f: statistics.median(v) for f, v in d.items() if v}  # noqa: E731
    return {
        "rates": dict(sorted(rates.items())),
        "means": {
            k: {"mean": round(statistics.mean(v), 2), "n": len(v), "sd": round(statistics.stdev(v), 2) if len(v) > 1 else None}
            for k, v in means.items()
        },
        "judge_errors": judge_errors,
        "perf_median": median(all_perf),
        "perf_median_by_turn": {k: median(v) for k, v in perf.items()},
    }


def two_proportion_z(b_passed: int, b_total: int, c_passed: int, c_total: int) -> float:
    """z of the drop from base to candidate (positive = candidate worse), pooled standard error."""
    pooled = (b_passed + c_passed) / (b_total + c_total)
    se = math.sqrt(pooled * (1 - pooled) * (1 / b_total + 1 / c_total))
    if se == 0:  # both sides at 0 % or both at 100 %: nothing dropped
        return 0.0
    return (b_passed / b_total - c_passed / c_total) / se


def mean_z(b: dict, c: dict) -> float | None:
    """Welch z of the drop between two means ({mean, n, sd}); None without both standard deviations."""
    if b.get("sd") is None or c.get("sd") is None:
        return None
    se = math.sqrt(b["sd"] ** 2 / b["n"] + c["sd"] ** 2 / c["n"])
    if se == 0:
        return math.inf if b["mean"] > c["mean"] else 0.0
    return (b["mean"] - c["mean"]) / se


@dataclass(frozen=True)
class GateResult:
    """One metric of a compare: ``regression`` is what makes the gate exit 1."""

    metric: str
    base: float | None
    candidate: float | None
    allowed_drop: float
    detail: str
    z: float | None = None
    gating: bool = True
    regression: bool = False


def _summary(doc: dict) -> dict:
    # Recomputed from the turns when present: older docs lack the judge persona sd.
    return summarize(doc) if doc.get("runs") else doc["summary"]


def evaluate(base: dict, cand: dict, *, gate_judge: bool = False) -> list[GateResult]:
    """Every gated metric of a candidate run against a base run (both full harness JSON docs). Pure.

    A drop is a regression when it exceeds the metric's tolerance *and* is statistically
    meaningful (z > ``Z_CRITICAL``); ``STRICT_METRICS`` flag any drop. Judge metrics are
    reported but only gate with ``gate_judge``.
    """
    b, c = _summary(base), _summary(cand)
    results: list[GateResult] = []
    for metric, allowed in RATE_GATES.items():
        bs, cs = b["rates"].get(metric), c["rates"].get(metric)
        if not bs or not bs["total"]:
            continue
        gating = metric not in JUDGE_METRICS or gate_judge
        if not cs or not cs["total"]:
            missing = metric not in OPTIONAL_METRICS
            detail = "missing in candidate" if missing else "skipped: not in candidate"
            results.append(GateResult(metric, bs["rate"], None, allowed, detail, gating=gating, regression=missing and gating))
            continue
        drop = 100 * (bs["passed"] / bs["total"] - cs["passed"] / cs["total"])
        z = two_proportion_z(bs["passed"], bs["total"], cs["passed"], cs["total"])
        delta = f"{0.0 - drop:+.1f} pp"
        if metric in STRICT_METRICS:
            flagged = drop > 1e-9
            detail = f"{delta}, strict: any drop" if flagged else delta
        elif drop <= allowed + 1e-9:
            flagged, detail = False, f"{delta}, within {allowed:g} pp"
        elif z <= Z_CRITICAL:
            flagged, detail = False, f"{delta}, noise: z {z:.2f} ≤ {Z_CRITICAL}"
        else:
            flagged, detail = True, f"{delta}, z {z:.2f} > {Z_CRITICAL}"
        if flagged and not gating:
            detail += " (judge: not gating)"
        results.append(GateResult(metric, bs["rate"], cs["rate"], allowed, detail, z, gating, flagged and gating))
    for metric, allowed in MEAN_GATES.items():
        bm, cm = b["means"].get(metric), c["means"].get(metric)
        if not bm or not cm:
            continue
        gating = metric not in JUDGE_METRICS or gate_judge
        drop = bm["mean"] - cm["mean"]
        z = mean_z(bm, cm)
        delta = f"{0.0 - drop:+.2f}"
        if drop <= allowed + 1e-9:
            flagged, detail = False, f"{delta}, within {allowed:g}"
        elif z is None:  # no spread known: the tolerance alone decides
            flagged, detail = True, f"{delta}, no sd: tolerance only"
        elif z <= Z_CRITICAL:
            flagged, detail = False, f"{delta}, noise: z {z:.2f} ≤ {Z_CRITICAL}"
        else:
            flagged, detail = True, f"{delta}, z {z:.2f} > {Z_CRITICAL}"
        if flagged and not gating:
            detail += " (judge: not gating)"
        results.append(GateResult(metric, bm["mean"], cm["mean"], allowed, detail, z, gating, flagged and gating))
    return results


def compare(base: dict, cand: dict, *, gate_judge: bool = False) -> list[GateResult]:
    """The regressions only (empty = the candidate passes the gate)."""
    return [r for r in evaluate(base, cand, gate_judge=gate_judge) if r.regression]


def _rate_cell(summary: dict, metric: str) -> str:
    slot = summary["rates"].get(metric)
    if not slot:
        return "—"
    return f"{slot['rate']}% ({slot['passed']}/{slot['total']})"


def _gate_cell(metric: str, allowed: float, gating: bool, unit: str) -> str:
    if not gating:
        return "info only (--gate-judge)"
    if metric in STRICT_METRICS:
        return "strict: any drop"
    return f"drop > {allowed:g}{unit} and z > {Z_CRITICAL}"


def compare_table(base: dict, cand: dict, results: list[GateResult]) -> str:
    b, c = _summary(base), _summary(cand)
    lines = [
        f"| metric | {base['label']} | {cand['label']} | z | gate | result |",
        "|---|---|---|---|---|---|",
    ]
    verdict = lambda r: ("REGRESSION" if r.regression else "ok") + f": {r.detail}"  # noqa: E731
    zcell = lambda r: "—" if r.z is None else ("inf" if math.isinf(r.z) else f"{r.z:.2f}")  # noqa: E731
    for r in results:
        if r.metric in MEAN_GATES:
            cell = lambda m: f"{m['mean']} (n={m['n']}, sd={m.get('sd', '—')})" if m else "—"  # noqa: E731
            base_cell, cand_cell = cell(b["means"].get(r.metric)), cell(c["means"].get(r.metric))
            unit = ""
        else:
            base_cell, cand_cell = _rate_cell(b, r.metric), _rate_cell(c, r.metric)
            unit = " pp"
        lines.append(f"| {r.metric} | {base_cell} | {cand_cell} | {zcell(r)} | {_gate_cell(r.metric, r.allowed_drop, r.gating, unit)} | {verdict(r)} |")
    lines += ["", "| perf (median, info only) | base | candidate |", "|---|---|---|"]
    for f in PERF_FIELDS:
        lines.append(f"| {f} | {b['perf_median'].get(f, '—')} | {c['perf_median'].get(f, '—')} |")
    return "\n".join(lines)


def summary_table(doc: dict) -> str:
    s = doc["summary"]
    lines = [f"## {doc['label']}", "", "| metric | pass rate |", "|---|---|"]
    lines += [f"| {m} | {_rate_cell(s, m)} |" for m in s["rates"]]
    lines += [f"| {m} (mean) | {v['mean']} (n={v['n']}) |" for m, v in s["means"].items()]
    if s["judge_errors"]:
        lines.append(f"| judge errors | {s['judge_errors']} |")
    lines += ["", "| case/turn | " + " | ".join(PERF_FIELDS) + " |", "|---|" + "---|" * len(PERF_FIELDS)]
    for key, med in s["perf_median_by_turn"].items():
        lines.append(f"| {key} | " + " | ".join(str(med.get(f, "—")) for f in PERF_FIELDS) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Datadog submission (judge results only)
# ---------------------------------------------------------------------------


def submit_refusal(env: dict[str, str], url: str) -> str | None:
    """Why --submit must not run with this environment and target (None when it may)."""
    if (urlsplit(url).hostname or "").lower() not in LOOPBACK_HOSTS:
        return f"--submit only targets a local backend (got {url}); deployed spans belong to the prod ml_app"
    ml_app = env.get("DD_LLMOBS_ML_APP", "").strip()
    if not ml_app:
        return "--submit needs DD_LLMOBS_ML_APP (e.g. mcpokedexagent-dev)"
    if ml_app.lower() in PROD_ML_APPS:
        return f"--submit refuses the prod ml_app {ml_app!r}; use mcpokedexagent-dev"
    if not env.get("DD_API_KEY", "").strip():
        return "--submit needs DD_API_KEY"
    return None


class _Submitter:
    """Judge verdicts → LLMObs evaluations on the turn's agent span (dev ml_app only)."""

    def __init__(self, label: str, model: str) -> None:
        from backend import telemetry

        for name, value in telemetry._DDTRACE_DEFAULTS.items():
            os.environ.setdefault(name, value)
        from ddtrace.llmobs import LLMObs

        self._llmobs = LLMObs
        self._tags = {"eval_source": "judge", "judge_model": model, "harness_label": label}
        LLMObs.enable(
            ml_app=os.environ["DD_LLMOBS_ML_APP"],
            api_key=os.environ["DD_API_KEY"],
            site=os.environ.get("DD_SITE", "datadoghq.com"),
            agentless_enabled=True,
            integrations_enabled=False,
            env=os.environ.get("DD_ENV", "dev"),
        )
        self.sent = 0

    def submit(self, record: dict, verdict: dict) -> None:
        if not record.get("span_id") or not record.get("trace_id"):
            return
        span = {"span_id": record["span_id"], "trace_id": record["trace_id"]}
        self._llmobs.submit_evaluation(
            label="judge_persona",
            metric_type="score",
            value=verdict["persona"],
            span=span,
            assessment="pass" if verdict["persona"] >= 4 else "fail",
            reasoning=verdict["reasoning"],
            tags=self._tags,
        )
        self._llmobs.submit_evaluation(
            label="judge_grounded",
            metric_type="boolean",
            value=verdict["grounded"],
            span=span,
            assessment="pass" if verdict["grounded"] else "fail",
            reasoning=verdict["reasoning"],
            metadata={"unsupported_claims": verdict["unsupported_claims"]},
            tags=self._tags,
        )
        self.sent += 1

    def close(self) -> None:
        self._llmobs.flush()
        self._llmobs.disable()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_cases(path: Path, only: list[str] | None = None) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    if only:
        unknown = set(only) - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in only]
    return cases


def run(args: argparse.Namespace, *, send: Callable[..., dict] = run_turn, sleep: Callable[[float], None] = time.sleep) -> dict:
    cases = load_cases(Path(args.cases), args.only)
    submitter = _Submitter(args.label, args.judge_model) if args.submit else None
    doc: dict[str, Any] = {
        "label": args.label,
        "url": args.url,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repeat": args.repeat,
        "pace_s": args.pace,
        "checks_version": checks.CHECKS_VERSION,
        "judge_model": args.judge_model if args.judge else None,
        "cases": [c["id"] for c in cases],
        "runs": [],
    }
    first = True
    try:
        with httpx.Client() as client:
            if send is run_turn:
                client.get(f"{args.url}/health", timeout=10).raise_for_status()
            for rep in range(1, args.repeat + 1):
                for case in cases:
                    conversation_id = str(uuid.uuid4())
                    run_doc = {"case": case["id"], "repeat": rep, "conversation_id": conversation_id, "turns": []}
                    doc["runs"].append(run_doc)
                    evidence: list[str] = []  # user texts + data tool results of this conversation
                    tool_texts: list[str] = []  # data tool results only (judge input)
                    for i, spec in enumerate(case["turns"], 1):
                        if not first and args.pace:
                            sleep(args.pace)
                        first = False
                        record = send(client, args.url, conversation_id, spec["q"])
                        if record["status"] == "done":
                            evidence.extend(evidence_texts(record))
                            tool_texts.extend(evidence_texts(record)[1:])
                        record["metrics"] = grade_turn(spec, record, list(evidence))
                        if args.judge and record["status"] == "done":
                            record["judge"] = judge_turn(
                                spec["q"],
                                record["reply"],
                                [t for t in tool_texts if t],
                                model=args.judge_model,
                                focus=spec.get("judge_focus"),
                            )
                            if verdict := record["judge"]:
                                record["metrics"]["judge_grounded"] = judge_metric(verdict)
                                if submitter:
                                    submitter.submit(record, verdict)
                        run_doc["turns"].append(record)
                        failed = [m for m, r in record["metrics"].items() if not r["passed"]]
                        judge = record.get("judge")
                        judged = f" judge={judge['persona']}/{judge['grounded']}" if judge else ""
                        print(
                            f"[{rep}/{args.repeat} {case['id']} t{i}] {spec['q']!r:<36} {record['status']} "
                            f"llm={record.get('llm_calls')} elapsed={record.get('elapsed_ms')}ms "
                            f"tools={[t['name'] for t in record['tools']]} "
                            f"{'FAIL ' + ','.join(failed) if failed else 'ok'}{judged}",
                            flush=True,
                        )
    finally:
        if submitter:
            submitter.close()
            print(f"submitted judge evals for {submitter.sent} turns", flush=True)
    doc["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["summary"] = summarize(doc)
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.evals.harness", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the labeled cases against a backend")
    r.add_argument("--label", required=True)
    r.add_argument("--url", default=os.environ.get("EVALS_URL", DEFAULT_URL))
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--pace", type=float, default=21.0, help="seconds between requests (anonymous limit = 3/min)")
    r.add_argument("--only", nargs="*", help="subset of case ids")
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--cases", default=str(CASES_FILE))
    r.add_argument("--judge", action="store_true", help="grade replies with an LLM judge (Groq, costs tokens)")
    r.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    r.add_argument("--submit", action="store_true", help="send judge results to Datadog (never the prod ml_app)")

    c = sub.add_parser("compare", help="gate a candidate run against a base run (exit 1 on regression)")
    c.add_argument("base")
    c.add_argument("candidate")
    c.add_argument("--gate-judge", action="store_true", help="judge metrics gate too (default: reported only)")
    c.add_argument("--regrade", action="store_true", help="re-grade both runs with the current checks and --cases first")
    c.add_argument("--cases", default=str(CASES_FILE))

    args = ap.parse_args(argv)

    if args.command == "compare":
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        if args.regrade:
            cases = load_cases(Path(args.cases))
            base, cand = regrade(base, cases), regrade(cand, cases)
            print(f"(both runs re-graded with the current checks, {checks.CHECKS_VERSION}; judge verdicts as recorded)\n")
        results = evaluate(base, cand, gate_judge=args.gate_judge)
        print(compare_table(base, cand, results))
        regressions = [r for r in results if r.regression]
        for reg in regressions:
            print(f"REGRESSION {reg.metric}: {reg.base} → {reg.candidate} ({reg.detail})", file=sys.stderr)
        return 1 if regressions else 0

    from dotenv import load_dotenv

    load_dotenv()  # GROQ_API_KEY for the judge
    if args.submit:
        if not args.judge:
            ap.error("--submit sends judge results; add --judge")
        if reason := submit_refusal(dict(os.environ), args.url):
            print(reason, file=sys.stderr)
            return 2
    if args.repeat < 1:
        ap.error("--repeat must be >= 1")

    doc = run(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.label}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + summary_table(doc) + f"\n\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
