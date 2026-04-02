.PHONY: help dev frontend backend build start stop clean

help:
	@echo "Pokemon Agent - Available commands:"
	@echo ""
	@echo "Development:"
	@echo "  make dev              - Start frontend (hot reload) + backend (--reload)"
	@echo "  make frontend         - Start frontend dev server on localhost:5173"
	@echo "  make backend          - Start backend server on localhost:8000 with auto-reload"
	@echo ""
	@echo "Build & Production:"
	@echo "  make build            - Build frontend (TypeScript + Vite → /backend/static/)"
	@echo "  make start            - Start backend serving production build (localhost:8000)"
	@echo ""
	@echo "Utilities:"
	@echo "  make docker           - Start Docker (colima start)"
	@echo "  make clean            - Clean build artifacts and cache"
	@echo ""

# Development: Frontend + Backend (requires 2 terminal windows or tmux)
dev:
	@echo "Starting development environment..."
	@echo "Frontend (with hot reload): http://localhost:5173"
	@echo "Backend API: http://localhost:8000"
	@echo ""
	@echo "Run these commands in separate terminals:"
	@echo "  make frontend"
	@echo "  make backend"

# Frontend dev server with hot reload
frontend:
	cd frontend && npm run dev

# Backend with auto-reload
backend:
	python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Build production frontend
build:
	cd frontend && npm run build
	@echo "✓ Frontend built to /backend/static/"

# Start production server (after npm run build)
start:
	python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Docker
docker:
	colima start

# Clean
clean:
	cd frontend && rm -rf dist node_modules/.vite
	rm -rf backend/static/assets
	@echo "✓ Build artifacts cleaned"
