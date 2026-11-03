.PHONY: up down db migrate api worker web test lint

up: ; docker compose up --build
down: ; docker compose down
db: ; docker compose up -d db
migrate: ; cd backend && uv run alembic upgrade head
api: ; cd backend && uv run uvicorn app.main:app --reload --port 8000
worker: ; cd backend && uv run python -m app.worker.main
web: ; cd frontend && npm run dev
test: ; cd backend && uv run pytest && cd ../frontend && npm test
lint: ; cd backend && uv run ruff check . && uv run mypy app && cd ../frontend && npm run lint

backup:
	./scripts/db_backup.sh

restore-check:
	./scripts/db_restore_check.sh
