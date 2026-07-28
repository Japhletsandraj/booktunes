# Booktunes backend. All targets assume you are in backend/ with the venv active.

.DEFAULT_GOAL := help
.PHONY: help install dev run worker lint fmt test cov migrate revision seed \
        reindex up down logs build clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- setup ------------------------------------------------------------------

install:  ## Install runtime deps (CPU-only torch wheels)
	pip install --upgrade pip
	pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

dev: install  ## Install runtime + dev deps
	pip install -r requirements-dev.txt

# --- run --------------------------------------------------------------------

run:  ## Start the API with reload
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:  ## Start a Celery worker with embedded beat
	celery -A app.core.celery_app worker --beat --loglevel=info --concurrency=1

# --- quality ----------------------------------------------------------------

lint:  ## Lint app, scripts and tests
	ruff check app scripts tests

fmt:  ## Auto-fix and format
	ruff check --fix app scripts tests
	ruff format app scripts tests

test:  ## Run the test suite
	pytest -q

cov:  ## Run tests with coverage
	pytest -q --cov=app --cov-report=term-missing

# --- database ---------------------------------------------------------------

migrate:  ## Apply migrations
	alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add table"
	alembic revision --autogenerate -m "$(m)"

seed:  ## Seed the book catalogue
	python -m scripts.seed_books

reindex:  ## Rebuild the pgvector index
	python -m scripts.rebuild_vector_index

# --- docker -----------------------------------------------------------------

up:  ## Start the local stack (db + redis + api)
	docker compose up -d --build

down:  ## Stop the local stack
	docker compose down

logs:  ## Tail the api container
	docker compose logs -f api

build:  ## Build the production image
	docker build -t booktunes-api .

# --- misc -------------------------------------------------------------------

clean:  ## Remove caches and bytecode
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
