.PHONY: help setup dev frontend backend build start docker clean

PORT ?= 8000
# Run through uv against this checkout's .venv, so nothing depends on an activated
# virtualenv (and an inherited VIRTUAL_ENV from elsewhere is ignored).
PYTHON_RUN = uv run --no-project --python .venv/bin/python

help:
	@echo "Pokemon Agent - Available commands:"
	@echo ""
	@echo "Setup:"
	@echo "  make setup            - Create .venv (Python 3.11) and install backend + frontend deps"
	@echo ""
	@echo "Development:"
	@echo "  make dev              - Start frontend (hot reload) + backend (--reload)"
	@echo "  make frontend         - Start frontend dev server on localhost:5173"
	@echo "  make backend          - Start backend server on localhost:$(PORT) with auto-reload"
	@echo ""
	@echo "Build & Production:"
	@echo "  make build            - Build frontend (TypeScript + Vite → /backend/static/)"
	@echo "  make start            - Start backend serving production build (localhost:$(PORT))"
	@echo ""
	@echo "Utilities:"
	@echo "  make docker           - Start Docker (colima start)"
	@echo "  make clean            - Clean build artifacts and cache"
	@echo ""
	@echo "Override the backend port with PORT=<n>, e.g. make backend PORT=8010"

# Same as the Conductor setup script
setup:
	uv venv --python 3.11 --allow-existing
	uv pip install --python .venv/bin/python -r requirements.txt
	npm ci --prefix frontend

# Development: Frontend + Backend (requires 2 terminal windows or tmux)
dev:
	@echo "Starting development environment..."
	@echo "Frontend (with hot reload): http://localhost:5173"
	@echo "Backend API: http://localhost:$(PORT)"
	@echo ""
	@echo "Run these commands in separate terminals:"
	@echo "  make frontend"
	@echo "  make backend"

# Frontend dev server with hot reload
frontend:
	cd frontend && npm run dev

# Backend with auto-reload
backend:
	$(PYTHON_RUN) uvicorn backend.main:app --reload --host 0.0.0.0 --port $(PORT)

# Build production frontend
build:
	cd frontend && npm run build
	@echo "✓ Frontend built to /backend/static/"

# Start production server (after npm run build)
start:
	$(PYTHON_RUN) uvicorn backend.main:app --host 0.0.0.0 --port $(PORT)

# Docker
docker:
	colima start

# Clean
clean:
	cd frontend && rm -rf dist node_modules/.vite
	rm -rf backend/static/assets
	@echo "✓ Build artifacts cleaned"
