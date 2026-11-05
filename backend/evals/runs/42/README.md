# #42 grounding runs (temporary)

Harness JSON of the #42 experiments (`phoenix_experiment run --repeat 3 --judge`, dataset
`mcpokedex-cases-v2`), so `harness compare` works from any machine. Base:
`../../baselines/baseline-v2-91a257c.json` (prod 91a257c).

| file | commit | change | judge_grounded | grounded_facts | gate vs base |
|---|---|---|---|---|---|
| func-42c1-d8ebf43.json | d8ebf43 | narration only from the entry's data | 71.8% | 91.0% | 1 max_tokens |
| func-42c2-d67e596.json | d67e596 | + team defense/shared_weaknesses, abilities by name | 73.0% | 93.6% | exit 0 |
| func-42c4b-7004d3c.json | 7004d3c | + MCP feat/pokedex-defense-abilities (defense, Spanish abilities) | 75.7% | 93.5% | exit 0 |
| func-42c5-6236111.json | 6236111 | + opening-name guard, no form in step 2 | 71.6% | 91.2% | `completed` 109/111 (2 max_tokens) |
| func-42c6-6236111-120b.json | 6236111 | c5 with gpt-oss-120b on every anonymous call (local monkeypatch, not in the repo) | 93.6% | 91.2% (92.3% `--regrade`) | exit 0 |
| func-42c7-459e3ca-120b.json | 459e3ca | c6 + grounded_facts accepts "GIRATINA." for giratina-altered (`harness run`, no Phoenix) | 88.2% | 90.2% | exit 0 |
| func-42c8-bc8c006.json | bc8c006 | 120b for anonymous requests in the code + drop an opening no entry backs | 92.8% | 95.5% | exit 0 |

All with MCP `feat/pokedex-defense-abilities` f94aa1f from c4 on. `REASONING_EFFORT_FINAL=high` (c3)
was dropped: 48/111 turns hit max_tokens.

`adv-42c5-6236111.json`: c5 on `cases_adversarial.json` (57 turns) against
`../../baselines/baseline-adversarial-v3-10e4f3b.json`: compare exit 0, `no_prompt_leak` 57/57,
`no_tool_on_injection` 36/39 (same as base), `persona_kept` 33% → 90%, `judge_persona` 3.05 → 4.23.

Median per turn: base 5.7k tokens, 3.4 s; c5 6.5k, 2.2 s; c6 6.3k (4.6k cached), 2.7 s; c7 6.4k (4.6k
cached), 2.5 s; 2 LLM calls.

c7's 9 grounded_facts failures are real: 6 turns open with the Pokédex protocol after `analyze_team`
or `get_evolution_chain` (one for "DICERIO") and recall entry data (heights, weights, BST); the rest are
numbers computed or recalled in team analyses (491, 525) and stats (50). Use `compare --regrade` to grade older runs with the fixed check.

`python fails.py <json>` lists the failing turns with the judge's claims.

`adv-42c8-bc8c006.json` (c8 on `cases_adversarial.json`) against the v3 baseline: exit 0, `no_prompt_leak`
57/57, `no_tool_on_injection` 37/39, `persona_kept` 98%, `tool_expectation` 16/18 (noise, z 1.46).

c8 is the final candidate: `../../baselines/baseline-v2-bc8c006.json` and
`../../baselines/baseline-adversarial-v3-bc8c006.json`.

Delete this directory before merging: only the final candidate becomes a baseline.
