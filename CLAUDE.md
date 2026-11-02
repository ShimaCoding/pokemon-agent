# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pokémon MCP Agent — a FastAPI web app that uses the Strands Agents SDK to answer Pokémon questions as Dexter, Professor Oak's Pokédex. The agent combines:
- **Remote MCP tools** from the MCPokédex server (`mcp.mcpokedex.com/mcp`, override with `MCP_SERVER_URL`): `get_pokedex_entry`, `analyze_pokemon_stats`, `analyze_team`, `get_type_effectiveness`, `search_pokemon`, etc. There are no local Pokémon tools.
- The **`dexter-pokedex-narrator` skill** (`backend/skills/`), loaded via the Strands `AgentSkills` plugin, with the narration protocol for Pokédex entries.

LLM requests are routed through LiteLLM with automatic provider fallback (Groq → Gemini → OpenAI). The browser receives both tool traces and final answers in real time via SSE.

## Running the App

```bash
# Local development (make setup once; deps go to .venv via uv)
make setup
make backend    # PORT=8010 to change the port
make frontend   # Vite on :5173, proxies to BACKEND_URL (default :8000)

# Docker (production-like, port 8002)
docker-compose up --build
```

Requires at least one API key in `.env` (see `.env.example`):
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`

## Desarrollo con Conductor

`.conductor/settings.toml` makes each Conductor workspace self-contained (`run_mode = "concurrent"`):

- Ports: backend on `$CONDUCTOR_PORT`, Vite on `$CONDUCTOR_PORT+1`. Vite proxies `/api`, `/providers`, `/config`, `/health` to `BACKEND_URL` (defaults to `http://localhost:8000`, so `make frontend` is unchanged).
- `setup`: `uv venv` (Python 3.11) + `uv pip install -r requirements.txt` into the workspace `.venv`, then `npm ci --prefix frontend`. Commands pass `--python .venv/bin/python` because an inherited `VIRTUAL_ENV` may point at another checkout's venv.
- Run scripts: `dev` (default, local only; backend + Vite via `concurrently -k`), `dev-local-mcp` (same, with `MCP_SERVER_URL=$LOCAL_MCP_URL`), `test` (`pytest backend/tests` via `uv run --with pytest`).
- To use a local MCP server (e.g. one running in another workspace), set `MCP_SERVER_URL=http://127.0.0.1:<port>/mcp` in the workspace `.env`.
- SQLite (`DB_PATH=data/queries.db`) is relative, so each workspace has its own DB.

## Architecture

The backend modules:

- **`backend/providers.py`** — `PROVIDERS` dict with model IDs and env vars per provider; `build_litellm_router()` builds a LiteLLM Router with fallback ordering; all models aliased to `"agent-model"` so the Strands agent doesn't need provider-aware logic.

- **`backend/tools.py`** — Empty placeholder. All Pokémon tools (including `get_pokedex_entry`) come from the remote MCP server.

- **`backend/skills/dexter-pokedex-narrator/SKILL.md`** — Narration protocol for Pokédex answers. The system prompt tells the model to activate it when the user asks about a Pokémon, so loading it costs an extra LLM call/tool round-trip.

- **`backend/database.py`** — SQLite query log (`DB_PATH`, default `data/queries.db`) backing the `/admin` stats/queries endpoints.

- **`backend/agent.py`** — `build_agent(provider_name)` creates a LiteLLMModel and MCPClient, then **monkey-patches `litellm.completion` and `litellm.acompletion` globally** to route through the Router. This is how fallback is achieved without modifying the Strands SDK. `create_agent()` wraps model + MCP tools + the Dexter system prompt + the `AgentSkills` plugin in a Strands Agent.

- **`backend/main.py`** — FastAPI app with lifespan validation. The `/api/agent/run` POST endpoint streams SSE events (schema documented in `_sse_generator`): `start`, `llm_call`, `model_attempt`, `tool_call`, `tool_result`, `text`, `done`, `error`. Also exposes `/api/tools`, `/api/prompts`, `/api/resources` (MCP listings) and `/admin`. The built frontend in `backend/static/` is mounted at `/`.

The frontend (`frontend/`) is React 19 + TypeScript + Vite. `npm run build` outputs to `backend/static/` (committed, regenerate after frontend changes). Components live in `frontend/src/components/` (`DexterConsole`, `PokedexViewer`, etc.) and consume the SSE stream in real time.

## Key Design Decisions

**Monkey-patching litellm**: The Router fallback works by globally replacing `litellm.completion`/`litellm.acompletion` at agent-build time. This is correct for a single-worker server but would break under concurrent multi-worker load, since the patch is global.

**MCP client lifecycle**: A new MCPClient and agent are instantiated per request (`build_agent` is called inside `_sse_generator`). The synchronous `list_tools_sync()` call adds ~100–300 ms latency per request.

**SSE event schema**: Each event is `data: <json>\n\n`. The `type` field distinguishes event kinds; `tool_call`/`tool_result` share an `index` so the frontend can match calls with their results.
