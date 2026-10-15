.PHONY: install test lint up down

install:
	cd backend && uv sync --extra dev
	cd frontend && npm ci

test:
	cd backend && uv run pytest
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
	cd frontend && npm run typecheck

up:
	docker compose up --build -d

down:
	docker compose down
