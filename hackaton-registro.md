# Proyecto: Pokémon MCP Agent

### 📛 Nombre del proyecto

Pokémon MCP Agent

---

### 📝 Descripción del proyecto

**Sandbox educativo para entender flujos agénticos de principio a fin.** El proyecto nació con una intención clara: que cualquier desarrollador pueda ver, de forma tangible, cómo un agente de IA toma decisiones, selecciona herramientas y encadena llamadas para resolver una consulta real. El dominio Pokémon sirve de excusa perfecta — todos lo conocen — pero el verdadero valor está en lo que ocurre entre bastidores.

El agente se conecta a un servidor MCP en vivo (`mcpokedex.com/mcp`) y expone cada paso del flujo al usuario en tiempo real: qué herramienta se invocó, con qué argumentos, qué devolvió y cuánto tardó. Nada es una caja negra.

El backend es FastAPI + Strands Agents SDK (Python). El frontend es una SPA en React con Zustand, CSS Modules y Vite, que consume el stream SSE del backend y actualiza la UI de forma reactiva.

**Conceptos agénticos que el proyecto ilustra:**

- **Tool use & selección dinámica**: el modelo decide en cada turno qué herramienta(s) llamar según la intención del usuario, sin lógica hardcodeada.
- **Protocolo MCP (Model Context Protocol)**: integración con un servidor MCP real como fuente de datos externa, con `list_tools_sync` para descubrir capacidades en tiempo de ejecución.
- **Descubrimiento dinámico de herramientas MCP**: el agente llama a `list_tools_sync()` en cada request para obtener las capacidades disponibles del servidor MCP en tiempo de ejecución, sin hardcodear ninguna herramienta.
- **Streaming agéntico con SSE**: el frontend consume un flujo de eventos `start → tool_call → tool_result → text → done`, mostrando el razonamiento antes de que la respuesta final esté lista.
- **Fallback multi-proveedor con LiteLLM Router**: Groq → Gemini → OpenAI de forma transparente, con monkey-patch sobre `litellm.completion` para no modificar el SDK de Strands.
- **Panel de trazas en vivo**: visualización didáctica de cada tool call con spinner → resultado crudo + tiempo transcurrido, pensada para que el aprendiz entienda el ciclo de vida completo de una llamada agéntica.
- **Panel administrativo**: dashboard en `/admin` que registra cada consulta en SQLite con los modelos intentados, tokens consumidos, latencia, tools usadas y estado — permite observar en tiempo real cómo el Router elige y hace fallback entre proveedores (Groq, Gemini, OpenAI).

---

### 🔗 URL de la demo (desplegada en CubePath)

<!-- ⚠️ REEMPLAZA con tu URL real de CubePath -->
https://pokemon-agent.cubepath.app

---

### 📦 URL del repositorio (público)

https://github.com/ShimaCoding/pokemon-agent

---

### 📸 Capturas de pantalla o GIFs

<!-- Arrastra aquí las imágenes de la carpeta del proyecto al crear el issue en GitHub -->

> **Sugerencia:** usa las capturas ya disponibles en el repo:
> - `Screenshot 2026-03-29 at 11.49.51 PM.png` — pantalla de inicio / intro modal
> - `Screenshot 2026-03-29 at 11.52.02 PM.png` — respuesta completa con análisis de Pokémon
> - `Screenshot 2026-03-29 at 11.55.30 PM.png` — trazas del LLM Router con fallback de proveedores
> - `Screenshot 2026-03-29 at 11.57.25 PM.png` — panel admin del MCPokedex Agent

---

### ☁️ ¿Cómo has utilizado CubePath?

El proyecto está dockerizado con `Dockerfile` y `docker-compose.yml` listos para producción. El despliegue en CubePath se realizó de la siguiente manera:

1. **Servidor de aplicación**: la imagen Docker de FastAPI + Uvicorn se despliega en un servidor nano de CubePath, exponiendo el puerto 8000.
2. **Variables de entorno**: las API keys de Groq, Gemini y OpenAI se configuran como variables de entorno en el panel de CubePath, sin necesidad de subir el fichero `.env`.
3. **Build automático**: CubePath construye la imagen desde el `Dockerfile` del repositorio público en cada despliegue.
4. **Dominio público**: CubePath asigna un dominio HTTPS al contenedor, eliminando la necesidad de configurar proxy inverso o certificados TLS manualmente.

Los 15 $ de crédito gratuito fueron más que suficientes para mantener el servidor nano activo durante toda la duración de la hackatón.

---

### 📧 Email de contacto (opcional)

<!-- tu@email.com -->

---

### ✅ Confirmación

- [x] Mi proyecto está desplegado en CubePath y funciona correctamente
- [x] El repositorio es público y contiene un README con la documentación
- [x] He leído y acepto las [reglas de la hackatón](https://github.com/midudev/hackaton-cubepath-2026#-reglas)
