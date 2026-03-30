# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pokémon MCP Agent — a FastAPI web app that uses the Strands Agents SDK to answer Pokémon questions. The agent combines:
- A **local Strands tool** (`get_pokedex_entry` in `backend/tools.py`) that fetches Pokédex entries directly from the PokeAPI (types, stats, abilities, flavor text in Spanish).
- An **MCP server** (`mcpokedex.com/mcp`) for additional Pokémon data.

LLM requests are routed through LiteLLM with automatic provider fallback (Groq → Gemini → OpenAI). The browser receives both tool traces and final answers in real time via SSE.

## Running the App

```bash
# Local development
python -m uvicorn backend.main:app --reload

# Docker (production-like, port 8002)
docker-compose up --build
```

Requires at least one API key in `.env` (see `.env.example`):
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`

## Architecture

The backend has four modules:

- **`backend/providers.py`** — `PROVIDERS` dict with model IDs and env vars per provider; `build_litellm_router()` builds a LiteLLM Router with fallback ordering; all models aliased to `"agent-model"` so the Strands agent doesn't need provider-aware logic.

- **`backend/tools.py`** — `get_pokedex_entry(pokemon)` is a local Strands tool decorated with `@tool`. The agent calls it directly in the backend (no MCP involved); it fetches types, stats, abilities, habitat, and Spanish flavor text from the PokeAPI (`https://pokeapi.co/api/v2/pokemon/{pokemon}`).

- **`backend/agent.py`** — `build_agent(provider_name)` creates a LiteLLMModel and MCPClient, then **monkey-patches `litellm.completion` and `litellm.acompletion` globally** to route through the Router. This is how fallback is achieved without modifying the Strands SDK. `create_agent()` wraps model + MCP tools + local tools in a Strands Agent.

- **`backend/main.py`** — FastAPI app with lifespan validation. The `/api/agent/run` POST endpoint streams SSE events: `start`, `tool_call`, `tool_result`, `text`, `done`, `error`. Static files (index.html) are mounted at `/`.

The frontend (`backend/static/index.html`) is a single self-contained HTML file using Tailwind CSS + vanilla JS. It displays a split layout: chat (60%) + tool trace (40%). The JS reads the SSE stream and updates both panels in real time.

## Key Design Decisions

**Monkey-patching litellm**: The Router fallback works by globally replacing `litellm.completion`/`litellm.acompletion` at agent-build time. This is correct for a single-worker server but would break under concurrent multi-worker load, since the patch is global.

**MCP client lifecycle**: A new MCPClient and agent are instantiated per request (`build_agent` is called inside `_sse_generator`). The synchronous `list_tools_sync()` call adds ~100–300 ms latency per request.

**SSE event schema**: Each event is `data: <json>\n\n`. The `type` field distinguishes `tool_call`, `tool_result`, `text`, `done`, `error`. Tool cards in the trace panel are keyed by `tool_id` to match calls with their results.
