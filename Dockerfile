FROM python:3.11-slim

# Prevent .pyc files, force stdout/stderr flush, disable pip noise
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create a dedicated non-root user/group (UID/GID 1001)
RUN addgroup --system --gid 1001 appgroup && \
    adduser  --system --uid 1001 --gid 1001 --no-create-home appuser

# Install Python dependencies first — layer is cached until requirements.txt changes
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application code with correct ownership
COPY --chown=appuser:appgroup backend/ ./backend/

# Drop root privileges
USER appuser

EXPOSE 8000

# Health-check using the built-in /health endpoint (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Single worker — required because litellm is monkey-patched globally (see agent.py)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
