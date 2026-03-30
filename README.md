# Pokémon MCP Agent

**Sandbox educativo para entender flujos agénticos de principio a fin.**

Un agente de IA que responde preguntas sobre Pokémon conectándose al servidor MCP en vivo de `mcpokedex.com/mcp`. Cada paso del flujo — qué herramienta se invocó, con qué argumentos, qué devolvió y cuánto tardó — se muestra al usuario en tiempo real. Nada es una caja negra.

El backend es **FastAPI + Strands Agents SDK** (Python). El frontend es una **SPA en React** con Zustand, CSS Modules y Vite, que consume el stream SSE del backend y actualiza la UI de forma reactiva.

**Demo:** https://pokemon-agent.cubepath.app · **Repo:** https://github.com/ShimaCoding/pokemon-agent

---

## Capturas de pantalla

| Herramientas MCP descubiertas dinámicamente | Respuesta con narración Dexter |
|:---:|:---:|
| ![MCP Tools panel](Screenshot%202026-03-29%20at%2011.56.13%E2%80%AFPM.png) | ![Dexter narration](Screenshot%202026-03-29%20at%2011.50.58%E2%80%AFPM.png) |

| Trazas del LLM Router con fallback | Admin dashboard |
|:---:|:---:|
| ![Router fallback traces](Screenshot%202026-03-29%20at%2011.55.30%E2%80%AFPM.png) | ![Admin dashboard](Screenshot%202026-03-29%20at%2011.57.25%E2%80%AFPM.png) |

---

## Conceptos agénticos que ilustra el proyecto

| Concepto | Descripción |
|----------|-------------|
| **Tool use & selección dinámica** | El modelo decide en cada turno qué herramienta(s) llamar según la intención del usuario, sin lógica hardcodeada. |
| **Protocolo MCP** | Integración con un servidor MCP real. El agente llama a `list_tools_sync()` en cada request para descubrir capacidades en tiempo de ejecución. |
| **Streaming agéntico con SSE** | El frontend consume un flujo `start → tool_call → tool_result → text → done`, mostrando el razonamiento antes de que la respuesta final esté lista. |
| **Fallback multi-proveedor** | LiteLLM Router con monkey-patch: Groq → Gemini → OpenAI de forma transparente, sin modificar el SDK de Strands. |
| **Panel de trazas en vivo** | Cada tool call aparece con spinner → resultado crudo + tiempo transcurrido. Didáctico para entender el ciclo de vida agéntico. |
| **Dexter narration skill** | Skill de narración que convierte las respuestas del agente en el estilo formal y sarcástico de la Pokédex del Dr. Dexter. |
| **Admin dashboard** | Panel en `/admin` que registra cada consulta en SQLite: modelos intentados, tokens, latencia, tools usadas y estado del fallback. |

---

## Quick Start

### Con Docker (recomendado)

```bash
# 1. Clonar el repositorio
git clone https://github.com/ShimaCoding/pokemon-agent && cd pokemon-agent

# 2. Copiar el fichero de variables de entorno
cp .env.example .env

# 3. Añadir al menos una API key en .env
$EDITOR .env

# 4. Construir e iniciar
docker-compose up --build
```

Abre **http://localhost:8000** en el navegador.

### Desarrollo local

```bash
# Backend
python -m uvicorn backend.main:app --reload

# Frontend (en otra terminal)
cd frontend && npm install && npm run dev
```

---

## API Keys

Solo necesitas **una** para que el agente funcione. El Router hace fallback automático al siguiente proveedor disponible.

| Proveedor | Tier gratuito | Obtener key |
|-----------|---------------|-------------|
| **Groq** | Sí — muy generoso | https://console.groq.com/keys |
| **Gemini** | Sí — cuota de Google AI Studio | https://aistudio.google.com/app/apikey |
| **OpenAI** | No — pay-as-you-go | https://platform.openai.com/api-keys |

---

## Arquitectura

```
browser ──SSE──► FastAPI (backend/main.py)
                    │
                    ├── Strands Agent (backend/agent.py)
                    │       ├── LiteLLMModel  ← monkey-patched sobre LiteLLM Router
                    │       └── MCPClient     → mcpokedex.com/mcp
                    │
                    └── SQLite (admin dashboard en /admin)
```

**Módulos del backend:**

- `backend/providers.py` — diccionario `PROVIDERS` con IDs de modelo y vars de entorno; construye el LiteLLM Router con orden de fallback.
- `backend/agent.py` — `build_agent()` crea el modelo, el MCPClient y parchea `litellm.completion`/`litellm.acompletion` globalmente para enrutar a través del Router.
- `backend/main.py` — app FastAPI con lifespan validation. El endpoint `/api/agent/run` hace streaming SSE: `start`, `tool_call`, `tool_result`, `text`, `done`, `error`.

**Frontend React (SPA):**

- `frontend/src/` — componentes con CSS Modules, Zustand para estado global, Vite como bundler.
- `IntroModal` — modal de bienvenida con ejemplos de queries y descripción de herramientas disponibles.
- Panel izquierdo: chat. Panel derecho: trazas de tool calls en tiempo real con vista Pretty/Raw y JSON viewer.
- `TitleBar` con selector de skill (Dexter narration toggle).

---

## Cómo funciona el panel de trazas

Cuando haces una pregunta, el agente puede invocar una o más herramientas contra el servidor MCP. Cada invocación se reenvía al servidor MCP en `mcpokedex.com/mcp`; los resultados se devuelven y se alimentan al siguiente turno del modelo.

El panel derecho muestra cada invocación en tiempo real:
- La tarjeta aparece inmediatamente con un spinner.
- El spinner se reemplaza por el resultado en cuanto llega.
- Cada tarjeta muestra: nombre de la herramienta, argumentos, respuesta y tiempo transcurrido.

Todas las llamadas MCP son **server-side** — el navegador nunca habla directamente con `mcpokedex.com`.

---

## Admin Dashboard

Disponible en `/admin`. Registra en SQLite cada consulta con:
- Modelo usado (y cuál fue el fallback)
- Tokens consumidos
- Latencia total
- Herramientas invocadas
- Estado de la respuesta

Útil para observar en tiempo real cómo el Router elige y hace fallback entre Groq, Gemini y OpenAI.

---

## Limitaciones conocidas (MVP)

- Sin autenticación — no exponer el puerto 8000 a internet sin añadir auth.
- Sin rate limiting — añadir `slowapi` antes de producción.
- Sin historial de sesión — la conversación se reinicia al refrescar la página.
- El monkey-patch de LiteLLM Router funciona para deployments de un solo worker; usar `--workers 1` (por defecto en Docker) o refactorizar a instancias por request para concurrencia real.
- `list_tools_sync()` es sincrónico y añade ~100–300 ms de latencia por request en la conexión inicial al MCP.

---

## Despliegue en CubePath

El proyecto está dockerizado con `Dockerfile` y `docker-compose.yml` listos para producción:

1. **Servidor**: imagen Docker de FastAPI + Uvicorn desplegada en un servidor nano de CubePath, exponiendo el puerto 8000.
2. **Variables de entorno**: las API keys se configuran como env vars en el panel de CubePath — sin subir el `.env`.
3. **Build automático**: CubePath construye la imagen desde el `Dockerfile` del repositorio público en cada despliegue.
4. **Dominio HTTPS**: CubePath asigna un dominio con TLS automático, sin configurar proxy inverso manualmente.
