# Pokémon MCP Agent

A production-ready AI agent sandbox that connects to the live Pokémon MCP server at `mcpokedex.com/mcp` and lets you query Pokémon data through a conversational web interface. The agent uses the Strands Agents SDK, routes LLM calls through a LiteLLM Router with automatic provider fallback (Groq → Gemini → OpenAI), and streams every tool call and its result back to the browser in real time via Server-Sent Events.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- At least one API key from the providers below

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> pokemon-agent && cd pokemon-agent

# 2. Copy the example environment file
cp .env.example .env

# 3. Fill in at least one API key in .env
$EDITOR .env

# 4. Build and start the container
docker-compose up --build
```

Then open **http://localhost:8000** in your browser.

---

## API Keys

| Provider | Free tier | Get your key |
|----------|-----------|--------------|
| **Groq** | Yes — generous free tier | https://console.groq.com/keys |
| **Gemini** | Yes — Google AI Studio free quota | https://aistudio.google.com/app/apikey |
| **OpenAI** | No — pay-as-you-go | https://platform.openai.com/api-keys |

You only need **one** key to run the agent. If multiple keys are set, the LiteLLM Router automatically falls back to the next available provider on failure.

---

## How the Tool Trace Works

When you ask a question, the agent may call one or more tools against the live Pokémon database (e.g. `get_pokemon_info`, `analyze_pokemon_stats`). Each tool call is forwarded through the backend to the MCP server at `mcpokedex.com/mcp`; results come back and are fed into the next model turn.

The right-hand panel shows every tool invocation in real time:
- The card appears immediately when the tool is called, with a spinner.
- The spinner is replaced by the raw result as soon as it comes back.
- Each card shows the tool name, the arguments it was called with, the response, and the time elapsed since the query started.

All MCP calls are made **server-side** — your browser never talks directly to `mcpokedex.com`.

---

## Known Limitations (MVP)

- No authentication — do not expose port 8000 to the public internet without adding auth first.
- No rate limiting — a single user can spam requests; add `slowapi` before production.
- No session storage — conversation history resets on page refresh.
- The LiteLLM Router is monkey-patched into `litellm.completion`; this works for single-worker deployments but may cause issues under concurrent load with multiple workers. Use `--workers 1` (the default) or refactor to per-request router instances.
- Streaming from the MCP server is synchronous (`list_tools_sync`) and may add ~100–300 ms latency per request for the initial connection.
