# #42 grounding runs (temporary)

Harness JSON of the #42 experiments (`phoenix_experiment run --repeat 3 --judge`, dataset
`mcpokedex-cases-v2`), so `harness compare` works from any machine. Base:
`../../baselines/baseline-v2-91a257c.json` (prod 91a257c).

| file | commit | change | judge_grounded | grounded_facts |
|---|---|---|---|---|
| func-42c1-d8ebf43.json | d8ebf43 | narration only from the entry's data | 71.8% | 91.0% |
| func-42c2-d67e596.json | d67e596 | + team defense/shared_weaknesses, abilities by name | 73.0% | 93.6% |
| func-42c4b-7004d3c.json | 7004d3c | + MCP feat/pokedex-defense-abilities (defense, Spanish abilities) | 75.7% | 93.5% |

`REASONING_EFFORT_FINAL=high` (c3) was dropped: 48/111 turns hit max_tokens. c5 (6236111, opening-name
guard) has no run yet. `python fails.py <json>` lists the failing turns with the judge's claims.

Delete this directory before merging: only the final candidate becomes a baseline.
