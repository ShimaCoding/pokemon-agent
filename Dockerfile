# ── frontend builder ──────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app

# Install deps as a separate layer for better cache reuse
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm ci

# Copy source and build; vite.config.ts outputs to ../backend/static
COPY frontend/ ./frontend/
RUN mkdir -p backend/static && cd frontend && npm run build

# ── builder ───────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Bring in uv — no pip needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies into an isolated virtual environment
COPY requirements.txt .
RUN uv venv .venv && \
    uv pip install --python .venv/bin/python -r requirements.txt

# ── runtime ───────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Create a dedicated non-root user/group (UID/GID 1001)
RUN addgroup --system --gid 1001 appgroup && \
    adduser  --system --uid 1001 --gid 1001 --no-create-home appuser

# Copy only the venv and app code from builder
COPY --from=builder --chown=appuser:appgroup /app/.venv ./.venv
COPY --chown=appuser:appgroup backend/ ./backend/

# Overwrite backend/static with the freshly compiled React app
COPY --from=frontend-builder --chown=appuser:appgroup /app/backend/static ./backend/static/
# Restore admin.html (not part of the React build, gets wiped by the line above)
COPY --chown=appuser:appgroup backend/static/admin.html ./backend/static/admin.html

# Pre-create data dir so the volume mount is writable by appuser
RUN mkdir -p /app/data && chown appuser:appgroup /app/data

# Drop root privileges
USER appuser

EXPOSE 8000

# Health-check using the built-in /health endpoint (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Single worker — required because litellm is monkey-patched globally (see agent.py)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
